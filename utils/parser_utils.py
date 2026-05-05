import re
from typing import Dict, Any, List
from loguru import logger

# =========================================================
# 🔹 KEYWORDS
# =========================================================
SRT_KEYWORDS = ["srt", "igsrt", "superficial radiation", "surface radiation"]
BIOPSY_KEYWORDS = ["biopsy", "bx"]
EXCISION_KEYWORDS = ["excision"]
MOHS_KEYWORDS = ["mohs"]
DEBRIDEMENT_KEYWORDS = ["debridement", "dbr"]
WOUND_KEYWORDS = ["ulcer", "wound", "subcutaneous", "full thickness", "partial thickness"]
DERM_KEYWORDS = [
    "eczema", "eczematous", "dermatitis",
    "infected skin", "crust", "debris",
    "xerosis", "flaky", "dry skin"
]

class ParserUtils:
    # =========================================================
    # 🔹 NORMALIZE TEXT
    # =========================================================  
    def normalize(self, text: str) -> str:
        return text.lower() if text else ""
    
    # =========================================================
    # 🔹 MOHS LOCATION EXTRACTION (FIXED + FALLBACK) & STAGES
    # =========================================================
    def extract_mohs_location(self, text: str) -> str:

        # -------------------------
        # 🔴 PRIMARY: Explicit "Location:"
        # -------------------------
        match = re.search(r"Location:\s*([^\n\r]+)", text, re.IGNORECASE)
        if match:
            location = match.group(1).strip()
            logger.info(f"📍 Mohs location detected (explicit): {location}")
            return location

        # -------------------------
        # 🔴 SECONDARY: complaint-style pattern
        # e.g. "- Location: Left Temple"
        # -------------------------
        match = re.search(r"-\s*Location:\s*([^\n\r]+)", text, re.IGNORECASE)
        if match:
            location = match.group(1).strip()
            logger.info(f"📍 Mohs location detected (bullet): {location}")
            return location

        # -------------------------
        # 🔴 FALLBACK: keyword inference
        # -------------------------
        logger.warning("⚠️ Primary location not found → fallback detection")

        fallback_match = re.search(
            r"(temple|face|nose|lip|ear|scalp|neck|hand|foot|genital)",
            text.lower()
        )

        if fallback_match:
            location = fallback_match.group(1)
            logger.info(f"📍 Mohs location inferred (fallback): {location}")
            return location

        logger.error("❌ Mohs location could not be determined")
        return ""


    def extract_mohs_stages(self, text: str) -> int:

        # -------------------------
        # 🔴 Pattern: "1st Stage", "2nd Stage"
        # -------------------------
        matches = re.findall(
            r"(\d+)(?:st|nd|rd|th)?\s*Stage",
            text,
            re.IGNORECASE
        )

        if matches:
            stages = max(map(int, matches))
            logger.info(f"🔢 Mohs stages detected (explicit): {stages}")
            return stages

        # -------------------------
        # 🔴 Pattern: multiple "Stage:" mentions
        # -------------------------
        stage_mentions = len(re.findall(r"Stage:", text, re.IGNORECASE))
        if stage_mentions > 0:
            logger.info(f"🔢 Mohs stages inferred (count): {stage_mentions}")
            return stage_mentions

        # -------------------------
        # 🔴 DEFAULT
        # -------------------------
        logger.info("🔢 No stage explicitly found → default = 1")
        return 1
    
    # =========================================================
    # 🔹 EXTRACT ENERGY(kV) FOR SRT/IGSTR
    # =========================================================
    def extract_kv(self, text: str) -> int | None:
        """
        Robust kV extraction for SRT/IGSRT notes
        """

        if not text:
            return None

        text = text.lower()

        patterns = [
            # 🔴 MOST RELIABLE (labeled formats)
            r"energy\s*\(\s*k\s*v\s*\)\s*[:\-]?\s*(\d+)",
            r"energy\s*k\s*v\s*[:\-]?\s*(\d+)",

            # 🔴 labeled but reversed
            r"energy\s*[:\-]?\s*(\d+)\s*k\s*v",

            # 🔴 generic inline
            r"\b(\d{2,3})\s*k\s*v\b",
            r"\b(\d{2,3})\s*kv\b",

            # 🔴 orthovoltage context
            r"orthovoltage.*?(\d{2,3})\s*k\s*v",
        ]

        matches = []

        for pattern in patterns:
            found = re.findall(pattern, text)
            for m in found:
                try:
                    val = int(m)

                    # 🔴 VALID RANGE FILTER (CRITICAL)
                    if 10 <= val <= 500:
                        matches.append(val)

                except:
                    continue

        if not matches:
            logger.warning("⚠️ No kV detected")
            return None

        # 🔴 STRATEGY: choose most frequent OR last occurrence
        # (radiation plans often repeat final value at end)
        kv = matches[-1]

        logger.info(f"⚡ kV extracted → candidates={matches} | selected={kv}")

        return kv

    # =========================================================
    # DETECT ULTRASOUND IMAGES
    # =========================================================
    def detect_images(self, note: Dict[str, Any]) -> bool:
        image_fields = ["images", "attachments", "media", "ultrasoundImages"]

        for field in image_fields:
            val = note.get(field)
            if isinstance(val, list) and len(val) > 0:
                logger.info(f"🖼️ Images detected via field: {field}")
                return True

        logger.warning("⚠️ No real images found")
        return False
    
    # =========================================================
    # 🔹 LESION COUNT FOR EXCISION SECTION
    # =========================================================
    def extract_lesion_count(self, text: str) -> int:
        text_lower = text.lower()

        matches = re.findall(r'(\d+)\s*(?:st|nd|rd|th)?\s*lesion', text_lower)
        if matches:
            count = max(map(int, matches))
            logger.info(f"🔢 Detected lesion count (numeric): {count}")
            return count

        if "second lesion" in text_lower:
            return 2
        if "third lesion" in text_lower:
            return 3

        if "lesions" in text_lower and "lesion" in text_lower:
            logger.info("🔢 Multiple lesions detected → default 2")
            return 2

        return 1

    # =========================================================
    # 🔹 KEYWORD DETECTION
    # =========================================================
    def detect_keyword(self, text: str, keywords: List[str]) -> bool:
        text = self.normalize(text)
        return any(k in text for k in keywords)
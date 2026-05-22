# utils/parser_utils.py

import re
from typing import Dict, Any, List
from loguru import logger

# =========================================================
# 🔹 KEYWORDS
# =========================================================
WOUND_KEYWORDS = ["ulcer", "wound", "subcutaneous", "full thickness", "partial thickness"]
SRT_KEYWORDS = ["srt", "igsrt", "superficial radiation", "surface radiation"]
DEBRIDEMENT_KEYWORDS = ["debridement", "dbr"]
BIOPSY_KEYWORDS = ["biopsy", "bx"]
EXCISION_KEYWORDS = ["excision"]
MOHS_KEYWORDS = ["mohs"]
DERM_KEYWORDS = [
    "eczema", "eczematous", "dermatitis",
    "infected skin", "crust", "debris",
    "xerosis", "flaky", "dry skin"
]

SHAVE_FACE_KEYWORDS = [
    "face", "ear", "ears", "eyelid", "eyelids",
    "nose", "lip", "lips", "mucous membrane"
]

SHAVE_SPECIAL_KEYWORDS = [
    "scalp", "neck", "hand", "hands",
    "foot", "feet", "genitalia"
]

XTRAC_KEYWORDS = [
    "xtrac",
    "xtrac laser treatment",
    "xtrac therapy"
]

IPL_KEYWORDS = [
    "intense pulsed light",
    "ipl"
]

IPL_METHOD_MAP = {

    "hair reduction": [
        "hair reduction",
        "hair removal"
    ],

    "tattoo removal": [
        "tattoo"
    ],

    "vein treatment": [
        "vein"
    ],

    "spider veins treatment": [
        "spider vein",
        "spider veins"
    ],

    "skin rejuvenation": [
        "skin rejuvenation",
        "rejuvenation"
    ],

    "photorejuvenation treatment": [
        "photorejuvenation"
    ],

    "rosacea treatment": [
        "rosacea"
    ],

    "melasma treatment": [
        "melasma"
    ],

    "acne treatment": [
        "acne"
    ],

    "birthmark": [
        "birthmark"
    ],

    "photofacial": [
        "photofacial"
    ],

    "photorejuvenation treatment": [
    "pigmented spots",
    "lentigo",
    "sun spots",
    "photoaging",
    "photodamage",
    "pigmentation"
    ]
}

CHEMICAL_PEEL_KEYWORDS = [
    "chemical peel",
    "chemical peel (peel)",
    "skin medica chemical peel",
    "skin medica peel",
    "illuminize peel",
    "vitalize peel",
    "rejuvenize peel"
]

CHEMICAL_METHOD_MAP = {

    "salicylic acid": [
        "salicylic acid"
    ],

    "glycolic acid": [
        "glycolic",
        "glycolic acid"
    ],

    "lactic acid": [
        "lactic acid"
    ],

    "retinoic acid": [
        "retinoic acid",
    ],

    "trichloroacetic acid": [
        "tca",
        "trichloroacetic"
    ],

    "beta hydroxy acid": [
        "bha",
        "beta hydroxy"
    ],

    "alpha hydroxy acid": [
        "aha",
        "alpha hydroxy"
    ],

    "phenol": [
        "phenol"
    ],

    "jessners": [
        "jessner"
    ]
}

CHEMICAL_CHOICE_MAP = {

    "epidermal": [
        "epidermal"
    ],

    "dermal": [
        "dermal"
    ],

    "facial": [
        "facial",
        "face"
    ],

    "nonfacial": [
        "nonfacial",
        "neck",
        "arm",
        "leg",
        "back",
        "chest",
        "abdomen",
        "hand",
        "foot"
    ]
}

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
    

    # =========================================================
    # 🔹 EXTRACT SIZE FROM X x Y FORMAT
    # =========================================================
    def extract_max_dimension(self, text: str) -> float | None:

        if not text:
            return None

        patterns = [
            r"([\d\.]+)\s*[xX×]\s*([\d\.]+)",
            r"([\d\.]+)\s*cm\s*[xX×]\s*([\d\.]+)\s*cm",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)

            if match:
                try:
                    vals = [
                        float(match.group(1)),
                        float(match.group(2))
                    ]

                    size = max(vals)

                    logger.info(f"📏 Parsed shave size={size}")
                    return size

                except Exception:
                    continue

        # fallback single number
        single = re.search(r"([\d\.]+)\s*cm", text, re.IGNORECASE)

        if single:
            try:
                return float(single.group(1))
            except:
                pass

        return None


    # =========================================================
    # 🔹 SHAVE LOCATION GROUP
    # =========================================================
    def classify_shave_location_group(self, location: str) -> str:

        location = (location or "").lower()

        if any(k in location for k in SHAVE_FACE_KEYWORDS):
            return "face"

        if any(k in location for k in SHAVE_SPECIAL_KEYWORDS):
            return "special"

        return "trunk"
    

    # =========================================================
    # 🔹 NORMALIZE LASER METHOD
    # =========================================================
    def normalize_laser_method(self, text: str) -> str:

        if not text:
            return ""

        text = text.lower().strip()

        text = re.sub(r"laser", "", text)
        text = re.sub(r"treatment", "", text)
        text = re.sub(r"therapy", "", text)

        text = re.sub(r"\s+", " ", text).strip()

        return text
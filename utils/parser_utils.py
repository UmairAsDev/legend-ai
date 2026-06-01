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
    def _clean_mohs_text(self, text: str) -> str:
        return (text or "").replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n").strip()


    def extract_mohs_location(self, text: str) -> str:
        text = self._clean_mohs_text(text)

        # Primary: explicit "Location:"
        match = re.search(r"(?im)^\s*Location:\s*([^\n\r]+)", text)
        if match:
            location = match.group(1).strip()
            logger.info(f"📍 Mohs location detected (explicit): {location}")
            return location

        # Secondary: bullet format
        match = re.search(r"(?im)^\s*-\s*Location:\s*([^\n\r]+)", text)
        if match:
            location = match.group(1).strip()
            logger.info(f"📍 Mohs location detected (bullet): {location}")
            return location

        # Fallback: keyword inference
        logger.warning("⚠️ Primary location not found → fallback detection")

        fallback_match = re.search(
            r"(temple|face|nose|lip|ear|scalp|neck|hand|hands|foot|feet|genital|chest|back|abdomen|arm|leg|shoulder|thigh)",
            text.lower()
        )

        if fallback_match:
            location = fallback_match.group(1)
            logger.info(f"📍 Mohs location inferred (fallback): {location}")
            return location

        logger.error("❌ Mohs location could not be determined")
        return ""


    def extract_mohs_site_blocks(self, text: str) -> List[Dict[str, Any]]:
        """
        Split a Mohs note into site-aware blocks.
        Captures repeated headings such as:
        Site A
        ...
        Site A
        1st Stage: ...
        2nd Stage: ...
        Site B
        ...
        """
        text = self._clean_mohs_text(text)
        if not text:
            return []

        site_pattern = re.compile(r"(?im)^\s*Site\s+([A-Z])\b")
        matches = list(site_pattern.finditer(text))

        blocks = []

        if matches:
            for idx, match in enumerate(matches):
                start = match.start()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
                site_label = match.group(1).upper()
                block_text = text[start:end].strip()

                if block_text:
                    blocks.append({
                        "site_label": site_label,
                        "label": f"site_{site_label}",
                        "text": block_text,
                    })

            logger.info(f"📦 Mohs site blocks detected: {len(blocks)}")
            return blocks

        # Fallback: split by Location if there is no Site marker
        location_pattern = re.compile(r"(?im)^\s*Location:\s*")
        loc_matches = list(location_pattern.finditer(text))

        if loc_matches:
            for idx, match in enumerate(loc_matches):
                start = match.start()
                end = loc_matches[idx + 1].start() if idx + 1 < len(loc_matches) else len(text)
                block_text = text[start:end].strip()

                if block_text:
                    blocks.append({
                        "site_label": str(idx + 1),
                        "label": f"site_{idx + 1}",
                        "text": block_text,
                    })

            logger.info(f"📦 Mohs location blocks detected: {len(blocks)}")
            return blocks

        # Final fallback: one block
        logger.warning("⚠️ No Mohs site markers found → single-block fallback")
        return [{
            "site_label": "1",
            "label": "site_1",
            "text": text
        }]


    def extract_mohs_stage_details(self, text: str) -> List[Dict[str, Any]]:
        """
        Returns structured stage rows:
        1st Stage: 2 Sections, Positive
        2nd Stage: 1 Sections, Negative
        """
        text = self._clean_mohs_text(text)
        if not text:
            return []

        stage_pattern = re.compile(
            r"(?im)^\s*(\d+)(?:st|nd|rd|th)?\s*Stage\s*:\s*([^\n\r]*)"
        )

        details = []

        for match in stage_pattern.finditer(text):
            stage_num = int(match.group(1))
            remainder = (match.group(2) or "").strip()

            sections = None
            sec_match = re.search(r"(\d+)\s*Sections?\b", remainder, re.IGNORECASE)
            if sec_match:
                try:
                    sections = int(sec_match.group(1))
                except Exception:
                    sections = None

            status = None
            if re.search(r"\bpositive\b", remainder, re.IGNORECASE):
                status = "positive"
            elif re.search(r"\bnegative\b", remainder, re.IGNORECASE):
                status = "negative"

            details.append({
                "stage": stage_num,
                "sections": sections,
                "status": status,
                "raw": match.group(0).strip(),
            })

        if details:
            logger.info(f"🔢 Mohs stage details detected: {details}")
            return details

        # fallback: count stage lines
        stage_lines = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"\bStage\b", line, re.IGNORECASE)
        ]

        if stage_lines:
            logger.info(f"🔢 Mohs stage lines inferred: {len(stage_lines)}")
            return [
                {
                    "stage": i + 1,
                    "sections": None,
                    "status": None,
                    "raw": line
                }
                for i, line in enumerate(stage_lines)
            ]

        logger.info("🔢 No stage explicitly found → default = 1")
        return []


    def extract_mohs_stages(self, text: str) -> int:
        details = self.extract_mohs_stage_details(text)

        if details:
            stages = len(details)
            logger.info(f"🔢 Mohs stages detected: {stages}")
            return stages

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
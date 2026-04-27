import re
from typing import Dict, Any, List
from loguru import logger


BIOPSY_KEYWORDS = ["biopsy", "bx"]
EXCISION_KEYWORDS = ["excision"]
MOHS_KEYWORDS = ["mohs"]


class ClinicalParser:

    def _normalize(self, text: str) -> str:
        return text.lower() if text else ""

    # =========================================================
    # 🔹 BIOPSY EXTRACTION
    # =========================================================
    def extract_biopsy_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        pattern = r"([A-Z])\.\s*Biopsy.*?(?=(?:\n[A-Z]\.\s*Biopsy|$))"
        matches = list(re.finditer(pattern, text, re.IGNORECASE | re.DOTALL))

        results = []
        for match in matches:
            logger.info(f"🔍 Processing biopsy section: {match.group(1)}")

            results.append({
                "label": match.group(1),
                "text": match.group(0).strip(),
                "quantity": 1
            })

        logger.info(f"📊 Total biopsy sections: {len(results)}")
        return results

    # =========================================================
    # 🔹 LESION COUNT
    # =========================================================
    def _extract_lesion_count(self, text: str) -> int:
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
    # 🔹 EXCISION EXTRACTION
    # =========================================================
    def extract_excision_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        pattern = r"([A-Z])\.\s*Excision.*?(?=(?:\n[A-Z]\.|$))"
        matches = list(re.finditer(pattern, text, re.IGNORECASE | re.DOTALL))

        results = []

        for match in matches:
            section_text = match.group(0)
            label = match.group(1)

            logger.info(f"🔍 Processing excision section: {label}")

            size = None

            # PRIORITY 1: Excision Size
            size_match = re.search(
                r"Excision Size.*?:\s*([\d\.]+)\s*[x\-]\s*([\d\.]+)",
                section_text, re.IGNORECASE
            )

            if size_match:
                size = max(float(size_match.group(1)), float(size_match.group(2)))
                logger.info(f"✅ Using Excision Size: {size}")

            # PRIORITY 2: Wound Size
            if not size:
                wound_match = re.search(
                    r"wound size.*?:?\s*([\d\.]+)\s*[x\-]?\s*([\d\.]+)?",
                    section_text, re.IGNORECASE
                )
                if wound_match:
                    values = [v for v in wound_match.groups() if v]
                    size = max(map(float, values))
                    logger.info(f"✅ Using Wound Size: {size}")

            # PRIORITY 3: Final Closure Size
            if not size:
                closure_match = re.search(
                    r"final closure size.*?:?\s*([\d\.]+)",
                    section_text, re.IGNORECASE
                )
                if closure_match:
                    size = float(closure_match.group(1))
                    logger.info(f"✅ Using Final Closure Size: {size}")

            if not size:
                logger.warning("⚠️ No valid excision size → SKIPPED")

            lesion_count = self._extract_lesion_count(section_text)

            cleaned_text = re.sub(
                r"Repair:.*", "",
                section_text,
                flags=re.IGNORECASE | re.DOTALL
            )

            results.append({
                "label": label,
                "text": cleaned_text.strip(),
                "size": size,
                "quantity": lesion_count
            })

        logger.info(f"📊 Total excision sections: {len(results)}")
        return results

    # =========================================================
    # 🔹 MOHS EXTRACTION (NEW - CRITICAL FIX)
    # =========================================================
    def extract_mohs_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        logger.info("🔍 Extracting Mohs sections (multi-site mode)...")

        sections = []

        # 🔴 Split by multiple "Location:"
        parts = re.split(r"(?=Location:\s*)", text, flags=re.IGNORECASE)

        for i, part in enumerate(parts):
            part = part.strip()

            if not part or "Location:" not in part:
                continue

            logger.info(f"🔍 Processing Mohs segment {i+1}")

            location = self._extract_mohs_location(part)
            stages = self._extract_mohs_stages(part)

            sections.append({
                "label": f"site_{i+1}",
                "text": part,
                "location": location,
                "stages": stages
            })

        logger.info(f"📊 Total Mohs sections: {len(sections)}")
        return sections


    # =========================================================
    # 🔹 MOHS LOCATION EXTRACTION (FIXED + FALLBACK)
    # =========================================================
    def _extract_mohs_location(self, text: str) -> str:

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


    # =========================================================
    # 🔹 MOHS STAGE EXTRACTION (IMPROVED)
    # =========================================================
    def _extract_mohs_stages(self, text: str) -> int:

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
    # 🔹 CLOSURE EXTRACTION
    # =========================================================
    def extract_closure_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        logger.info("🔍 Extracting closure sections...")

        sections = []

        # 🔴 Detect closure blocks
        pattern = r"(Repair:\s*.*?Closure.*?)(?=(?:\n[A-Z]\.|$))"

        matches = re.finditer(pattern, text, re.IGNORECASE | re.DOTALL)

        for i, match in enumerate(matches):
            block = match.group(1)

            logger.info(f"🔍 Processing closure block {i+1}")

            # -------------------------
            # TYPE
            # -------------------------
            if "complex" in block.lower():
                ctype = "complex"
            elif "intermediate" in block.lower() or "layered" in block.lower():
                ctype = "intermediate"

            elif "deficit size" in block.lower():
                logger.info("⚠️ Skipping deficit size (not closure)")
                continue
            else:
                logger.info("⚠️ Simple closure detected → skipping")
                continue  # ignore simple closures

            # -------------------------
            # 🔴 SIZE EXTRACTION (ROBUST)
            # -------------------------
            size = None

            patterns = [
                r"final closure size.*?:?\s*([\d\.]+)",
                r"closure size.*?:?\s*([\d\.]+)",
                r"closure length.*?:?\s*([\d\.]+)",
                r"length of closure.*?:?\s*([\d\.]+)",
                r"final closure length.*?:?\s*([\d\.]+)",
                r"final size.*?:?\s*([\d\.]+)",
                r"measuring\s*([\d\.]+)\s*cm",
            ]

            for p in patterns:
                match = re.search(p, block, re.IGNORECASE)
                if match:
                    size = float(match.group(1))
                    logger.info(f"📏 Closure size detected via pattern '{p}': {size}")
                    break

            if not size:
                logger.warning("⚠️ Closure size not found")

            # -------------------------
            # LOCATION (fallback)
            # -------------------------
            loc_match = re.search(r"Location:\s*(.*)", text)
            location = loc_match.group(1).strip() if loc_match else ""

            sections.append({
                "type": ctype,
                "size": size,
                "location": location,
                "text": block.strip()
            })

        logger.info(f"📊 Total closure sections: {len(sections)}")
        return sections

    # =========================================================
    # 🔹 KEYWORD DETECTION
    # =========================================================
    def detect_keyword(self, text: str, keywords: List[str]) -> bool:
        text = self._normalize(text)
        return any(k in text for k in keywords)

    # =========================================================
    # 🔹 MAIN PARSER
    # =========================================================
    def parse(self, note: Dict[str, Any]) -> Dict[str, Any]:

        biopsy_text = note.get("biopsyNotes") or ""
        mohs_text = note.get("mohsNotes") or ""
        procedure_text = note.get("procedure") or ""
        assessment_text = note.get("assesment") or ""

        combined_text = f"{biopsy_text} {assessment_text} {procedure_text}".lower()

        biopsy_data = []
        excision_data = []
        mohs_data = []
        closure_data = []

        combined = f"{biopsy_text} {mohs_text} {procedure_text}"
        closure_data = self.extract_closure_sections(combined)
        has_procedure = bool(procedure_text.strip())

        # -------------------------
        # 🔴 BIOPSY
        # -------------------------
        if self.detect_keyword(biopsy_text, BIOPSY_KEYWORDS):
            logger.info("🧠 Biopsy detected")
            biopsy_data = self.extract_biopsy_sections(biopsy_text)

            if not biopsy_data:
                logger.warning("⚠️ Biopsy fallback mode")
                biopsy_data = [{
                    "label": "single",
                    "text": combined_text,
                    "quantity": 1
                }]

        # -------------------------
        # 🔴 EXCISION
        # -------------------------
        if self.detect_keyword(biopsy_text, EXCISION_KEYWORDS):
            logger.info("🧠 Excision detected")
            excision_data = self.extract_excision_sections(biopsy_text)

        # -------------------------
        # 🔴 MOHS (STRUCTURED FIRST)
        # -------------------------
        if self.detect_keyword(mohs_text, MOHS_KEYWORDS):
            logger.info("🧠 Mohs detected (from mohsNotes)")
            mohs_data = self.extract_mohs_sections(mohs_text)

        elif any(k in combined_text for k in MOHS_KEYWORDS):
            logger.warning("⚠️ Mohs detected from fallback context")
            mohs_data = self.extract_mohs_sections(combined_text)

        # -------------------------
        # 🔹 FINAL DEBUG
        # -------------------------
        logger.info(
            f"📊 FINAL PARSER OUTPUT → "
            f"biopsy={len(biopsy_data)}, "
            f"excision={len(excision_data)}, "
            f"mohs={len(mohs_data)}, "
            f"procedure={has_procedure}"
        )

        return {
            "has_biopsy": len(biopsy_data) > 0,
            "biopsy_count": len(biopsy_data),
            "biopsy_sections": biopsy_data,

            "has_excision": len(excision_data) > 0,
            "excision_sections": excision_data,

            "has_mohs": len(mohs_data) > 0,
            "mohs_sections": mohs_data,

            "has_closure": len(closure_data) > 0,
            "closure_sections": closure_data,

            "has_procedure": has_procedure
        }
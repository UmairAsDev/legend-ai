import re
from typing import Dict, Any, List
from loguru import logger


BIOPSY_KEYWORDS = ["biopsy", "bx"]
EXCISION_KEYWORDS = ["excision"]
MOHS_KEYWORDS = ["mohs"]


class ClinicalParser:

    def _normalize(self, text: str) -> str:
        return text.lower() if text else ""

    # -------------------------
    # 🔹 COUNT BIOPSY SECTIONS
    # -------------------------
    def extract_biopsy_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        pattern = r"([A-Z])\.\s*Biopsy.*?(?=(?:\n[A-Z]\.\s*Biopsy|$))"

        matches = list(re.finditer(pattern, text, re.IGNORECASE | re.DOTALL))

        results = []
        for match in matches:
            results.append({
                "label": match.group(1),
                "text": match.group(0).strip(),
                "quantity": 1
            })

        return results
    

    def extract_excision_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        pattern = r"([A-Z])\.\s*Excision.*?(?=(?:\n[A-Z]\.|$))"
        matches = list(re.finditer(pattern, text, re.IGNORECASE | re.DOTALL))

        results = []

        for match in matches:
            section_text = match.group(0)

            logger.info(f"🔍 Processing excision section: {match.group(1)}")

            size = None

            # -------------------------
            # 🔴 PRIORITY 1: Excision Size (with margins)
            # -------------------------
            size_match = re.search(
                r"Excision Size.*?:\s*([\d\.]+)\s*[x\-]\s*([\d\.]+)",
                section_text,
                re.IGNORECASE
            )

            if size_match:
                size = max(float(size_match.group(1)), float(size_match.group(2)))
                logger.info(f"✅ Using Excision Size: {size}")

            # -------------------------
            # 🔴 PRIORITY 2: Wound size
            # -------------------------
            if not size:
                wound_match = re.search(
                    r"wound size.*?:?\s*([\d\.]+)\s*[x\-]?\s*([\d\.]+)?",
                    section_text,
                    re.IGNORECASE
                )

                if wound_match:
                    values = [v for v in wound_match.groups() if v]
                    size = max(map(float, values))
                    logger.info(f"✅ Using Wound Size: {size}")

            # -------------------------
            # 🔴 PRIORITY 3: Final closure size
            # -------------------------
            if not size:
                closure_match = re.search(
                    r"final closure size.*?:?\s*([\d\.]+)",
                    section_text,
                    re.IGNORECASE
                )

                if closure_match:
                    size = float(closure_match.group(1))
                    logger.info(f"✅ Using Final Closure Size: {size}")

            # -------------------------
            # 🔴 DO NOT fallback to lesion size
            # -------------------------
            if not size:
                logger.warning("⚠️ No valid excision size found → SKIPPING section")

            # -------------------------
            # 🔴 REMOVE CLOSURE TEXT (CRITICAL)
            # -------------------------
            cleaned_text = re.sub(
                r"Repair:.*",
                "",
                section_text,
                flags=re.IGNORECASE | re.DOTALL
            )

            results.append({
                "label": match.group(1),
                "text": cleaned_text.strip(),
                "size": size
            })

        logger.info(f"📊 Total excision sections parsed: {len(results)}")

        return results
    # -------------------------
    # 🔹 KEYWORD MATCH
    # -------------------------
    def detect_keyword(self, text: str, keywords: List[str]) -> bool:
        text = self._normalize(text)
        return any(k in text for k in keywords)

    # -------------------------
    # 🔹 MAIN PARSER
    # -------------------------
    def parse(self, note: Dict[str, Any]) -> Dict[str, Any]:

        biopsy_text = note.get("biopsyNotes") or ""
        mohs_text = note.get("mohsNotes") or ""
        procedure_text = note.get("procedure") or ""
        assessment_text = note.get("assesment") or ""

        # 🔴 Combined fallback context
        combined_text = f"{biopsy_text} {assessment_text} {procedure_text}".lower()

        biopsy_data = []
        excision_data = []
        mohs_present = False
        has_procedure = bool(procedure_text.strip())

        # -------------------------
        # 🔴 BIOPSY DETECTION
        # -------------------------
        if (
            self.detect_keyword(biopsy_text, BIOPSY_KEYWORDS) or
            any(k in combined_text for k in BIOPSY_KEYWORDS)
        ):
            logger.info("🧠 Biopsy detected")

            biopsy_data = self.extract_biopsy_sections(biopsy_text)

            # 🔴 fallback if structure missing
            if not biopsy_data:
                logger.warning("⚠️ No structured biopsy sections → fallback mode")

                biopsy_data = [{
                    "label": "single",
                    "text": combined_text,
                    "quantity": 1
                }]

        if self.detect_keyword(biopsy_text, EXCISION_KEYWORDS):

            logger.info("🧠 Excision detected")
            excision_data = self.extract_excision_sections(biopsy_text)
            logger.info(f"📊 Excision sections: {len(excision_data)}")

        # -------------------------
        # 🔴 MOHS DETECTION (FIXED)
        # -------------------------
        if (
            self.detect_keyword(mohs_text, MOHS_KEYWORDS) or
            any(k in combined_text for k in MOHS_KEYWORDS)
        ):
            logger.info("🧠 Mohs detected")
            mohs_present = True

        # -------------------------
        # 🔹 DEBUG
        # -------------------------
        logger.info(
            f"📊 Parser Output → biopsy_count: {len(biopsy_data)}, "
            f"mohs: {mohs_present}, procedure: {has_procedure}"
        )

        return {
            "has_biopsy": len(biopsy_data) > 0,
            "biopsy_count": len(biopsy_data),
            "biopsy_sections": biopsy_data,
            "has_mohs": mohs_present,
            "has_procedure": has_procedure,
            "has_excision": len(excision_data) > 0,
            "excision_sections": excision_data
        }
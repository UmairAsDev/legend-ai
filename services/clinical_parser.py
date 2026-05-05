import re
from typing import Dict, Any, List
from loguru import logger

from utils.parser_utils import (ParserUtils,
    SRT_KEYWORDS,
    BIOPSY_KEYWORDS,
    EXCISION_KEYWORDS,
    MOHS_KEYWORDS,
    DEBRIDEMENT_KEYWORDS,
    WOUND_KEYWORDS,
    DERM_KEYWORDS)

class ClinicalParser:
    def __init__(self):
        # Instantiate the utilities class for use within the parser
        self.utils = ParserUtils()

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

            lesion_count = self.utils.extract_lesion_count(section_text)

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
    # 🔹 MOHS EXTRACTION 
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

            location = self.utils.extract_mohs_location(part)
            stages = self.utils.extract_mohs_stages(part)

            sections.append({
                "label": f"site_{i+1}",
                "text": part,
                "location": location,
                "stages": stages
            })

        logger.info(f"📊 Total Mohs sections: {len(sections)}")
        return sections


    # =========================================================
    # 🔹 CLOSURE EXTRACTION 
    # =========================================================
    def extract_closure_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        logger.info("🔍 CLOSURE: event-based extraction (FINAL FIX)")

        sections = []

        size_patterns = [
            r"final closure size.*?(?:was|is|:)?\s*([\d\.]+)",
            r"final closure size.*?([\d\.]+)\s*cm2",
            r"closure size.*?(?:was|is|:)?\s*([\d\.]+)",
            r"closure length.*?(?:was|is|:)?\s*([\d\.]+)",
            r"length of closure.*?(?:was|is|:)?\s*([\d\.]+)",
        ]

        # 🔴 FIX: COLLECT ALL MATCHES (NO BREAK)
        matches = []
        for p in size_patterns:
            found = list(re.finditer(p, text, re.IGNORECASE))
            matches.extend(found)

        logger.info(f"📏 Total closure size matches: {len(matches)}")

        for i, m in enumerate(matches):

            try:
                size = float(m.group(1))
            except:
                continue

            start = max(0, m.start() - 5000)
            end = min(len(text), m.end() + 3000)

            snippet = text[start:end]
            snippet_lower = snippet.lower()

            # -------------------------
            # TYPE (GLOBAL CONTEXT)
            # -------------------------
            full_back = text[max(0, m.start() - 10000):m.start()].lower()

            if "complex" in full_back:
                ctype = "complex"
            elif "adjacent tissue transfer" in full_back:
                ctype = "adjacent"
            elif "intermediate" in full_back or "layered" in full_back:
                ctype = "intermediate"
            else:
                logger.warning(f"⚠️ Closure {i+1}: type fallback → complex")
                ctype = "complex"

            # -------------------------
            # LOCATION (ROBUST)
            # -------------------------
            back = text[max(0, m.start() - 5000):m.start()]

            loc_matches = list(re.finditer(r"Location:\s*([^\n\r]+)", back, re.IGNORECASE))
            location_raw = loc_matches[-1].group(1).strip() if loc_matches else ""

            loc_lower = (location_raw or "").lower()

            LOCATION_MAP = {
                "extremities": ["scalp", "arm", "leg"],
                "high_risk": ["hand", "foot", "genital", "axillae", "neck", "chin", "cheek", "forehead"],
                "critical": ["nose", "lip", "ear", "eyelid"],
                "trunk": ["back", "chest", "abdomen", "trunk"]
            }

            location_group = "unknown"

            for group, keywords in LOCATION_MAP.items():
                if any(k in loc_lower for k in keywords):
                    location_group = group
                    break

            # fallback
            if location_group == "unknown":
                snippet_loc = re.search(
                    r"(scalp|arm|leg|hand|foot|nose|lip|ear|eyelid|neck|chin|cheek|forehead|back|chest|abdomen)",
                    snippet_lower
                )
                if snippet_loc:
                    loc_lower = snippet_loc.group(1)
                    for group, keywords in LOCATION_MAP.items():
                        if loc_lower in keywords:
                            location_group = group
                            break

            if location_group == "unknown":
                logger.warning(f"⚠️ Closure {i+1}: location unresolved → trunk")
                location_group = "trunk"

            location = location_raw or loc_lower

            logger.info(
                f"✅ Closure {i+1} → size={size}, type={ctype}, group={location_group}"
            )

            sections.append({
                "type": ctype,
                "size": size,
                "location": location,
                "location_group": location_group, 
                "group_key": f"{ctype}_{location_group}",
                "text": snippet.strip()
            })

        # 🔴 DEDUP
        unique = {}
        for s in sections:
            key = (s["size"], s["location"], s["type"])
            unique[key] = s

        final = list(unique.values())

        logger.info(f"📊 FINAL CLOSURES: {final}")

        return final
    

    # =========================================================
    # 🔹 SRT/IGSTR EXTRACTION 
    # =========================================================
    def extract_srt_sections(self, text: str, note: Dict[str, Any]) -> List[Dict]:
        if not text:
            return []

        text_lower = text.lower()

        if not any(k in text_lower for k in SRT_KEYWORDS):
            return []

        logger.info("🔍 Extracting SRT sections...")

        # 🔴 ENERGY
        kv = self.utils.extract_kv(text)

        # 🔴 ULTRASOUND
        ultrasound = "ultrasound" in text_lower

        # 🔴 IMAGE VALIDATION
        images_present = self.utils.detect_images(note)

        # 🔴 TYPE
        if kv and kv <= 150:
            delivery_type = "superficial"
        elif kv and kv > 150:
            delivery_type = "orthovoltage"
        else:
            delivery_type = "unknown"

        logger.info(
            f"⚡ SRT → kv={kv}, type={delivery_type}, "
            f"ultrasound={ultrasound}, images={images_present}"
        )

        return [{
            "kv": kv,
            "type": delivery_type,
            "ultrasound": ultrasound,
            "images_present": images_present,
            "text": text
        }]
    

    # =========================================================
    # 🔹 DEBRIDEMENT EXTRACTION 
    # =========================================================
    def extract_debridement_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        text_lower = text.lower()

        if not any(k in text_lower for k in DEBRIDEMENT_KEYWORDS):
            return []

        logger.info("🔍 Extracting Debridement sections (STRICT MODE)...")

        sections = []

        # -------------------------
        # 🔴 SPLIT INTO BLOCKS (NEW)
        # -------------------------
        blocks = re.split(r"(?=Debridement\s*\(DBR\))", text, flags=re.IGNORECASE)

        for i, block in enumerate(blocks):
            block = block.strip()
            block_lower = block.lower()

            if not block or "debridement" not in block_lower:
                continue

            logger.info(f"🔍 Processing debridement block {i+1}")

            # =========================================================
            # 🔴 STRICT VALIDATION (CRITICAL FIX)
            # =========================================================
            has_location = re.search(r"location:\s*.+", block_lower)
            has_quantity = re.search(r"quantity:\s*\d+", block_lower)
            has_method = re.search(r"method:\s*.+", block_lower)
            has_choice = re.search(r"choice:\s*.+", block_lower)

            if not (has_location and has_quantity and has_method and has_choice):
                logger.warning(
                    "⛔ Skipping debridement → missing required fields "
                    "(location/quantity/method/choice)"
                )
                continue

            # =========================================================
            # 🔴 DEPTH (same logic, but scoped per block)
            # =========================================================
            if "partial thickness" in block_lower or "superficial" in block_lower or "shave" in block_lower:
                depth = "partial"
            elif "full thickness" in block_lower:
                depth = "full"
            elif "subcutaneous" in block_lower:
                depth = "subcutaneous"
            else:
                depth = "unknown"

            # -------------------------
            # 🔴 NAIL
            # -------------------------
            nail = any(k in block_lower for k in ["nail", "toenail", "fingernail"])

            # -------------------------
            # 🔴 DERMATOLOGIC
            # -------------------------
            is_dermatologic = any(k in block_lower for k in DERM_KEYWORDS)

            # -------------------------
            # 🔴 WOUND
            # -------------------------
            is_wound = any(k in block_lower for k in WOUND_KEYWORDS)

            # -------------------------
            # 🔴 QUANTITY (block-level FIX)
            # -------------------------
            qty_match = re.search(r"quantity:\s*(\d+)", block_lower)
            quantity = int(qty_match.group(1)) if qty_match else 1

            # -------------------------
            # 🔴 LOCATION (NEW)
            # -------------------------
            loc_match = re.search(r"location:\s*([^\n\r]+)", block, re.IGNORECASE)
            location = loc_match.group(1).strip() if loc_match else ""

            # -------------------------
            # 🔴 METHOD (NEW)
            # -------------------------
            method_match = re.search(r"method:\s*([^\n\r]+)", block, re.IGNORECASE)
            method = method_match.group(1).strip() if method_match else ""

            # -------------------------
            # 🔴 CHOICE (NEW)
            # -------------------------
            choice_match = re.search(r"choice:\s*([^\n\r]+)", block, re.IGNORECASE)
            choice = choice_match.group(1).strip() if choice_match else ""

            logger.info(
                f"🧠 Debridement VALID → depth={depth}, nail={nail}, "
                f"derm={is_dermatologic}, wound={is_wound}, qty={quantity}, "
                f"location={location}"
            )

            # =========================================================
            # 🔴 FINAL SECTION (ONLY VALID ONES)
            # =========================================================
            sections.append({
                "depth": depth,
                "nail": nail,
                "dermatologic": is_dermatologic,
                "is_wound": is_wound,
                "quantity": quantity,
                "location": location,
                "method": method,
                "choice": choice,
                "text": block
            })

        logger.info(f"📊 Valid debridement sections: {len(sections)}")

        return sections


    # =========================================================
    # 🔹 MAIN PARSER
    # =========================================================
    def parse(self, note: Dict[str, Any]) -> Dict[str, Any]:

        biopsy_text = note.get("biopsyNotes") or ""
        mohs_text = note.get("mohsNotes") or ""
        procedure_text = note.get("procedure") or ""

        closure_data = []

        closure_data += self.extract_closure_sections(biopsy_text)
        closure_data += self.extract_closure_sections(mohs_text)
        closure_data += self.extract_closure_sections(procedure_text)

        biopsy_data = self.extract_biopsy_sections(biopsy_text) \
            if self.utils.detect_keyword(biopsy_text, BIOPSY_KEYWORDS) else []

        excision_data = self.extract_excision_sections(biopsy_text) \
            if self.utils.detect_keyword(biopsy_text, EXCISION_KEYWORDS) else []

        mohs_data = self.extract_mohs_sections(mohs_text) \
            if self.utils.detect_keyword(mohs_text, MOHS_KEYWORDS) else []
        
        srt_data = self.extract_srt_sections(procedure_text, note)
        debridement_data = self.extract_debridement_sections(procedure_text)

        return {
            "has_biopsy": bool(biopsy_data),
            "biopsy_sections": biopsy_data,

            "has_excision": bool(excision_data),
            "excision_sections": excision_data,

            "has_mohs": bool(mohs_data),
            "mohs_sections": mohs_data,

            "has_closure": bool(closure_data),
            "closure_sections": closure_data,

            "has_srt": bool(srt_data),
            "srt_sections": srt_data,

            "has_debridement": bool(debridement_data),
            "debridement_sections": debridement_data,

            "has_procedure": bool(procedure_text.strip())
        }
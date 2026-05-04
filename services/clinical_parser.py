import re
from typing import Dict, Any, List
from loguru import logger


SRT_KEYWORDS = ["srt", "igsrt", "superficial radiation", "surface radiation"]
BIOPSY_KEYWORDS = ["biopsy", "bx"]
EXCISION_KEYWORDS = ["excision"]
MOHS_KEYWORDS = ["mohs"]


class ClinicalParser:

    def _normalize(self, text: str) -> str:
        return text.lower() if text else ""
    

    # =========================================================
    # DETECT ULTRASOUND IMAGES
    # =========================================================
    def _detect_images(self, note: Dict[str, Any]) -> bool:
        image_fields = ["images", "attachments", "media", "ultrasoundImages"]

        for field in image_fields:
            val = note.get(field)
            if isinstance(val, list) and len(val) > 0:
                logger.info(f"🖼️ Images detected via field: {field}")
                return True

        logger.warning("⚠️ No real images found")
        return False

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
    # 🔴CLOSURE EXTRACTION 
    # =========================================================

    def _map_location_group(self, location: str) -> str:
        loc = (location or "").lower()

        if any(k in loc for k in ["scalp", "arm", "leg"]):
            return "extremities"

        if any(k in loc for k in ["face", "nose", "lip", "ear", "hand", "foot", "eyelid"]):
            return "high_risk"

        return "trunk"


    def _split_clinical_blocks(self, text: str):
        return re.split(
            r"(?=Location:\s|[A-Z]\.\s|Mohs Micrographic Procedure)",
            text,
            flags=re.IGNORECASE
        )


    def _extract_location(self, block: str) -> str:
        m = re.search(r"Location:\s*([^\n\r]+)", block, re.IGNORECASE)
        return m.group(1).strip() if m else ""


    def _detect_closure_type(self, block: str):
        b = block.lower()

        if "complex" in b:
            return "complex"

        if "intermediate" in b or "layered" in b:
            return "intermediate"

        return None


    def _extract_closure_size(self, block: str):
        patterns = [
            r"final closure size.*?([\d\.]+)",
            r"closure size.*?([\d\.]+)",
            r"closure length.*?([\d\.]+)",
            r"length of closure.*?([\d\.]+)",
            r"measuring\s*([\d\.]+)\s*cm",
        ]

        for p in patterns:
            m = re.search(p, block, re.IGNORECASE)
            if m:
                return float(m.group(1))

        return None
    

    def _extract_kv(self, text: str) -> int | None:
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


    def extract_closure_sections(self, text: str) -> List[Dict]:
        if not text:
            return []

        logger.info("🔍 CLOSURE: event-based extraction (FINAL FIX)")

        sections = []

        size_patterns = [
            r"final closure size.*?(?:was|is|:)?\s*([\d\.]+)",
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
            elif "intermediate" in full_back or "layered" in full_back:
                ctype = "intermediate"
            else:
                logger.warning(f"⚠️ Closure {i+1}: type fallback → intermediate")
                ctype = "intermediate"

            # -------------------------
            # LOCATION (ROBUST)
            # -------------------------
            back = text[max(0, m.start() - 5000):m.start()]

            loc_matches = list(re.finditer(r"Location:\s*([^\n\r]+)", back, re.IGNORECASE))
            location_raw = loc_matches[-1].group(1).strip() if loc_matches else ""

            loc_lower = (location_raw or "").lower()

            LOCATION_MAP = {
                "extremities": ["scalp", "arm", "leg"],
                "high_risk": ["hand", "foot", "genital", "axilla", "neck", "chin", "cheek", "forehead"],
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
                "location_group": location_group,   # ✅ USE THIS
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
    

    def extract_srt_sections(self, text: str, note: Dict[str, Any]) -> List[Dict]:
        if not text:
            return []

        text_lower = text.lower()

        if not any(k in text_lower for k in SRT_KEYWORDS):
            return []

        logger.info("🔍 Extracting SRT sections...")

        # 🔴 ENERGY
        kv = self._extract_kv(text)

        # 🔴 ULTRASOUND
        ultrasound = "ultrasound" in text_lower

        # 🔴 IMAGE VALIDATION
        images_present = self._detect_images(note)

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

        closure_data = []

        closure_data += self.extract_closure_sections(biopsy_text)
        closure_data += self.extract_closure_sections(mohs_text)
        closure_data += self.extract_closure_sections(procedure_text)

        biopsy_data = self.extract_biopsy_sections(biopsy_text) \
            if self.detect_keyword(biopsy_text, BIOPSY_KEYWORDS) else []

        excision_data = self.extract_excision_sections(biopsy_text) \
            if self.detect_keyword(biopsy_text, EXCISION_KEYWORDS) else []

        mohs_data = self.extract_mohs_sections(mohs_text) \
            if self.detect_keyword(mohs_text, MOHS_KEYWORDS) else []
        
        srt_data = self.extract_srt_sections(procedure_text, note)

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

            "has_procedure": bool(procedure_text.strip())
        }
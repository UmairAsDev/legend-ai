# app/services/medical_engine.py

import re
from typing import List

from loguru import logger

from llm_layer.coding_prompt import build_coding_prompt
from llm_layer.cot_prompts import (
    build_clinical_reader_prompt,
    build_billing_params_prompt,
    build_focused_coder_prompt,
)
from llm_layer.llm_client import LLMClient
from services.clinical_parser import ClinicalParser
from services.audit_logger import log_coding_decision
from services.reasoning_engine import generate_reasoning
from services.validation_engine import validate_codes
from services.web_lookup import WebLookupService
from services.code_selectors import (
    BiopsySelector,
    ClosureSelector,
    DebridementSelector,
    DestructionSelector,
    ExcisionSelector,
    MohsSelector,
    ShaveRemovalSelector,
    SrtSelector,
    XtracSelector,
)
from services.retriever import CodeRetriever
from services.site_builder import build_sites, ProcedureSite
from services.procedure_normalizer import normalize_procedures
from services.boundary_checker import detect_boundary_cases
from src.data_layer.progressnote import notes
from utils.engine_utils import (
    aggregate_chemical_peels,
    aggregate_closures,
    aggregate_shave_removals,
    clean_note_data,
    enforce_closure_addon,
    enforce_confirmed_codes,
    enforce_destruction_quantity,
    enforce_em_and_modifiers,
    enforce_excision_quantity,
    enrich_with_charges,
    enrich_with_site_ids,
    merge_parsed_results,
    normalize_llm_output,
    serialize_data,
    trim_for_llm,
)


class CodingNodes:

    def __init__(self):
        self.parser = ClinicalParser()
        self.retriever = CodeRetriever()
        self.llm = LLMClient()
        self.web_lookup = WebLookupService()

    # ------------------------------------------------------------------
    # PIPELINE NODES
    # ------------------------------------------------------------------

    async def fetch(self, state):
        try:
            logger.info(f"Fetching note: {state['note_id']}")
            data = await notes(state["note_id"])
            if not data:
                raise ValueError(f"Note {state['note_id']} not found")
            return {"raw_note": data[0]}
        except Exception as e:
            logger.exception(f"Fetch failed: {e}")
            raise

    async def clean(self, state):
        try:
            cleaned = clean_note_data(state["raw_note"])
            cleaned = serialize_data(cleaned)
            return {"cleaned_note": cleaned}
        except Exception as e:
            logger.exception(f"Clean step failed: {e}")
            raise

    async def parse(self, state):
        try:
            parsed = self.parser.parse(state["cleaned_note"])
            parsed = aggregate_closures(parsed)
            parsed = aggregate_shave_removals(parsed)
            parsed = aggregate_chemical_peels(parsed)
            return {"parsed": parsed}
        except Exception as e:
            logger.exception(f"Parse step failed: {e}")
            raise

    # ------------------------------------------------------------------
    # CoT STEP 1 — CLINICAL READER
    # Holistic note comprehension; free-text output; no codes assigned.
    # ------------------------------------------------------------------

    async def clinical_read(self, state):
        try:
            prompt = build_clinical_reader_prompt(state["cleaned_note"])
            # No parser — returns raw free-text reasoning
            summary = await self.llm.generate_response(prompt)
            logger.info("Clinical reader complete")
            return {"clinical_summary": summary or ""}
        except Exception as e:
            # Non-fatal — billing_params will extract directly from note
            logger.warning(f"Clinical reader failed (non-fatal): {e}")
            return {"clinical_summary": ""}

    # ------------------------------------------------------------------
    # CoT STEP 2 — BILLING PARAMETERIZER
    # Structured extraction from the clinical summary + note.
    # Merges with regex parsed output (regex always wins on overlap).
    # ------------------------------------------------------------------

    async def billing_params(self, state):
        try:
            parser, formatted_prompt = build_billing_params_prompt(
                clinical_summary=state.get("clinical_summary", ""),
                note=state["cleaned_note"],
            )
            llm_extraction = await self.llm.generate_response(
                formatted_prompt, parser=parser
            )

            # Normalise to plain dict
            if hasattr(llm_extraction, "model_dump"):
                llm_extraction = llm_extraction.model_dump()
            elif not isinstance(llm_extraction, dict):
                llm_extraction = {}

            merged, parse_source = merge_parsed_results(
                state["parsed"], llm_extraction
            )

            # Deterministic boundary detection (replaces LLM boundary flags).
            # Checks each size against minSize/maxSize for its OWN procedure family —
            # never cross-applies excision boundaries to closure sizes.
            boundary_issues = detect_boundary_cases(merged)
            if boundary_issues:
                existing = merged.setdefault("unresolved_procedures", [])
                # Avoid duplicating what the LLM may have already flagged
                existing_descs = {u.get("reason") for u in existing}
                for issue in boundary_issues:
                    if issue.get("reason") not in existing_descs:
                        existing.append(issue)
                logger.info(f"BoundaryChecker added {len(boundary_issues)} unresolved item(s)")

            # Remove LLM-generated boundary_case entries — they're replaced above
            merged["unresolved_procedures"] = [
                u for u in merged.get("unresolved_procedures", [])
                if not (u.get("reason") == "boundary_case" and "suggested_resolution" not in u)
            ]

            # Surface unresolved procedures
            unresolved = merged.get("unresolved_procedures", [])
            if unresolved:
                logger.warning(
                    "Unresolved procedures: "
                    + ", ".join(u.get("description", "") for u in unresolved)
                )

            return {"parsed": merged, "parse_source": parse_source}

        except Exception as e:
            # Non-fatal — keep regex-only parsed output
            logger.warning(f"Billing params extraction failed (non-fatal): {e}")
            return {"parse_source": {}}

    # ------------------------------------------------------------------
    # SITE BUILDER — Phase 1
    # Groups parsed sections into ProcedureSite objects and back-annotates
    # every section with its site_id so selectors can tag produced codes.
    # ------------------------------------------------------------------

    async def build_sites_node(self, state):
        try:
            # 1. Normalize parsed sections into standardized ProcedureInstance objects
            procedure_instances = normalize_procedures(state["parsed"])

            # 2. Group instances into ProcedureSite objects (annotates sections with site_id)
            sites: list[ProcedureSite] = build_sites(state["parsed"])

            return {
                "procedures": [p.to_dict() for p in procedure_instances],
                "sites":      [s.to_dict() for s in sites],
            }
        except Exception as e:
            logger.warning(f"Site builder failed (non-fatal): {e}")
            return {"procedures": [], "sites": []}

    # ------------------------------------------------------------------
    # WEB LOOKUP — CONDITIONAL
    # Searches only for triggered edge cases; max 2 per note.
    # ------------------------------------------------------------------

    async def web_lookup_node(self, state):
        try:
            triggers = self.web_lookup.should_search(state["parsed"])
            if not triggers:
                return {"web_refs": []}
            refs = await self.web_lookup.search(triggers)
            logger.info(f"Web lookup returned {len(refs)} reference(s)")
            return {"web_refs": refs}
        except Exception as e:
            logger.warning(f"Web lookup failed (non-fatal): {e}")
            return {"web_refs": []}

    async def retrieve(self, state):
        """
        Build the candidate list.

        For procedures with deterministic selection rules (excision, shave
        removal, destruction, biopsy, mohs, closure, SRT, debridement,
        Xtrac), the selector is tried first.  If it returns results those
        codes are tagged confidence='confirmed'.  Only when the selector
        cannot determine the code (missing data) does the method fall back
        to the pgvector DB filter, tagging results as confidence='candidate'.

        Procedures without deterministic rules (IPL, laser, filler, chemical
        peel) always go through the DB filter.
        """
        try:
            parsed = state.get("parsed", {})
            cleaned = state.get("cleaned_note", {})
            all_candidates = []

            detected = [k for k, v in parsed.items() if k.startswith("has_") and v]
            logger.info(f"Detected procedures: {detected}")

            if parsed.get("has_excision"):
                all_candidates.extend(await self._retrieve_excision(parsed))

            if parsed.get("has_destruction"):
                all_candidates.extend(await self._retrieve_destruction(parsed))

            if parsed.get("has_biopsy"):
                all_candidates.extend(await self._retrieve_biopsy(parsed))

            if parsed.get("has_shave_removal"):
                all_candidates.extend(await self._retrieve_shave_removal(parsed))

            if parsed.get("has_mohs"):
                all_candidates.extend(await self._retrieve_mohs(parsed))

            if parsed.get("has_closure"):
                all_candidates.extend(await self._retrieve_closure(parsed))

            if parsed.get("has_srt"):
                all_candidates.extend(await self._retrieve_srt(parsed))

            if parsed.get("has_debridement"):
                all_candidates.extend(await self._retrieve_debridement(parsed))

            if parsed.get("has_xtrac"):
                all_candidates.extend(await self._retrieve_xtrac(parsed))

            # Procedures without deterministic rules — always DB filter
            if parsed.get("has_laser_treatment"):
                all_candidates.extend(await self._retrieve_laser_treatment(parsed, cleaned))

            if parsed.get("has_ipl"):
                all_candidates.extend(await self._retrieve_ipl(parsed))

            if parsed.get("has_filler_material"):
                all_candidates.extend(await self._retrieve_filler_material(parsed))

            if parsed.get("has_filler"):
                all_candidates.extend(await self._retrieve_filler(parsed))

            if parsed.get("has_chemical_peel"):
                all_candidates.extend(await self._retrieve_chemical_peel(parsed))

            if all_candidates:
                final = self._deduplicate(all_candidates)
                confirmed = sum(1 for c in final if c.get("confidence") == "confirmed")
                logger.info(
                    f"Retrieval complete: {len(final)} candidates  "
                    f"({confirmed} confirmed, {len(final)-confirmed} candidates)"
                )
                return {"candidates": final}

            # No procedures detected — return empty candidates.
            # E/M code will be assigned deterministically by em_modifiers node.
            logger.info("No procedures detected — returning empty candidates")
            return {"candidates": []}

        except Exception as e:
            logger.exception(f"Retrieval failed: {e}")
            raise

    async def llm_call(self, state):
        """
        CoT Step 3 — Focused Coder.

        Uses the pre-reasoned parameters from billing_params (Step 2) and
        the focused coder prompt instead of the legacy one-shot prompt.

        Falls back to the original one-shot prompt if Step 3 fails,
        so the pipeline is never left without output.
        """
        try:
            logger.info("CoT Step 3: Focused coder")

            candidates = state["candidates"]
            confirmed = [c for c in candidates if c.get("confidence") == "confirmed"]
            ambiguous = trim_for_llm(
                [c for c in candidates if c.get("confidence") != "confirmed"]
            )

            logger.info(
                f"LLM input: {len(confirmed)} confirmed + "
                f"{len(ambiguous)} ambiguous | "
                f"web_refs={len(state.get('web_refs', []))}"
            )

            parser, formatted_prompt = build_focused_coder_prompt(
                note=state["cleaned_note"],
                parsed=state["parsed"],
                confirmed_codes=confirmed,
                ambiguous_candidates=ambiguous,
                web_refs=state.get("web_refs"),
            )

            result = await self.llm.generate_response(formatted_prompt, parser=parser)
            result = normalize_llm_output(result)

        except Exception as e:
            logger.warning(
                f"Focused coder failed ({e}) — falling back to one-shot prompt"
            )
            result = await self._llm_call_fallback(state)

        # Deterministic enforcement passes
        result = enforce_confirmed_codes(
            state["candidates"], result, note=state.get("cleaned_note")
        )
        result = enforce_excision_quantity(state["parsed"], result)
        result = enforce_closure_addon(state["parsed"], state["candidates"], result)
        result = enforce_destruction_quantity(
            parsed=state["parsed"],
            retrieved_candidates=state["candidates"],
            llm_output=result,
        )

        # Stamp site_id onto every output code that doesn't already carry one.
        # Must run after all enforcement so every code is present in the output.
        result = enrich_with_site_ids(result, state["candidates"])

        # Inject unresolved procedures into audit flags
        unresolved = state["parsed"].get("unresolved_procedures", [])
        if unresolved:
            flags = result.setdefault("audit_flags", [])
            for proc in unresolved:
                flags.append(
                    f"Unresolved procedure — {proc.get('description', '')} "
                    f"(reason: {proc.get('reason', 'unknown')})"
                )

        logger.info("LLM step complete — deterministic enforcement applied")
        return {"llm_output": result}

    async def _llm_call_fallback(self, state) -> dict:
        """Original one-shot prompt — used only when CoT Step 3 fails."""
        logger.info("Running one-shot fallback prompt")
        candidates = state["candidates"]
        confirmed = [c for c in candidates if c.get("confidence") == "confirmed"]
        ambiguous = trim_for_llm(
            [c for c in candidates if c.get("confidence") != "confirmed"]
        )
        _, _, formatted_prompt = build_coding_prompt(
            {"note": state["cleaned_note"], "parsed": state["parsed"]},
            confirmed_codes=confirmed,
            ambiguous_candidates=ambiguous,
        )
        from langchain_core.output_parsers import JsonOutputParser
        from llm_layer.coding_prompt import OutputSchema
        parser = JsonOutputParser(pydantic_object=OutputSchema)
        result = await self.llm.generate_response(formatted_prompt, parser=parser)
        return normalize_llm_output(result)

    async def validate(self, state):
        """
        Validation node — runs after LLM code assignment, before modifier enforcement.
        Applies 5 hard and soft billing integrity rules.
        Non-fatal: if validation fails, pipeline continues with original output.
        """
        try:
            result = validate_codes(
                llm_output=state["llm_output"],
                parsed=state["parsed"],
                candidates=state.get("candidates", []),
            )
            return {"llm_output": result}
        except Exception as e:
            logger.warning(f"Validation node failed (non-fatal): {e}")
            return {}

    async def assign_em(self, state):
        try:
            result = enforce_em_and_modifiers(
                parsed=state["parsed"],
                llm_output=state["llm_output"],
                note=state.get("cleaned_note"),
                sites=state.get("sites") or [],   # Phase 8: site-aware modifier
            )
            result = enrich_with_charges(result)
            return {"llm_output": result}
        except Exception as e:
            logger.exception(f"E/M assignment failed: {e}")
            raise

    async def reason(self, state):
        """
        Reasoning node — explains every code/modifier/E/M decision in plain language,
        cites the note, and flags anything the documentation does not support.
        Runs after all enforcement is complete.  Never changes codes.
        """
        try:
            result = await generate_reasoning(
                llm_output=state["llm_output"],
                parsed=state["parsed"],
                note=state.get("cleaned_note", {}),
            )
            log_coding_decision(
                note_id=state["note_id"],
                llm_output=result,
                candidates=state.get("candidates", []),
                parsed=state["parsed"],
            )
            return {"llm_output": result}
        except Exception as e:
            logger.exception(f"Reasoning node failed: {e}")
            raise

    # ------------------------------------------------------------------
    # PRIVATE RETRIEVE HELPERS
    # Each method tries the deterministic selector first.
    # Falls back to DB filter when selector returns empty (missing data).
    #
    # Every produced code is tagged with site_id from the section that
    # produced it.  The site_builder annotated each section in-place with
    # its site_id before this node runs.
    # ------------------------------------------------------------------

    @staticmethod
    def _tag(codes: List[dict], site_id: str) -> None:
        """Attach site_id to every code in the list (mutates in place)."""
        for c in codes:
            c["site_id"] = site_id

    async def _retrieve_excision(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("excision_sections", []):
            site_id  = sec.get("site_id", "")
            size     = sec.get("size")
            location = sec.get("location") or ""
            lesion_type = (
                "malignant"
                if re.search(r"(?<!non[- ])\bmalignant\b", (sec.get("text", "")).lower())
                else "benign"
            )

            selected = ExcisionSelector.select(size, location, lesion_type)
            if selected:
                self._tag(selected, site_id)
                results.extend(selected)
                continue

            if not size:
                logger.warning("Excision: size missing and selector failed — skipping")
                continue
            loc_match = re.search(r"Location:\s*(.*)", sec.get("text", ""))
            loc = loc_match.group(1).strip() if loc_match else location
            res = await self.retriever.excision_filter(size, loc)
            for r in res:
                r["confidence"] = "candidate"
                r["source"]     = "excision"
            self._tag(res, site_id)
            results.extend(res)
        return results

    async def _retrieve_destruction(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("destruction_sections", []):
            site_id  = sec.get("site_id", "")
            dtype    = sec.get("destruction_type")
            qty      = sec.get("quantity") or 1
            size     = sec.get("size")
            location = sec.get("location")

            selected = DestructionSelector.select(dtype, int(qty or 1), size, location)
            if selected:
                for r in selected:
                    r["destruction_label"]    = sec.get("label")
                    r["destruction_location"] = location
                    r["destruction_quantity"] = qty
                    r["destruction_size"]     = size
                self._tag(selected, site_id)
                results.extend(selected)
                continue

            res = await self.retriever.destruction_filter(
                destruction_type=dtype, quantity=qty, size=size, location=location
            )
            for r in res:
                r["confidence"]           = "candidate"
                r["source"]               = f"destruction_{dtype}"
                r["destruction_label"]    = sec.get("label")
                r["destruction_location"] = location
                r["destruction_quantity"] = qty
                r["destruction_size"]     = size
            self._tag(res, site_id)
            results.extend(res)
        return results

    async def _retrieve_biopsy(self, parsed: dict) -> List[dict]:
        biopsy_sections = parsed.get("biopsy_sections", [])
        total_count = len(biopsy_sections)
        # Use the first section's site_id as a representative tag.
        # Multi-site biopsy refinement is tracked for a future per-section pass.
        first_site_id = biopsy_sections[0].get("site_id", "") if biopsy_sections else ""

        method = None
        for sec in biopsy_sections:
            text = (sec.get("text") or "").lower()
            if "punch" in text:
                method = "punch"
                break
            if "shave" in text or "tangential" in text:
                method = "tangential"
                break
            if "incision" in text:
                method = "incisional"
                break

        selected = BiopsySelector.select(method, total_count)
        if selected:
            self._tag(selected, first_site_id)
            return selected

        res = await self.retriever.biopsy_filter()
        for r in res:
            r["confidence"] = "candidate"
        self._tag(res, first_site_id)
        return res

    async def _retrieve_shave_removal(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("shave_removal_aggregated", []):
            # shave_removal_aggregated doesn't carry site_id directly; derive from
            # the first raw section whose location_group + size match this group.
            site_id = self._shave_site_id(parsed, sec)

            selected = ShaveRemovalSelector.select(
                size=sec.get("size"),
                location_group=sec.get("location_group"),
            )
            if selected:
                for r in selected:
                    r["shave_quantity"] = sec.get("quantity")
                self._tag(selected, site_id)
                results.extend(selected)
                continue

            res = await self.retriever.shave_removal_filter(
                location_group=sec.get("location_group"),
                size=sec.get("size"),
            )
            for r in res:
                r["confidence"]    = "candidate"
                r["source"]        = "shave_removal"
                r["shave_quantity"] = sec.get("quantity")
            self._tag(res, site_id)
            results.extend(res)
        return results

    @staticmethod
    def _shave_site_id(parsed: dict, aggregated_sec: dict) -> str:
        """
        Find the site_id from the first raw shave_removal_section that matches
        this aggregated group (same location_group and size).
        """
        for raw in parsed.get("shave_removal_sections", []):
            if (raw.get("location_group") == aggregated_sec.get("location_group")
                    and raw.get("size") == aggregated_sec.get("size")):
                return raw.get("site_id", "")
        return ""

    async def _retrieve_mohs(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("mohs_sections", []):
            site_id  = sec.get("site_id", "")
            location = sec.get("location", "")
            stages   = int(sec.get("stages") or 1)

            selected = MohsSelector.select(location, stages)
            if selected:
                self._tag(selected, site_id)
                results.extend(selected)
                continue

            res = await self.retriever.mohs_filter(location) or []
            for r in res:
                r["confidence"] = "candidate"
                r["source"]     = "mohs"
            self._tag(res, site_id)
            results.extend(res)
        return results

    async def _retrieve_closure(self, parsed: dict) -> List[dict]:
        results = []
        for group in parsed.get("closure_aggregated", []):
            total_size = group.get("total_size")
            ctype      = group.get("type")
            group_key  = group.get("group_key", "")

            # Extract location_group by stripping the closure type prefix.
            # group_key format: "{type}_{location_group}", e.g. "adjacent_high_risk".
            # Using split("_")[-1] would return "risk" for "high_risk" — wrong.
            # Using split("_", 1)[1] returns the full location suffix correctly.
            if group_key and "_" in group_key:
                location_group = group_key.split("_", 1)[1]
            else:
                location_group = group_key or None
            # Derive site_id from the first raw closure section in this group
            site_id = self._closure_site_id(parsed, group_key)

            if not total_size:
                logger.warning("Closure: missing size — skipped")
                continue

            selected = ClosureSelector.select(total_size, ctype, location_group)
            if selected:
                for r in selected:
                    r["closure_group"] = group_key
                self._tag(selected, site_id)
                results.extend(selected)
                continue

            res = await self.retriever.closure_filter(total_size, location_group, ctype)
            for r in res:
                r["confidence"]    = "candidate"
                r["source"]        = "closure"
                r["closure_group"] = group_key
            self._tag(res, site_id)
            results.extend(res)
        return results

    @staticmethod
    def _closure_site_id(parsed: dict, group_key: str) -> str:
        for raw in parsed.get("closure_sections", []):
            if raw.get("group_key") == group_key:
                return raw.get("site_id", "")
        return ""

    async def _retrieve_srt(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("srt_sections", []):
            site_id  = sec.get("site_id", "")
            selected = SrtSelector.select(
                kv=sec.get("kv"),
                ultrasound=bool(sec.get("ultrasound")),
                images_present=bool(sec.get("images_present")),
            )
            if selected:
                self._tag(selected, site_id)
                results.extend(selected)
                continue

            res = await self.retriever.srt_filter(sec)
            for r in res:
                r["confidence"] = "candidate"
                r["source"]     = "srt"
            self._tag(res, site_id)
            results.extend(res)
        return results

    async def _retrieve_debridement(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("debridement_sections", []):
            site_id  = sec.get("site_id", "")
            selected = DebridementSelector.select(
                nail=bool(sec.get("nail")),
                dermatologic=bool(sec.get("dermatologic")),
                is_wound=bool(sec.get("is_wound")),
                depth=sec.get("depth"),
                quantity=int(sec.get("quantity") or 1),
            )
            if selected:
                self._tag(selected, site_id)
                results.extend(selected)
                continue

            res = await self.retriever.debridement_filter(sec)
            for r in res:
                r["confidence"] = "candidate"
                r["source"]     = "debridement"
            self._tag(res, site_id)
            results.extend(res)
        return results

    async def _retrieve_xtrac(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("xtrac_sections", []):
            site_id  = sec.get("site_id", "")
            selected = XtracSelector.select(total_area=sec.get("total_area"))
            if selected:
                for r in selected:
                    r["xtrac_area"] = sec.get("total_area")
                self._tag(selected, site_id)
                results.extend(selected)
                continue

            res = await self.retriever.xtrac_filter(total_area=sec.get("total_area"))
            for r in res:
                r["confidence"] = "candidate"
                r["source"]     = "xtrac"
            self._tag(res, site_id)
            results.extend(res)
        return results

    # Procedures without deterministic rules — always DB filter

    async def _retrieve_laser_treatment(self, parsed: dict, cleaned: dict) -> List[dict]:
        results = []
        procedure_text = cleaned.get("procedure", "")
        for sec in parsed.get("laser_treatment_sections", []):
            site_id = sec.get("site_id", "")
            try:
                res = await self.retriever.laser_treatment_filter(
                    section=sec, full_procedure_text=procedure_text
                )
                for r in res:
                    r["confidence"] = "candidate"
                    r["source"]     = "laser_treatment"
                self._tag(res, site_id)
                results.extend(res)
            except Exception as e:
                logger.exception(f"Laser retrieval failed: {e}")
        return results

    async def _retrieve_ipl(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("ipl_sections", []):
            site_id = sec.get("site_id", "")
            try:
                res = await self.retriever.ipl_filter(section=sec)
                for r in res:
                    r["confidence"] = "candidate"
                    r["source"]     = "ipl"
                self._tag(res, site_id)
                results.extend(res)
            except Exception as e:
                logger.exception(f"IPL retrieval failed: {e}")
        return results

    async def _retrieve_filler_material(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("filler_material_sections", []):
            site_id = sec.get("site_id", "")
            try:
                res = await self.retriever.filler_material_filter(section=sec)
                for r in res:
                    r["confidence"] = "candidate"
                    r["source"]     = "fm"
                self._tag(res, site_id)
                results.extend(res)
            except Exception as e:
                logger.exception(f"Filler material retrieval failed: {e}")
        return results

    async def _retrieve_filler(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("filler_sections", []):
            site_id = sec.get("site_id", "")
            try:
                res = await self.retriever.filler_filter(section=sec)
                for r in res:
                    r["confidence"] = "candidate"
                    r["source"]     = "filler"
                self._tag(res, site_id)
                results.extend(res)
            except Exception as e:
                logger.exception(f"Filler retrieval failed: {e}")
        return results

    async def _retrieve_chemical_peel(self, parsed: dict) -> List[dict]:
        results = []
        for sec in parsed.get("chemical_peel_sections", []):
            site_id = sec.get("site_id", "")
            try:
                res = await self.retriever.chemical_peel_filter(section=sec)
                for r in res:
                    r["confidence"] = "candidate"
                    r["source"]     = "chemical_peel"
                self._tag(res, site_id)
                results.extend(res)
            except Exception as e:
                logger.exception(f"Chemical peel retrieval failed: {e}")
        return results

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate(candidates: List[dict]) -> List[dict]:
        """
        Deduplicate by code, preferring confirmed over candidate entries
        so a selector-confirmed code is never overwritten by a fallback result.
        """
        seen: dict = {}
        for c in candidates:
            code = c.get("code")
            if not code:
                continue
            existing = seen.get(code)
            if existing is None:
                seen[code] = c
            elif existing.get("confidence") != "confirmed" and c.get("confidence") == "confirmed":
                seen[code] = c
        return list(seen.values())


""
# services/lesion_validator.py
"""
Phase 5 — Lesion Conflict Validation.

Detects same-site procedure pairs that cannot both be billed and either
rejects the lower-priority code or flags it for human review.

Design principles (from the plan):
  - ALL conflict rules are defined in terms of procedure FAMILY names,
    not CPT code numbers or code prefixes.
  - Family membership is determined at runtime from the CPT's `proName`
    field in proCodeList.csv via the KnowledgeBase.
  - Adding a new CPT code with an existing proName automatically brings
    it under the applicable conflict rules with zero code changes.
  - NCCI bundled pairs (BUNDLED_PAIRS in constants.py) are the only place
    where specific code numbers appear — because CMS publishes NCCI edits
    as explicit code-to-code pairs that are updated quarterly.

Procedure families supported (matching the plan's target procedure list):
  Mohs, Excision (Benign / Malignant), Closure (Simple / Layered / Complex),
  ATT, Biopsy, Shave Removal, Destruction (all subtypes), Skin Tags,
  ED&C / Debridement, Nail Procedures, Intra-lesional Injections, PDT,
  XTRAC, SRT, IPL, Chemical Peels, Fillers, Botox — and any future
  procedure added to the CSV under a recognised proName.
"""

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from config.constants import BUNDLED_PAIRS
from services.knowledge_base import kb
from services.procedure_models import Family


# ─────────────────────────────────────────────────────────────────────────────
# PROCEDURE FAMILY DEFINITIONS
#
# Maps a clinical category name → set of proName values in proCodeList.csv.
#
# This is the ONLY place that links clinical category names to CSV data.
# All conflict rules reference these family names.
# No CPT code numbers appear here.
# ─────────────────────────────────────────────────────────────────────────────

# Maps Family constant → set of proName values in proCodeList.csv.
# Family constants come from procedure_models.py — one definition, used everywhere.
PROCEDURE_FAMILIES: Dict[str, frozenset] = {

    Family.BIOPSY: frozenset({"Biopsy"}),

    Family.SHAVE_REMOVAL: frozenset({"Shave Removal"}),

    Family.EXCISION: frozenset({
        "Excision Benign Lesion & Margins",
        "Excision Malignant Lesion & Margins",
        "Excision Non Skin",
    }),

    Family.MOHS: frozenset({
        "MOHS Micrographic Surgery",
        "MOHS Additional Tissue Blocks",
    }),

    Family.SIMPLE_CLOSURE: frozenset({"Simple Closure"}),
    Family.INTERMEDIATE_CLOSURE: frozenset({"Layered Closure"}),
    Family.COMPLEX_CLOSURE: frozenset({"Complex Closure"}),

    # Supergroup — all closure types; referenced by conflict rules that
    # apply regardless of closure sub-type (e.g., ATT includes any closure)
    "closure_any": frozenset({
        "Simple Closure",
        "Layered Closure",
        "Complex Closure",
    }),

    Family.ADJACENT_TRANSFER: frozenset({"Adjacent Tissue Transfer"}),

    Family.SKIN_GRAFT: frozenset({
        "Graft Full Thickness",
        "Pinch Graft",
    }),

    Family.DESTRUCTION: frozenset({
        "Destruction Benign",
        "Destruction Premalignant Lesion",
        "Destruction Malignant Lesion",
        "Destruction Vascular Proliferative Lesion",
        "Acne Cryotherapy",
        "Chemical Cauterization Granulation Tissue",
    }),

    Family.SKIN_TAG: frozenset({"Tag Destruction"}),

    Family.DEBRIDEMENT: frozenset({"Debridement"}),

    Family.NAIL: frozenset({
        "Avulsion of the Nail Plate",
        "Excision Nail/Matrix",
    }),

    Family.INTRALESIONAL_INJ: frozenset({"Intra-lesional Injection"}),

    Family.PDT: frozenset({
        "Photodynamic Therapy",
        "Blue Light Application",
        "Levulan Application",
    }),

    Family.XTRAC: frozenset({
        "Xtrac Laser Treatment",
        "Narrow Band UVB Phototherapy",
    }),

    Family.SRT: frozenset({
        "Surface radiation therapy (SRT); superficial delivery",
        "Surface radiation therapy (SRT); orthovoltage delivery",
        "Surface radiation therapy (SRT); planning",
        "Surface radiation therapy (SRT); ultrasound guidance",
    }),

    Family.IPL: frozenset({"Intense Pulsed Light"}),

    Family.CHEMICAL_PEEL: frozenset({
        "Chemical Peel",
        "Chemical Peel Epidermal",
        "Chemical Peel Dermal",
        "Acne Chemical Exfoliation",
    }),

    Family.FILLER: frozenset({
        "Filler",
        "Filler Material",
        "Belotero",
        "Juvederm",
        "Radiesse",
        "Sculptra",
        "Human Cadaver-Derived Implant",
        "Implant (Autologous)",
        "Injectable Micro-Implant",
        "Semi-Permanet Bio-Catalyst Filler",
    }),

    Family.BOTOX: frozenset({
        "Botulinum Toxin Type A",
        "Neuromodulator",
    }),

    Family.INCISION_DRAINAGE: frozenset({
        "Incision & Drainage",
        "Incision & drainage, complex, postoperative wound",
        "Incision and drainage of hematoma, seroma or fluid",
    }),

    Family.LASER: frozenset({"Laser Treatment"}),
}


# ─────────────────────────────────────────────────────────────────────────────
# CONFLICT RULES
#
# Each rule is:
#   (secondary_family, primary_family, action, message_template)
#
# secondary_family: the procedure family that loses when both are at the same site
# primary_family:   the procedure family that wins (kept)
# action:           "reject" (hard) or "review" (soft flag)
# message_template: audit message; {secondary} and {primary} are code numbers
#
# Rules are checked BIDIRECTIONALLY — (a, b) and (b, a) are both tested.
# The family whose proName matches secondary_family is rejected/flagged.
# ─────────────────────────────────────────────────────────────────────────────

ConflictRule = Tuple[str, str, str, str]

CONFLICT_RULES: List[ConflictRule] = [

    # Shave Removal + Biopsy — same lesion: bill biopsy only
    (Family.SHAVE_REMOVAL, Family.BIOPSY, "reject",
     "REJECTED {secondary} (shave removal): cannot be billed with biopsy {primary} "
     "on the same lesion. Bill only the biopsy."),

    # Biopsy + Excision — same lesion: bill excision only
    (Family.BIOPSY, Family.EXCISION, "reject",
     "REJECTED {secondary} (biopsy): cannot be billed with excision {primary} "
     "on the same lesion. Bill only the excision."),

    # Shave Removal + Excision — same lesion: bill excision only
    (Family.SHAVE_REMOVAL, Family.EXCISION, "reject",
     "REJECTED {secondary} (shave removal): cannot be billed with excision {primary} "
     "on the same lesion. Bill only the excision."),

    # Excision + Mohs — same lesion: bill Mohs only
    (Family.EXCISION, Family.MOHS, "reject",
     "REJECTED {secondary} (excision): cannot be billed with Mohs surgery {primary} "
     "on the same lesion. Bill Mohs only."),

    # Biopsy + Mohs — same lesion: bill Mohs only
    (Family.BIOPSY, Family.MOHS, "reject",
     "REJECTED {secondary} (biopsy): cannot be billed with Mohs surgery {primary} "
     "on the same lesion. Bill Mohs only."),

    # Any Closure + ATT — same defect: ATT includes closure
    ("closure_any", Family.ADJACENT_TRANSFER, "reject",
     "REJECTED {secondary} (closure): adjacent tissue transfer {primary} includes "
     "closure. Do not bill a separate closure for the same defect."),

    # Destruction + Excision — same lesion: excision is definitive
    (Family.DESTRUCTION, Family.EXCISION, "reject",
     "REJECTED {secondary} (destruction): cannot be billed with excision {primary} "
     "on the same lesion. Bill only the excision."),

    # Destruction + Mohs — same lesion: Mohs is definitive
    (Family.DESTRUCTION, Family.MOHS, "reject",
     "REJECTED {secondary} (destruction): cannot be billed with Mohs surgery "
     "{primary} on the same lesion. Bill Mohs only."),

    # Skin Tag + Excision — same lesion: flag for review
    (Family.SKIN_TAG, Family.EXCISION, "review",
     "REVIEW {secondary} (skin tag removal): verify this is a separate lesion "
     "from excision {primary}. Cannot bill both for the same lesion."),
]


# ─────────────────────────────────────────────────────────────────────────────
# RUNTIME FAMILY LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def _procedure_families(code: str) -> frozenset:
    """
    Return ALL procedure families this CPT code belongs to.

    A code can belong to multiple families simultaneously — for example, a
    benign excision belongs to both "Excision Benign" (specific) and
    "Excision" (supergroup).  Conflict rules can reference either the specific
    or the supergroup family; both will match correctly.

    No CPT code numbers are used here — only proName from the CSV.
    """
    cpt = kb.get_cpt(code)
    if not cpt or not cpt.pro_name:
        return frozenset()

    pro_name = cpt.pro_name
    result = set()
    for family_name, pro_names in PROCEDURE_FAMILIES.items():
        if pro_name in pro_names:
            result.add(family_name)
    return frozenset(result)


# ─────────────────────────────────────────────────────────────────────────────
# NCCI BUNDLED PAIRS
# ─────────────────────────────────────────────────────────────────────────────

def _check_ncci_pairs(
    cpt_codes: List[Dict],
    llm_output: Dict,
) -> List[Dict]:
    """
    Enforce NCCI Procedure-to-Procedure (PTP) edits from BUNDLED_PAIRS.

    BUNDLED_PAIRS uses specific CPT codes because CMS publishes NCCI edits
    as explicit code pairs — they are not procedure-family rules.

    Behaviour:
      - Secondary in BUNDLED_PAIRS + paired primary present + same site → enforce.
      - Modifier -59 already on secondary → flag for review (distinct site claimed).
      - No -59 → reject secondary (bundled into primary).
    """
    present: Dict[str, Dict] = {
        str(c.get("code", "")).strip(): c
        for c in cpt_codes if c.get("code")
    }
    rejected: set = set()

    for secondary_code, primary_set in BUNDLED_PAIRS.items():
        if secondary_code not in present:
            continue

        conflicts = primary_set & set(present.keys())
        if not conflicts:
            continue

        secondary_cpt = present[secondary_code]
        same_site_conflicts = {
            p for p in conflicts
            if _same_or_unknown_site(secondary_cpt, present[p])
        }
        if not same_site_conflicts:
            continue

        has_59 = str(secondary_cpt.get("modifier", "") or "").strip() == "59"

        if has_59:
            _add_flag(
                llm_output,
                f"REVIEW {secondary_code}: NCCI bundled with {sorted(same_site_conflicts)} "
                f"at same site — modifier -59 present. Verify distinct lesion documentation.",
            )
            logger.info(
                f"NCCI pair {secondary_code}/{sorted(same_site_conflicts)}: "
                f"-59 present — flagged for review"
            )
        else:
            rejected.add(secondary_code)
            _add_flag(
                llm_output,
                f"REJECTED {secondary_code}: NCCI bundled with {sorted(same_site_conflicts)}. "
                f"Add modifier -59 only if performed on a distinctly separate lesion.",
            )
            logger.warning(
                f"NCCI bundle rejected: {secondary_code} conflicts with "
                f"{sorted(same_site_conflicts)}"
            )

    return [c for c in cpt_codes if str(c.get("code", "")).strip() not in rejected]


# ─────────────────────────────────────────────────────────────────────────────
# SAME-SITE CONFLICT CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _check_same_site_conflicts(
    cpt_codes: List[Dict],
    llm_output: Dict,
) -> List[Dict]:
    """
    Check every pair of CPT codes at the same site against CONFLICT_RULES.

    Procedure family membership is determined via proName from the KnowledgeBase.
    No CPT code numbers appear in this function.
    """
    present: Dict[str, Dict] = {
        str(c.get("code", "")).strip(): c
        for c in cpt_codes if c.get("code")
    }
    code_list = list(present.keys())
    rejected: set = set()

    for i, code_a in enumerate(code_list):
        if code_a in rejected:
            continue

        families_a = _procedure_families(code_a)

        for code_b in code_list[i + 1:]:
            if code_b in rejected:
                continue

            cpt_a = present[code_a]
            cpt_b = present[code_b]

            if not _confirmed_same_site(cpt_a, cpt_b):
                continue

            families_b = _procedure_families(code_b)

            for secondary_family, primary_family, action, msg_tmpl in CONFLICT_RULES:
                # Check a→b direction (code_a is secondary, code_b is primary)
                if secondary_family in families_a and primary_family in families_b:
                    s_code, p_code = code_a, code_b
                # Check b→a direction (code_b is secondary, code_a is primary)
                elif secondary_family in families_b and primary_family in families_a:
                    s_code, p_code = code_b, code_a
                else:
                    continue

                message = msg_tmpl.format(secondary=s_code, primary=p_code)
                _add_flag(llm_output, message)

                if action == "reject":
                    rejected.add(s_code)
                    logger.warning(
                        f"Lesion conflict [{secondary_family} + {primary_family}]: "
                        f"rejected {s_code} (same site as {p_code})"
                    )
                else:
                    logger.info(
                        f"Lesion conflict review [{secondary_family} + {primary_family}]: "
                        f"{s_code} same site as {p_code}"
                    )
                break  # one rule match per pair is enough

    return [c for c in cpt_codes if str(c.get("code", "")).strip() not in rejected]


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def validate_lesion_conflicts(
    llm_output: Dict[str, Any],
    candidates: List[Dict],
) -> Dict[str, Any]:
    """
    Run all lesion-level conflict checks.

    Called from validation_engine.validate_codes() after core billing
    integrity rules have run.

    Returns modified llm_output with conflicting codes removed or flagged.
    """
    try:
        codes     = llm_output.get("codes", {})
        cpt_codes = list(codes.get("cpt_codes", []))

        original_count = len(cpt_codes)

        cpt_codes = _check_ncci_pairs(cpt_codes, llm_output)
        cpt_codes = _check_same_site_conflicts(cpt_codes, llm_output)

        rejected = original_count - len(cpt_codes)
        if rejected:
            logger.info(
                f"LesionValidator: {rejected} code(s) rejected, "
                f"{len(cpt_codes)} remain"
            )
        else:
            logger.info("LesionValidator: no same-site conflicts detected")

        llm_output["codes"]["cpt_codes"] = cpt_codes
        return llm_output

    except Exception as e:
        logger.exception(f"LesionValidator failed (non-fatal): {e}")
        return llm_output


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _confirmed_same_site(cpt_a: Dict, cpt_b: Dict) -> bool:
    """
    True only when BOTH codes carry a site_id AND they match.

    Used for clinical conflict rules (biopsy+shave, excision+Mohs, etc.).
    We only reject a code when we have positive evidence that both procedures
    are at the same anatomical site.  A missing site_id means "site unknown" —
    we do NOT assume same-site, because a false rejection (losing legitimate
    revenue) is worse than a missed conflict (which human review can catch).
    """
    site_a = str(cpt_a.get("site_id", "")).strip()
    site_b = str(cpt_b.get("site_id", "")).strip()
    if not site_a or not site_b:
        return False   # unknown — do not reject
    return site_a == site_b


def _same_or_unknown_site(cpt_a: Dict, cpt_b: Dict) -> bool:
    """
    True when codes share a site_id OR when site is unknown for either.

    Used only for NCCI bundled-pair enforcement, where the billing compliance
    rule applies regardless of site certainty (the coder must provide -59 to
    claim a distinct service; absence of -59 means bundled).
    """
    site_a = str(cpt_a.get("site_id", "")).strip()
    site_b = str(cpt_b.get("site_id", "")).strip()
    if not site_a or not site_b:
        return True   # conservative for NCCI compliance
    return site_a == site_b


def _add_flag(llm_output: Dict, message: str) -> None:
    llm_output.setdefault("audit_flags", [])
    if message not in llm_output["audit_flags"]:
        llm_output["audit_flags"].append(message)

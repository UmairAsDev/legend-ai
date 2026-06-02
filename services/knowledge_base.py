# services/knowledge_base.py
"""
Phase 2 — CPT Knowledge Service.

Single source of truth for all CPT, modifier, and E/M metadata.

Loads proCodeList.csv, modifierList.csv, and enmCodeList.csv once at process
startup (lazy, on first access).  All selectors and validators use this service.
No other module should open these CSV files directly.

Usage:
    from services.knowledge_base import kb

    kb.is_addon("12032")       # True / False
    kb.parent_code("12032")    # "12031" or None
    kb.requires_laterality("27447")  # True / False
    kb.get_codes_by_name("Biopsy")   # List[CPTCode]
"""

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

_DATA_DIR = Path(__file__).parent.parent / "data"


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CPTCode:
    """
    CPT code — only fields from the approved proCodeList.csv columns:
    proCode, codeDesc, proName, associatedWithProCode,
    minQty, maxQty, minSize, maxSize, chargePerUnit, addOn
    """
    code: str
    description: str
    pro_name: str
    min_size: float
    max_size: float
    min_qty: int
    max_qty: int
    parent_code: Optional[str]   # associatedWithProCode — links add-on to primary
    charge_per_unit: bool        # chargePerUnit
    _addon_flag: bool            # addOn column (used for closure add-ons and similar)

    @property
    def is_addon(self) -> bool:
        """
        True when the addOn flag is set OR when a parent code is referenced.
        Both signals are checked so Mohs additional-stage codes (which have
        associatedWithProCode but addOn=0 in the CSV) are correctly identified.
        """
        return self._addon_flag or (self.parent_code is not None)


@dataclass(frozen=True)
class ModifierCode:
    code: str
    description: str
    det_description: str
    is_em_modifier: bool             # enmModifier=1 means E/M-only modifier


@dataclass(frozen=True)
class EMCode:
    code: str
    description: str
    em_type: str                     # newPat | estPat | consult | other
    em_level: int
    encounter_time: int              # minimum documented time in minutes
    is_active: bool


# ─────────────────────────────────────────────────────────────────────────────
# KNOWLEDGE BASE
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeBase:
    """
    Singleton knowledge base.  Import and use the module-level `kb` instance.
    Thread-safe for read access after load() completes.
    """

    def __init__(self) -> None:
        self._cpt_by_code:   Dict[str, CPTCode]       = {}
        self._cpt_by_name:   Dict[str, List[CPTCode]] = {}   # key: pro_name.lower()
        self._modifiers:     Dict[str, ModifierCode]  = {}
        self._em_codes:      List[EMCode]             = []
        self._loaded = False

    # ── Lazy loader ──────────────────────────────────────────────────────────

    def load(self) -> None:
        if self._loaded:
            return
        self._load_pro_codes()
        self._load_modifiers()
        self._load_em_codes()
        self._loaded = True
        logger.info(
            f"KnowledgeBase ready: "
            f"{len(self._cpt_by_code)} CPT codes, "
            f"{len(self._modifiers)} modifiers, "
            f"{len(self._em_codes)} E/M codes"
        )

    # ── CPT loader ───────────────────────────────────────────────────────────

    def _load_pro_codes(self) -> None:
        path = _DATA_DIR / "proCodeList.csv"
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = str(row.get("proCode", "")).strip()
                if not code:
                    continue
                if str(row.get("deleted", "0")).strip() == "1":
                    continue

                cpt = CPTCode(
                    code=code,
                    description=str(row.get("codeDesc", "")).strip(),
                    pro_name=str(row.get("proName", "")).strip(),
                    min_size=_f(row.get("minSize")),
                    max_size=_f(row.get("maxSize")),
                    min_qty=_i(row.get("minQty")),
                    max_qty=_i(row.get("maxQty")),
                    parent_code=_normalise_assoc(row.get("associatedWithProCode")),
                    charge_per_unit=str(row.get("chargePerUnit", "0")).strip() == "1",
                    _addon_flag=str(row.get("addOn", "0")).strip() == "1",
                )

                self._cpt_by_code[code] = cpt
                name_key = cpt.pro_name.lower()
                self._cpt_by_name.setdefault(name_key, []).append(cpt)

        logger.debug(f"KnowledgeBase: {len(self._cpt_by_code)} CPT codes loaded from proCodeList.csv")

    # ── Modifier loader ──────────────────────────────────────────────────────

    def _load_modifiers(self) -> None:
        path = _DATA_DIR / "modifierList.csv"
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = str(row.get("modifier", "")).strip()
                if not code or str(row.get("deleted", "0")).strip() == "1":
                    continue
                self._modifiers[code] = ModifierCode(
                    code=code,
                    description=str(row.get("modifierDesc", "")).strip(),
                    det_description=str(row.get("modifierDetDesc", "")).strip(),
                    is_em_modifier=str(row.get("enmModifier", "0")).strip() == "1",
                )

        logger.debug(f"KnowledgeBase: {len(self._modifiers)} modifiers loaded from modifierList.csv")

    # ── E/M loader ───────────────────────────────────────────────────────────

    def _load_em_codes(self) -> None:
        from datetime import date

        path = _DATA_DIR / "enmCodeList.csv"
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = str(row.get("enmCode", "")).strip()
                if not code or str(row.get("deleted", "0")).strip() == "1":
                    continue

                # Parse expiry date
                active = True
                expire_raw = str(row.get("expireDate", "12/31/2050")).strip()
                try:
                    parts = expire_raw.split("/")
                    expire_iso = f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                    active = expire_iso >= date.today().isoformat()
                except Exception:
                    pass

                self._em_codes.append(EMCode(
                    code=code,
                    description=str(row.get("enmCodeDesc", "")).strip(),
                    em_type=str(row.get("enmType", "")).strip(),
                    em_level=_i(row.get("enmLevel")),
                    encounter_time=_i(row.get("encounterTime")),
                    is_active=active,
                ))

        logger.debug(f"KnowledgeBase: {len(self._em_codes)} E/M codes loaded from enmCodeList.csv")

    # ─────────────────────────────────────────────────────────────────────────
    # CPT QUERIES
    # ─────────────────────────────────────────────────────────────────────────

    def get_cpt(self, code: str) -> Optional[CPTCode]:
        """Return the CPTCode for this code string, or None if unknown."""
        self.load()
        return self._cpt_by_code.get(str(code).strip())

    def get_codes_by_name(self, pro_name: str) -> List[CPTCode]:
        """Return all CPT codes whose proName matches (case-insensitive)."""
        self.load()
        return list(self._cpt_by_name.get(pro_name.lower().strip(), []))

    def is_addon(self, code: str) -> bool:
        """True when this code is an add-on (addOn flag OR has a parent via associatedWithProCode)."""
        cpt = self.get_cpt(code)
        return cpt.is_addon if cpt else False

    def parent_code(self, code: str) -> Optional[str]:
        """Return the primary code this add-on requires (associatedWithProCode), or None."""
        cpt = self.get_cpt(code)
        return cpt.parent_code if cpt else None

    def get_size_range(self, code: str) -> Tuple[float, float]:
        """Return (min_size, max_size) for this code, or (0.0, 0.0) if unknown."""
        cpt = self.get_cpt(code)
        return (cpt.min_size, cpt.max_size) if cpt else (0.0, 0.0)

    def get_qty_range(self, code: str) -> Tuple[int, int]:
        """Return (min_qty, max_qty) for this code."""
        cpt = self.get_cpt(code)
        return (cpt.min_qty, cpt.max_qty) if cpt else (1, 1)

    def charge_per_unit(self, code: str) -> bool:
        """True when this CPT is billed per unit (chargePerUnit=1)."""
        cpt = self.get_cpt(code)
        return cpt.charge_per_unit if cpt else False

    # ─────────────────────────────────────────────────────────────────────────
    # MODIFIER QUERIES
    # ─────────────────────────────────────────────────────────────────────────

    def get_modifier(self, code: str) -> Optional[ModifierCode]:
        """Return modifier metadata or None if not in modifierList."""
        self.load()
        return self._modifiers.get(str(code).strip())

    def is_valid_modifier(self, code: str) -> bool:
        """True when this modifier code exists in modifierList.csv."""
        self.load()
        return str(code).strip() in self._modifiers

    def is_em_modifier(self, code: str) -> bool:
        """True when this modifier is designated for E/M codes (enmModifier=1)."""
        mod = self.get_modifier(code)
        return mod.is_em_modifier if mod else False

    # ─────────────────────────────────────────────────────────────────────────
    # E/M QUERIES
    # ─────────────────────────────────────────────────────────────────────────

    def get_em_codes(self, em_type: str) -> List[EMCode]:
        """Return active E/M codes for the given patient type, sorted by level."""
        self.load()
        return sorted(
            [c for c in self._em_codes if c.em_type == em_type and c.is_active],
            key=lambda c: c.em_level,
        )


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL SINGLETON  —  import `kb` everywhere
# ─────────────────────────────────────────────────────────────────────────────

kb = KnowledgeBase()


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_assoc(val) -> Optional[str]:
    if not val:
        return None
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s if s not in ("", "0", "None", "null") else None


def _f(val) -> float:
    try:
        result = float(val) if val not in (None, "", "nan") else 0.0
        return 0.0 if math.isnan(result) or math.isinf(result) else result
    except (ValueError, TypeError):
        return 0.0


def _i(val) -> int:
    try:
        return int(float(val)) if val not in (None, "", "nan") else 1
    except (ValueError, TypeError):
        return 1

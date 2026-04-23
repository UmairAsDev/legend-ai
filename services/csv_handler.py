# services/csv_handler.py

import pandas as pd
from loguru import logger


class CSVHandler:

    # =========================
    # 🔹 COMMON CLEANERS
    # =========================
    def _safe_str(self, value):
        if pd.isna(value):
            return None
        return str(value).strip()

    def _safe_int(self, value):
        try:
            return int(value) if pd.notna(value) else None
        except Exception:
            return None

    def _safe_float(self, value):
        try:
            return float(value) if pd.notna(value) else None
        except Exception:
            return None

    # =========================
    # 🔹 CPT LOADER
    # =========================
    def load_cpt(self, path: str):
        try:
            df = pd.read_csv(path)

            results = []

            for _, row in df.iterrows():

                pro_code = self._safe_str(row.get("proCode"))

                if not pro_code:
                    continue

                results.append({
                    "proCode": pro_code,
                    "codeDesc": self._safe_str(row.get("codeDesc")),
                    "associatedWithProCode": self._safe_str(row.get("associatedWithProCode")),
                    "proName": self._safe_str(row.get("proName")),
                    "minQty": self._safe_int(row.get("minQty")),
                    "maxQty": self._safe_int(row.get("maxQty")),
                    "minSize": self._safe_str(row.get("minSize")),
                    "maxSize": self._safe_str(row.get("maxSize")),
                    "chargePerUnit": self._safe_float(row.get("chargePerUnit")),
                })

            logger.info(f"✅ CPT loaded: {len(results)} records")
            return results

        except Exception as e:
            logger.exception(f"❌ Failed loading CPT CSV: {e}")
            raise

    # =========================
    # 🔹 E/M LOADER
    # =========================
    def load_em(self, path: str):
        try:
            df = pd.read_csv(path)

            results = []

            for _, row in df.iterrows():

                code = self._safe_str(row.get("enmCode"))

                if not code:
                    continue

                results.append({
                    "enmCode": code,
                    "enmCodeDesc": self._safe_str(row.get("enmCodeDesc")),
                    "encounterTime": self._safe_str(row.get("encounterTime")),
                    "enmLevel": self._safe_int(row.get("enmLevel")),
                })

            logger.info(f"✅ E/M loaded: {len(results)} records")
            return results

        except Exception as e:
            logger.exception(f"❌ Failed loading EM CSV: {e}")
            raise

    # =========================
    # 🔹 MODIFIER LOADER
    # =========================
    def load_modifiers(self, path: str):
        try:
            df = pd.read_csv(path)

            results = []

            for _, row in df.iterrows():

                code = self._safe_str(row.get("modifier"))

                if not code:
                    continue

                results.append({
                    "modifier": code,
                    "modifierDesc": self._safe_str(row.get("modifierDesc")),
                    "modifierDetDesc": self._safe_str(row.get("modifierDetDesc")),
                })

            logger.info(f"✅ Modifiers loaded: {len(results)} records")
            return results

        except Exception as e:
            logger.exception(f"❌ Failed loading Modifier CSV: {e}")
            raise
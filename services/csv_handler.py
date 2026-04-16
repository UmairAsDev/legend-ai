import pandas as pd
from loguru import logger


class CSVHandler:

    def load_cpt(self, path: str):
        df = pd.read_csv(path)

        results = []

        for _, row in df.iterrows():
            code = str(row.get("proCode")).strip()

            desc = f"{row.get('codeDesc', '')} {row.get('proName', '')}".strip()

            if code and code != "nan":
                results.append({
                    "code": code,
                    "description": desc
                })

        logger.info(f"CPT loaded: {len(results)}")
        return results


    def load_em(self, path: str):
        df = pd.read_csv(path)

        results = []

        for _, row in df.iterrows():
            code = str(row.get("enmCode")).strip()
            desc = str(row.get("enmCodeDesc")).strip()

            if code and code != "nan":
                results.append({
                    "code": code,
                    "description": desc
                })

        logger.info(f"E/M loaded: {len(results)}")
        return results


    def load_modifiers(self, path: str):
        df = pd.read_csv(path)

        results = []

        for _, row in df.iterrows():
            code = str(row.get("modifier")).strip()

            desc = f"{row.get('modifierDesc', '')} {row.get('modifierDetDesc', '')}".strip()

            if code and code != "nan":
                results.append({
                    "code": code,
                    "description": desc
                })

        logger.info(f"Modifiers loaded: {len(results)}")
        return results
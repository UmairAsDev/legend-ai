# services/reranker.py

from loguru import logger


class Reranker:

    def rerank(self, candidates, note_text, top_k=25):
        """
        Improved reranking:
        - lexical match boost
        - semantic distance penalty
        - type-aware boosting (CPT > EM > Modifier)
        """

        try:
            logger.info("⚡ Reranking candidates...")

            scored = []

            note_text_lower = note_text.lower()

            for c in candidates:
                score = 0.0

                code = str(c.get("code", "")).lower()
                desc = str(c.get("description", "")).lower()
                distance = float(c.get("distance", 1.0))
                ctype = c.get("type", "")

                # 🔹 Code match boost
                if code and code in note_text_lower:
                    score += 3.0

                # 🔹 Description match
                if desc and desc in note_text_lower:
                    score += 2.0

                # 🔹 Type priority
                if ctype == "cpt":
                    score += 1.5
                elif ctype == "em":
                    score += 1.0
                elif ctype == "modifier":
                    score += 0.5

                # 🔹 Distance penalty (core signal)
                score -= distance

                scored.append((score, c))

            # 🔹 Sort descending
            scored.sort(key=lambda x: x[0], reverse=True)

            top_results = [c[1] for c in scored[:top_k]]

            logger.info(f"✅ Reranked top {len(top_results)} results")

            return top_results

        except Exception as e:
            logger.exception(f"❌ Reranking failed: {e}")
            raise
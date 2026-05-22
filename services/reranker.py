from loguru import logger


class Reranker:

    def rerank(self, candidates, state, top_k=25):
        """
        Advanced reranking:
        - Focused on biopsyNotes / mohsNotes
        - Section-aware boosting (A, B, C...)
        - Keyword intent boosting (biopsy vs mohs)
        - Semantic distance penalty
        - Type-aware priority
        """

        try:
            logger.info("⚡ Reranking candidates (biopsy/mohs aware)...")

            scored = []

            cleaned_note = state.get("cleaned_note", {})
            parsed = state.get("parsed", {})

            biopsy_text = (cleaned_note.get("biopsyNotes") or "").lower()
            mohs_text = (cleaned_note.get("mohsNotes") or "").lower()
            fallback_text = state.get("query_text", "").lower()

            # -------------------------
            # 🔹 Determine focus text
            # -------------------------
            if biopsy_text:
                focus_text = biopsy_text
                intent = "biopsy"
            elif mohs_text:
                focus_text = mohs_text
                intent = "mohs"
            else:
                focus_text = fallback_text
                intent = "general"

            biopsy_count = parsed.get("biopsy_count", 0)

            for c in candidates:
                score = 0.0

                code = str(c.get("code", "")).lower()
                desc = str(c.get("description", "")).lower()
                pro_name = str(c.get("pro_name", "")).lower()

                distance = float(c.get("distance", 1.0))
                ctype = c.get("type", "")

                combined_text = f"{desc} {pro_name}"

                # -------------------------
                # 🔹 Intent Matching Boost
                # -------------------------
                if intent == "biopsy":
                    if "biopsy" in combined_text:
                        score += 5.0
                elif intent == "mohs":
                    if "mohs" in combined_text:
                        score += 5.0

                # -------------------------
                # 🔹 Strong lexical match (focused text)
                # -------------------------
                if code and code in focus_text:
                    score += 3.5

                if desc and any(word in focus_text for word in desc.split()[:5]):
                    score += 2.5

                # -------------------------
                # 🔹 Section-aware boost (multi-biopsy A/B/C/D)
                # -------------------------
                if intent == "biopsy" and biopsy_count > 1:
                    if "add-on" in combined_text or "each additional" in combined_text:
                        score += 3.0   # e.g., 11103
                    else:
                        score += 1.5   # base biopsy code (11102)

                # -------------------------
                # 🔹 Procedure method boost (shave, punch, excision)
                # -------------------------
                if "shave" in focus_text and "shave" in combined_text:
                    score += 2.0
                if "punch" in focus_text and "punch" in combined_text:
                    score += 2.0
                if "excision" in focus_text and "excision" in combined_text:
                    score += 2.0

                # -------------------------
                # 🔹 Type priority
                # -------------------------
                if ctype == "cpt":
                    score += 2.0
                elif ctype == "em":
                    score += 0.8
                elif ctype == "modifier":
                    score += 0.3

                # -------------------------
                # 🔹 Distance penalty (core semantic signal)
                # -------------------------
                score -= distance

                scored.append((score, c))

            # -------------------------
            # 🔹 Sort descending
            # -------------------------
            scored.sort(key=lambda x: x[0], reverse=True)

            top_results = [c[1] for c in scored[:top_k]]

            logger.info(f"✅ Reranked top {len(top_results)} results")

            return top_results

        except Exception as e:
            logger.exception(f"❌ Reranking failed: {e}")
            raise
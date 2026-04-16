# services/reranker.py

class Reranker:

    def rerank(self, candidates, note_text):
        # lightweight semantic boost
        scored = []

        for c in candidates:
            score = 0

            if c["code"] in note_text:
                score += 2

            if c["description"].lower() in note_text.lower():
                score += 1

            score -= c["distance"]

            scored.append((score, c))

        scored.sort(reverse=True, key=lambda x: x[0])

        return [c[1] for c in scored[:10]]
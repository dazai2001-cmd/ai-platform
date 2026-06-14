from core.config.settings import settings


class Retriever:
    def __init__(self, embedder, store):
        self.embedder = embedder
        self.store = store

    def search(self, query: str, k: int = None) -> list[dict]:
        k = k or settings.TOP_K
        vec = self.embedder.embed(query)
        return self.store.search(vec, k)

    def format_context(self, results: list[dict], max_chars: int = 4000) -> str:
        parts = []
        total = 0
        for r in results:
            meta = r["metadata"]
            entry = f"[SOURCE: {meta.get('source', 'unknown')}]\n{meta.get('text', '')}"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)
        return "\n\n".join(parts) if parts else "No relevant context found."

#!/usr/bin/env python
"""Quick test of the RAG pipeline without the API."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infrastructure.embeddings.embedder import Embedder
from infrastructure.vectorstore.faiss_store import FAISSStore
from application.retrieval.retriever import Retriever
from domain.rag.pipeline import QAPipeline

def main():
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What is in the knowledge base?"

    embedder = Embedder()
    store = FAISSStore(dim=embedder.dim)
    store.load()

    print(f"\nIndex size: {store.total} chunks")
    if store.total == 0:
        print("No data — run: python scripts/ingest_pdfs.py first")
        return

    retriever = Retriever(embedder, store)
    pipeline = QAPipeline(retriever)

    print(f"\nQuery: {query}\n")
    results = retriever.search(query)
    print("Top matches:")
    for r in results:
        print(f"  [{r['score']:.2f}] {r['metadata']['source']} — {r['metadata']['text'][:80]}...")

    print("\nGenerating answer...")
    result = pipeline.ask(query)
    print(f"\nAnswer ({result['model']}):\n{result['answer']}")

if __name__ == "__main__":
    main()

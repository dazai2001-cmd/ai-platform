#!/usr/bin/env python
"""
Ingest PDFs from the command line.

Usage:
    python scripts/ingest_pdfs.py                  # ingest data/raw/
    python scripts/ingest_pdfs.py path/to/file.pdf  # ingest single file
    python scripts/ingest_pdfs.py path/to/folder/   # ingest folder
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from infrastructure.embeddings.embedder import Embedder
from infrastructure.vectorstore.faiss_store import FAISSStore
from application.ingestion.ingestion_service import IngestionService

def main():
    embedder = Embedder()
    store = FAISSStore(dim=embedder.dim)
    store.load()
    service = IngestionService(embedder, store)

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/raw")

    if target.is_file():
        count = service.ingest_pdf(str(target))
        print(f"✓ {target.name}: {count} chunks")
    elif target.is_dir():
        results = service.ingest_folder(str(target))
        for name, count in results.items():
            print(f"{'✓' if isinstance(count, int) else '✗'} {name}: {count}")
        total = sum(c for c in results.values() if isinstance(c, int))
        print(f"\nTotal: {total} chunks ingested")
    else:
        print(f"Error: {target} not found")
        sys.exit(1)

if __name__ == "__main__":
    main()

import re
from core.config.settings import settings


def chunk_text(text: str, chunk_size: int = None, overlap: int = None) -> list[str]:
    """
    Split text into overlapping chunks.
    Breaks at word boundaries to avoid cutting mid-word.
    Keeps short non-empty notes as a single chunk.
    """
    chunk_size = chunk_size or settings.CHUNK_SIZE
    overlap = overlap or settings.CHUNK_OVERLAP

    text = re.sub(r"\s+", " ", text).strip()

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        # Break at word boundary
        if end < len(text):
            last_space = chunk.rfind(" ")
            if last_space != -1:
                chunk = chunk[:last_space]

        chunk = chunk.strip()

        if chunk:
            chunks.append(chunk)

        start += chunk_size - overlap

    return chunks

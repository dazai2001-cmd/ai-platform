import os
import ipaddress
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
import requests

from infrastructure.embeddings.embedder import Embedder
from infrastructure.vectorstore.faiss_store import FAISSStore
from application.ingestion.chunker import chunk_text
from core.config.settings import settings


_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AIPlatform/1.0; +http://localhost)"
}


class IngestionService:
    def __init__(self, embedder: Embedder, store: FAISSStore):
        self.embedder = embedder
        self.store = store

    def ingest_pdf(self, path: str, source: str = None, extra: dict = None) -> int:
        text = self._extract_pdf_text(path)
        if not text.strip():
            text = self._ocr_pdf_to_text(path)
        return self._ingest_text(text, source=source or os.path.basename(path), extra=extra)

    def ingest_url(self, url: str, extra: dict = None) -> int:
        self._validate_public_url(url)
        html = self._read_limited_url(url)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        return self._ingest_text(text, source=url, extra={"type": "url", **(extra or {})})

    def ingest_text(self, text: str, source: str = "note", extra: dict = None) -> int:
        if len(text) > settings.MAX_TEXT_INGEST_CHARS:
            raise ValueError("Text exceeds the ingestion limit")
        return self._ingest_text(text, source=source, extra={"type": "note", **(extra or {})})

    def ingest_folder(self, folder: str) -> dict:
        results = {}
        for pdf in Path(folder).glob("*.pdf"):
            try:
                results[pdf.name] = self.ingest_pdf(str(pdf))
            except Exception as e:
                results[pdf.name] = f"ERROR: {e}"
        return results

    def _ingest_text(self, text: str, source: str, extra: dict = None) -> int:
        chunks = chunk_text(text)
        if not chunks:
            return 0
        if len(chunks) > settings.MAX_CHUNKS_PER_DOCUMENT:
            raise ValueError(
                f"Document exceeds the {settings.MAX_CHUNKS_PER_DOCUMENT}-chunk limit"
            )

        user_id = (extra or {}).get("user_id", "local")
        reservation = self.store.reserve_user_chunks(
            user_id,
            len(chunks),
            settings.MAX_CHUNKS_PER_USER,
        )
        try:
            vectors = self.embedder.embed_batch(chunks)
            n = min(len(vectors), len(chunks))
            metadata = [
                {"text": chunks[i], "source": source, **(extra or {})}
                for i in range(n)
            ]
            return reservation.commit(vectors[:n], metadata)
        finally:
            # commit() consumes the reservation; this also covers embedding and
            # persistence failures without requiring every caller to clean up.
            reservation.release()

    @staticmethod
    def _extract_pdf_text(path: str) -> str:
        with fitz.open(path) as doc:
            if doc.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported")
            if len(doc) > settings.MAX_PDF_PAGES:
                raise ValueError(f"PDF exceeds the {settings.MAX_PDF_PAGES}-page limit")
            return "\n".join(page.get_text() for page in doc)

    @staticmethod
    def _ocr_pdf_to_text(path: str) -> str:
        executable = shutil.which("ocrmypdf")
        if not executable:
            raise ValueError(
                "No extractable text found in this PDF, and OCR is not installed in the API container."
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "ocr.pdf")
            command = [
                executable,
                "--skip-text",
                "--deskew",
                "--quiet",
                path,
                output_path,
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired as e:
                raise ValueError("OCR took too long for this PDF. Try a smaller file or fewer pages.") from e
            except subprocess.CalledProcessError as e:
                message = (e.stderr or e.stdout or "OCR failed").strip()
                raise ValueError(f"OCR failed for this PDF: {message}") from e

            text = IngestionService._extract_pdf_text(output_path)
            if not text.strip():
                raise ValueError("OCR completed, but no readable text was found in this PDF.")
            return text

    @staticmethod
    def _validate_public_url(url: str):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http and https URLs are allowed")
        if not parsed.hostname:
            raise ValueError("URL must include a hostname")
        host = parsed.hostname.lower()
        if host in _BLOCKED_HOSTS:
            raise ValueError("Local URLs are not allowed")
        try:
            addresses = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise ValueError(f"Could not resolve URL host: {host}") from e
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise ValueError("Private, local, and reserved network URLs are not allowed")

    @staticmethod
    def _read_limited_url(url: str) -> bytes:
        current_url = url
        for _ in range(4):
            IngestionService._validate_public_url(current_url)
            response = requests.get(
                current_url,
                timeout=15,
                stream=True,
                allow_redirects=False,
                headers=_REQUEST_HEADERS,
            )
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("URL redirect did not include a location")
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if content_type and "text/html" not in content_type and "text/plain" not in content_type:
                raise ValueError("URL must return text or HTML content")
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total > settings.MAX_URL_INGEST_BYTES:
                    raise ValueError("URL response is too large")
            data = b"".join(chunks)
            break
        else:
            raise ValueError("URL redirected too many times")
        return data

import os
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename
from core.config.settings import settings

UPLOAD_PATH = Path(settings.UPLOAD_PATH)

ALLOWED_PDF = {"pdf"}
ALLOWED_CV = {"pdf", "docx"}
ALLOWED_DATA = {"csv", "xlsx", "xls"}
PDF_MAGIC = b"%PDF"
XLSX_MAGIC = b"PK\x03\x04"
DOCX_MAGIC = XLSX_MAGIC
XLS_MAGIC = b"\xd0\xcf\x11\xe0"


class UploadTooLargeError(ValueError):
    """Raised when a route-specific upload limit is exceeded."""


def allowed_file(filename: str, allowed: set) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def save_upload(
    file,
    allowed: set,
    *,
    max_bytes: int | None = None,
    limit_name: str = "dataset",
) -> str:
    """Save uploaded file, return path. Raises ValueError if not allowed."""
    if not file or file.filename == "":
        raise ValueError("No file provided")
    if len(file.filename) > 255:
        raise ValueError("Filename is too long")
    if not allowed_file(file.filename, allowed):
        raise ValueError(f"File type not allowed. Allowed: {allowed}")
    _validate_magic(file, file.filename)
    safe_name = secure_filename(file.filename) or "upload"
    filename = f"{uuid.uuid4().hex}_{safe_name}"
    UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    path = str(UPLOAD_PATH / filename)
    try:
        if max_bytes is None:
            file.save(path)
        else:
            written = 0
            with Path(path).open("wb") as destination:
                while chunk := file.stream.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise UploadTooLargeError(
                            f"Upload exceeds the {max_bytes}-byte {limit_name} limit"
                        )
                    destination.write(chunk)
    except Exception:
        remove_upload(path)
        raise
    return path


def remove_upload(path: str | None) -> None:
    """Best-effort cleanup for uploads that are no longer needed."""
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        # Cleanup must not hide the request's actual result or error.
        pass


def _validate_magic(file, filename: str):
    ext = filename.rsplit(".", 1)[1].lower()
    head = file.stream.read(8)
    file.stream.seek(0)
    if ext == "pdf" and not head.startswith(PDF_MAGIC):
        raise ValueError("Invalid PDF file")
    if ext == "docx" and not head.startswith(DOCX_MAGIC):
        raise ValueError("Invalid Word document")
    if ext == "xlsx" and not head.startswith(XLSX_MAGIC):
        raise ValueError("Invalid spreadsheet file")
    if ext == "xls" and not head.startswith(XLS_MAGIC):
        raise ValueError("Invalid spreadsheet file")

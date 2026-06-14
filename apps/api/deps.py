import os
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename
from core.config.settings import settings

UPLOAD_PATH = Path(settings.UPLOAD_PATH)

ALLOWED_PDF = {"pdf"}
ALLOWED_DATA = {"csv", "xlsx", "xls"}
PDF_MAGIC = b"%PDF"
XLSX_MAGIC = b"PK\x03\x04"
XLS_MAGIC = b"\xd0\xcf\x11\xe0"


def allowed_file(filename: str, allowed: set) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def save_upload(file, allowed: set) -> str:
    """Save uploaded file, return path. Raises ValueError if not allowed."""
    if not file or file.filename == "":
        raise ValueError("No file provided")
    if not allowed_file(file.filename, allowed):
        raise ValueError(f"File type not allowed. Allowed: {allowed}")
    _validate_magic(file, file.filename)
    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    path = str(UPLOAD_PATH / filename)
    file.save(path)
    return path


def _validate_magic(file, filename: str):
    ext = filename.rsplit(".", 1)[1].lower()
    head = file.stream.read(8)
    file.stream.seek(0)
    if ext == "pdf" and not head.startswith(PDF_MAGIC):
        raise ValueError("Invalid PDF file")
    if ext == "xlsx" and not head.startswith(XLSX_MAGIC):
        raise ValueError("Invalid spreadsheet file")
    if ext == "xls" and not head.startswith(XLS_MAGIC):
        raise ValueError("Invalid spreadsheet file")

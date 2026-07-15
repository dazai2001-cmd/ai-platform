from __future__ import annotations

import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

import fitz
from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from core.config.settings import settings


_DOCX_MAIN_CONTENT_TYPE = (
    b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_SUPPORTED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MIN_PDF_PAGE_TEXT_CHARACTERS = 32


@dataclass(frozen=True)
class ExtractedCv:
    text: str
    file_type: str
    characters: int
    pages: int | None
    used_ocr: bool


class CvDocumentService:
    """Extract bounded, reviewable text from a PDF or OOXML Word CV."""

    def extract(self, path: str, original_filename: str) -> ExtractedCv:
        extension = Path(original_filename).suffix.lower()
        if extension == ".pdf":
            raw_text, pages, used_ocr = self._extract_pdf(path)
            file_type = "pdf"
        elif extension == ".docx":
            raw_text = self._extract_docx(path)
            pages = None
            used_ocr = False
            file_type = "docx"
        else:
            raise ValueError("CV file type not allowed. Use a PDF or DOCX file.")

        text = self._normalize_text(raw_text)
        if not text:
            raise ValueError("No readable text was found in this CV.")
        if len(text) > settings.MAX_PROMPT_CHARS:
            raise ValueError(
                f"CV text exceeds the {settings.MAX_PROMPT_CHARS}-character limit; "
                "shorten the CV and try again."
            )

        return ExtractedCv(
            text=text,
            file_type=file_type,
            characters=len(text),
            pages=pages,
            used_ocr=used_ocr,
        )

    def _extract_pdf(self, path: str) -> tuple[str, int, bool]:
        text, page_count, has_sparse_page = self._read_pdf_text(path)
        if not has_sparse_page:
            return text, page_count, False

        text = self._ocr_pdf_to_text(path)
        return text, page_count, True

    @staticmethod
    def _read_pdf_text(path: str) -> tuple[str, int, bool]:
        try:
            with fitz.open(path) as document:
                if document.needs_pass or document.is_encrypted:
                    raise ValueError("Password-protected PDFs are not supported.")
                page_count = len(document)
                if page_count > settings.MAX_CV_PDF_PAGES:
                    raise ValueError(
                        f"CV PDF exceeds the {settings.MAX_CV_PDF_PAGES}-page limit."
                    )

                pages: list[str] = []
                characters = 0
                has_sparse_page = page_count == 0
                for page in document:
                    page_text = page.get_text("text", sort=True)
                    visible_characters = len(re.sub(r"\s", "", page_text))
                    if visible_characters < _MIN_PDF_PAGE_TEXT_CHARACTERS:
                        has_sparse_page = True

                    characters += len(page_text) + (1 if pages else 0)
                    if characters > settings.MAX_PROMPT_CHARS:
                        raise ValueError(
                            f"CV text exceeds the {settings.MAX_PROMPT_CHARS}-character limit; "
                            "shorten the CV and try again."
                        )
                    pages.append(page_text)

                return "\n".join(pages), page_count, has_sparse_page
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("The PDF is corrupt or could not be read.") from exc

    @classmethod
    def _ocr_pdf_to_text(cls, path: str) -> str:
        executable = shutil.which("ocrmypdf")
        if not executable:
            raise ValueError(
                "No text was found in this PDF, and OCR is not available on this server."
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = str(Path(tmpdir) / "ocr.pdf")
            command = [
                executable,
                "--skip-text",
                "--deskew",
                "--quiet",
                path,
                output_path,
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=150)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("OCR took too long. Try a smaller PDF.") from exc
            except subprocess.CalledProcessError as exc:
                raise ValueError("The scanned PDF could not be read by OCR.") from exc
            except OSError as exc:
                raise ValueError("OCR could not be started on this server.") from exc

            if not Path(output_path).is_file():
                raise ValueError("OCR did not produce a readable PDF.")
            text, _, _ = cls._read_pdf_text(output_path)
            if not text.strip():
                raise ValueError("OCR completed, but no readable text was found in this PDF.")
            return text

    def _extract_docx(self, path: str) -> str:
        self._validate_docx_archive(path)
        try:
            document = Document(path)
            return self._docx_text(document)
        except Exception as exc:
            raise ValueError("The Word document is corrupt or could not be read.") from exc

    @staticmethod
    def _validate_docx_archive(path: str) -> None:
        if not zipfile.is_zipfile(path):
            raise ValueError("Invalid Word document.")

        try:
            with zipfile.ZipFile(path) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                if len(infos) > settings.MAX_CV_DOCX_ARCHIVE_FILES:
                    raise ValueError(
                        "Word document contains too many archive entries "
                        f"(maximum {settings.MAX_CV_DOCX_ARCHIVE_FILES})."
                    )

                member_names: dict[str, str] = {}
                total_uncompressed = 0
                total_compressed = 0
                for info in infos:
                    name = info.filename
                    normalized_name = PurePosixPath(name).as_posix().casefold()
                    parts = PurePosixPath(name).parts
                    if (
                        not name
                        or "\x00" in name
                        or "\\" in name
                        or name.startswith("/")
                        or ".." in parts
                        or (parts and ":" in parts[0])
                        or normalized_name in member_names
                    ):
                        raise ValueError("Word document contains an unsafe archive path.")
                    member_names[normalized_name] = name

                    if info.flag_bits & 0x1:
                        raise ValueError("Encrypted Word documents are not supported.")
                    if stat.S_ISLNK(info.external_attr >> 16):
                        raise ValueError("Word document contains an unsupported archive link.")
                    if info.compress_type not in _SUPPORTED_ZIP_COMPRESSION:
                        raise ValueError("Word document uses unsupported archive compression.")

                    total_uncompressed += info.file_size
                    total_compressed += info.compress_size
                    if total_uncompressed > settings.MAX_CV_DOCX_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            "Word document exceeds the uncompressed-size limit "
                            f"of {settings.MAX_CV_DOCX_UNCOMPRESSED_BYTES} bytes."
                        )
                    if info.file_size and (
                        info.compress_size == 0
                        or info.file_size / info.compress_size
                        > settings.MAX_CV_DOCX_COMPRESSION_RATIO
                    ):
                        raise ValueError("Word document exceeds the archive compression-ratio limit.")

                if total_uncompressed and (
                    total_compressed == 0
                    or total_uncompressed / total_compressed
                    > settings.MAX_CV_DOCX_COMPRESSION_RATIO
                ):
                    raise ValueError("Word document exceeds the archive compression-ratio limit.")

                required_parts = {"[Content_Types].xml", "word/document.xml"}
                required_keys = {name.casefold() for name in required_parts}
                if not required_keys.issubset(member_names):
                    raise ValueError("File is not a valid DOCX Word document.")
                if "word/vbaproject.bin" in member_names:
                    raise ValueError("Macro-enabled Word documents are not supported.")

                content_types = archive.read(member_names["[content_types].xml"])
                if (
                    _DOCX_MAIN_CONTENT_TYPE not in content_types.lower()
                    or b"macroenabled" in content_types.lower()
                ):
                    raise ValueError("File is not a standard non-macro DOCX document.")

                for info in infos:
                    lowered_name = info.filename.lower()
                    if not lowered_name.endswith((".xml", ".rels")):
                        continue
                    xml = archive.read(info).lower()
                    if b"<!doctype" in xml or b"<!entity" in xml:
                        raise ValueError("Word document contains unsafe XML declarations.")

                corrupt_member = archive.testzip()
                if corrupt_member:
                    raise ValueError("Word document archive is corrupt.")
        except ValueError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, KeyError) as exc:
            raise ValueError("The Word document archive is corrupt or unsupported.") from exc

    @classmethod
    def _docx_text(cls, document: DocxDocument) -> str:
        header_lines: list[str] = []
        footer_lines: list[str] = []
        seen_header_parts: set[int] = set()
        seen_footer_parts: set[int] = set()

        for section in document.sections:
            for attribute in ("header", "first_page_header", "even_page_header"):
                container = getattr(section, attribute)
                part_identity = id(container._element)
                if part_identity not in seen_header_parts:
                    seen_header_parts.add(part_identity)
                    header_lines.extend(cls._container_lines(container))
            for attribute in ("footer", "first_page_footer", "even_page_footer"):
                container = getattr(section, attribute)
                part_identity = id(container._element)
                if part_identity not in seen_footer_parts:
                    seen_footer_parts.add(part_identity)
                    footer_lines.extend(cls._container_lines(container))

        body_lines = cls._container_lines(document)
        return "\n".join([*header_lines, *body_lines, *footer_lines])

    @classmethod
    def _container_lines(cls, container) -> list[str]:
        lines: list[str] = []
        for item in cls._iter_block_items(container):
            if isinstance(item, Paragraph):
                lines.extend(cls._paragraph_lines(item))
            elif isinstance(item, Table):
                lines.extend(cls._table_lines(item))
            elif item.strip():
                lines.append(item)
        return lines

    @classmethod
    def _paragraph_lines(cls, paragraph: Paragraph) -> list[str]:
        lines = [paragraph.text] if paragraph.text.strip() else []
        for text_box in paragraph._p.xpath(".//w:txbxContent"):
            text = "".join(node.text or "" for node in text_box.xpath(".//w:t"))
            if text.strip() and text not in lines:
                lines.append(text)
        return lines

    @classmethod
    def _table_lines(cls, table: Table) -> list[str]:
        lines: list[str] = []
        for row in table.rows:
            values: list[str] = []
            seen_cells: set[int] = set()
            for cell in row.cells:
                cell_identity = id(cell._tc)
                if cell_identity in seen_cells:
                    continue
                seen_cells.add(cell_identity)
                value = " / ".join(cls._container_lines(cell)).strip()
                if value:
                    values.append(value)
            if values:
                lines.append(" | ".join(values))
        return lines

    @staticmethod
    def _iter_block_items(container) -> Iterator[Paragraph | Table | str]:
        if isinstance(container, DocxDocument):
            element = container.element.body
        elif isinstance(container, _Cell):
            element = container._tc
        else:
            element = container._element

        for child in element.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, container)
            elif isinstance(child, CT_Tbl):
                yield Table(child, container)
            else:
                text = "".join(node.text or "" for node in child.xpath(".//w:t"))
                if text.strip():
                    yield text

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).replace("\u00a0", " ")
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        normalized = _CONTROL_CHARACTERS.sub("", normalized)
        normalized = re.sub(r"[^\S\n]+", " ", normalized)
        normalized = re.sub(r" *\n *", "\n", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()


cv_documents = CvDocumentService()

"""Text from a transcript: pasted text, a .docx or a text-based PDF (docs/02 §10; DECISIONS #44).

There is no OCR: a PDF with too little text per page is refused as scanned. The file is read in memory and
never stored; only the identifier-stripped text is kept later (``sources/<name>.txt``). A .docx is read with
the standard library (zipfile + ElementTree), walking the body in order: paragraphs as lines, each table row
as one line with its cells separated by tabs. A document type definition or entity in the XML is refused,
as is a .docx whose unpacked XML exceeds ``courses.max_unpacked_bytes``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import PurePath
from typing import TYPE_CHECKING
from xml.etree import ElementTree

if TYPE_CHECKING:
    from hub.config import CoursesConfig

SCANNED = "This PDF has no text layer; OCR is not supported. Export it as text or Word, or paste the text."
NOTHING_READ = "Nothing was read."
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractError(Exception):
    """The document can't be used; the message is shown to the user as is."""


def text_from_paste(text: str) -> str:
    """Pasted text with its line breaks normalised (and a byte-order mark dropped)."""
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def extract_text(filename: str, data: bytes, cfg: CoursesConfig) -> str:
    """The text of an uploaded .docx or PDF; ``ExtractError`` with the user's message otherwise."""
    suffix = PurePath(filename or "").suffix.lower()
    if suffix not in cfg.allowed_types:
        allowed = " and ".join(cfg.allowed_types)
        raise ExtractError(f"Only {allowed} files can be imported, or paste the text. {NOTHING_READ}")
    if len(data) > cfg.max_upload_bytes:
        raise ExtractError(f"The file is larger than {cfg.max_upload_bytes // 1_000_000} MB. {NOTHING_READ}")
    if suffix == ".pdf":
        return _pdf(data, cfg)
    return _docx(data, cfg)


def _pdf(data: bytes, cfg: CoursesConfig) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not data.startswith(b"%PDF-"):
        raise ExtractError(f"The file is not a PDF. {NOTHING_READ}")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractError(
                f"This PDF is password-protected; export it without a password. {NOTHING_READ}"
            )
        pages = [page.extract_text() or "" for page in reader.pages]
    except ExtractError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise ExtractError(f"The PDF could not be read. {NOTHING_READ}") from None
    if not pages:
        raise ExtractError(SCANNED)
    letters = sum(len("".join(p.split())) for p in pages)
    if letters / len(pages) < cfg.min_pdf_text_per_page:
        raise ExtractError(SCANNED)
    return text_from_paste("\n".join(pages))


def _docx(data: bytes, cfg: CoursesConfig) -> str:
    if not data.startswith(b"PK"):
        raise ExtractError(f"The file is not a Word document (.docx). {NOTHING_READ}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > cfg.max_unpacked_bytes:
                raise ExtractError(f"The Word file is too large once unpacked. {NOTHING_READ}")
            xml = archive.read(info)
    except ExtractError:
        raise
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError):
        raise ExtractError(f"The Word file could not be read. {NOTHING_READ}") from None
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ExtractError(f"The Word file contains definitions the app does not read. {NOTHING_READ}")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        raise ExtractError(f"The Word file could not be read. {NOTHING_READ}") from None
    body = root.find(f"{W}body")
    if body is None:
        raise ExtractError(f"The Word file could not be read. {NOTHING_READ}")
    lines: list[str] = []
    for child in body:
        if child.tag == f"{W}p":
            lines.append(_paragraph(child))
        elif child.tag == f"{W}tbl":
            for row in child.iter(f"{W}tr"):
                cells = [
                    " ".join(_paragraph(p) for p in cell.iter(f"{W}p")).strip()
                    for cell in row.findall(f"{W}tc")
                ]
                lines.append("\t".join(cells))
    return text_from_paste("\n".join(lines) + "\n")


def _paragraph(p: ElementTree.Element) -> str:
    out: list[str] = []
    for node in p.iter():
        if node.tag == f"{W}t":
            out.append(node.text or "")
        elif node.tag == f"{W}tab":
            out.append("\t")
        elif node.tag in (f"{W}br", f"{W}cr"):
            out.append("\n")
    return "".join(out)

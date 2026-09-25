# backend/ai/policy_ingestion.py

"""
Policy document ingestion (Days 86-88): turns a PDF or DOCX file into an
ordered list of paragraph chunks.

Pure extraction only - no database access, no Ollama, no agent logic.
That split matters: extract_paragraphs() is trivially testable against
real files with no fixtures beyond the files themselves, and the
DB-facing ingest_document() (below) is the thin layer that stores what
this returns. The eventual Policy Intelligence Agent (Days 89-97) reads
already-stored chunks; it never re-parses a document itself.

Design choice: chunks are PARAGRAPHS, not sentences or fixed-size
windows, and are stored as a flat ordered list rather than queried live
from the source file on every question. Two reasons: (1) a paragraph is
the smallest unit that is usually still a coherent, citable statement of
policy - "[Excerpt 4]" should point at something a person can read and
judge on its own; (2) the source file is not guaranteed to still exist,
be reachable, or be unchanged by the time a citation is checked weeks
later, but a stored chunk is. Whether keyword-overlap retrieval over
this stored list is sufficient, or embeddings are needed instead, is a
Day 86 question deferred to Day 89 (retrieval) - see the roadmap's own
"no embeddings before keyword-overlap proves insufficient" principle.
"""

from dataclasses import dataclass
from pathlib import Path

import pypdf
from datetime import datetime, timezone

from docx import Document as DocxDocument
from sqlalchemy.orm import Session

from database.repositories import PolicyRepository

MIN_CHUNK_LENGTH = 1  # a paragraph of just whitespace or a stray bullet char is not a real chunk


class UnsupportedDocumentTypeError(Exception):
    """The file's extension is not one this module knows how to parse."""


class DocumentParsingError(Exception):
    """The file has a supported extension but could not actually be read (corrupt, encrypted, empty, ...)."""


@dataclass(frozen=True)
class ExtractedDocument:
    paragraphs: list[str]
    page_or_section_count: int


def extract_paragraphs(file_path: str) -> ExtractedDocument:
    """Dispatches by extension. Raises UnsupportedDocumentTypeError or DocumentParsingError."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        return _extract_from_pdf(file_path)
    if suffix == ".docx":
        return _extract_from_docx(file_path)
    raise UnsupportedDocumentTypeError(f"Unsupported document type '{suffix}' for {file_path}. Supported: .pdf, .docx")


def _clean_paragraphs(raw_paragraphs: list[str]) -> list[str]:
    """Strips whitespace and drops anything that is empty once stripped - preserves order."""
    cleaned = [p.strip() for p in raw_paragraphs]
    return [p for p in cleaned if len(p) >= MIN_CHUNK_LENGTH]


def _extract_from_pdf(file_path: str) -> ExtractedDocument:
    try:
        reader = pypdf.PdfReader(file_path)
    except (pypdf.errors.PdfReadError, FileNotFoundError, OSError) as error:
        raise DocumentParsingError(f"Could not open PDF {file_path}: {error}") from error

    if reader.is_encrypted:
        raise DocumentParsingError(f"PDF {file_path} is encrypted/password-protected - cannot extract text")

    paragraphs: list[str] = []
    try:
        for page in reader.pages:
            text = page.extract_text() or ""
            # pypdf gives one text blob per page with internal newlines
            # roughly at line breaks, not true paragraph breaks - a blank
            # line is the closest available signal for "new paragraph".
            for block in text.split("\n\n"):
                paragraphs.extend(line for line in block.split("\n"))
    except Exception as error:
        raise DocumentParsingError(f"Could not extract text from PDF {file_path}: {error}") from error

    return ExtractedDocument(paragraphs=_clean_paragraphs(paragraphs), page_or_section_count=len(reader.pages))


def _extract_from_docx(file_path: str) -> ExtractedDocument:
    try:
        document = DocxDocument(file_path)
    except Exception as error:
        raise DocumentParsingError(f"Could not open DOCX {file_path}: {error}") from error

    # python-docx's own Paragraph objects ARE the real semantic
    # paragraphs (unlike the PDF case), so no blank-line heuristic needed.
    paragraphs = [p.text for p in document.paragraphs]

    return ExtractedDocument(paragraphs=_clean_paragraphs(paragraphs), page_or_section_count=len(document.paragraphs))


def ingest_document(session: Session, title: str, file_path: str, source_filename: str = None) -> int:
    """
    Parses file_path and stores it as a new policy document with its
    chunks. Returns the new document's id. Raises
    UnsupportedDocumentTypeError / DocumentParsingError from extraction -
    nothing is stored if parsing fails (create_document() is only called
    once extraction has already succeeded).
    """
    extracted = extract_paragraphs(file_path)
    uploaded_at = datetime.now(timezone.utc).isoformat()
    return PolicyRepository(session).create_document(
        title=title,
        source_filename=source_filename or Path(file_path).name,
        uploaded_at=uploaded_at,
        chunks=extracted.paragraphs,
    )

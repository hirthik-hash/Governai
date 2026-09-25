# backend/tests/policy_fixtures.py

"""
Helpers that generate REAL PDF and DOCX files on disk for the policy
ingestion tests - not hand-rolled binary blobs standing in for real
documents. Uses reportlab (PDF) and python-docx (DOCX, the same library
the app itself uses to WRITE a fixture as any real DOCX-producing tool
would) so the extraction code in ai/policy_ingestion.py is exercised
against genuine file formats.
"""

from pathlib import Path

from docx import Document as DocxDocument
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def make_pdf(path: Path, lines: list[str]) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 720
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return path


def make_multi_page_pdf(path: Path, pages: list[list[str]]) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    for page_lines in pages:
        y = 720
        for line in page_lines:
            c.drawString(72, y, line)
            y -= 20
        c.showPage()
    c.save()
    return path


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    document = DocxDocument()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))
    return path

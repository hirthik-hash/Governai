# backend/tests/test_policy_ingestion.py

"""
Days 86-88: extract_paragraphs() against REAL PDF and DOCX files built
by tests/policy_fixtures.py - proving pypdf/python-docx integration
actually works, not a mock of what we hope they'd return.
"""

import pytest

from ai.policy_ingestion import (
    DocumentParsingError, UnsupportedDocumentTypeError, extract_paragraphs,
)
from tests.policy_fixtures import make_docx, make_multi_page_pdf, make_pdf


class TestPdfExtraction:

    def test_extracts_real_text_from_a_generated_pdf(self, tmp_path):
        make_pdf(tmp_path / "policy.pdf", [
            "Access Control Policy",
            "All requests for restricted resources must be logged.",
            "Escalations time out after 30 minutes.",
        ])

        result = extract_paragraphs(str(tmp_path / "policy.pdf"))

        assert result.paragraphs == [
            "Access Control Policy",
            "All requests for restricted resources must be logged.",
            "Escalations time out after 30 minutes.",
        ]
        assert result.page_or_section_count == 1

    def test_counts_multiple_pages_and_preserves_reading_order(self, tmp_path):
        make_multi_page_pdf(tmp_path / "multi.pdf", [
            ["Page one line A", "Page one line B"],
            ["Page two line A"],
        ])

        result = extract_paragraphs(str(tmp_path / "multi.pdf"))

        assert result.page_or_section_count == 2
        assert result.paragraphs == ["Page one line A", "Page one line B", "Page two line A"]

    def test_blank_and_whitespace_only_lines_are_dropped(self, tmp_path):
        make_pdf(tmp_path / "sparse.pdf", ["Real content here", "   ", "More real content"])

        result = extract_paragraphs(str(tmp_path / "sparse.pdf"))

        assert result.paragraphs == ["Real content here", "More real content"]

    def test_a_corrupt_pdf_raises_a_clear_parsing_error(self, tmp_path):
        bad_file = tmp_path / "corrupt.pdf"
        bad_file.write_bytes(b"%PDF-1.4 this is not a real pdf body at all")

        with pytest.raises(DocumentParsingError):
            extract_paragraphs(str(bad_file))

    def test_a_missing_file_raises_a_parsing_error_not_a_generic_one(self, tmp_path):
        with pytest.raises(DocumentParsingError):
            extract_paragraphs(str(tmp_path / "does-not-exist.pdf"))

    def test_an_empty_pdf_returns_no_paragraphs_without_raising(self, tmp_path):
        # reportlab genuinely produces a zero-page PDF when nothing was
        # ever drawn - confirmed by inspection, not assumed.
        make_pdf(tmp_path / "empty.pdf", [])

        result = extract_paragraphs(str(tmp_path / "empty.pdf"))

        assert result.paragraphs == []
        assert result.page_or_section_count == 0


class TestDocxExtraction:

    def test_extracts_real_paragraphs_from_a_generated_docx(self, tmp_path):
        make_docx(tmp_path / "policy.docx", [
            "Data Retention Policy",
            "Audit records are retained indefinitely.",
            "Decision log entries are retained for seven years.",
        ])

        result = extract_paragraphs(str(tmp_path / "policy.docx"))

        assert result.paragraphs == [
            "Data Retention Policy",
            "Audit records are retained indefinitely.",
            "Decision log entries are retained for seven years.",
        ]
        assert result.page_or_section_count == 3

    def test_empty_paragraphs_in_the_docx_are_dropped(self, tmp_path):
        make_docx(tmp_path / "sparse.docx", ["First", "", "   ", "Second"])

        result = extract_paragraphs(str(tmp_path / "sparse.docx"))

        assert result.paragraphs == ["First", "Second"]

    def test_a_docx_with_only_empty_paragraphs_returns_nothing(self, tmp_path):
        make_docx(tmp_path / "empty.docx", ["", "   "])

        result = extract_paragraphs(str(tmp_path / "empty.docx"))

        assert result.paragraphs == []

    def test_a_corrupt_docx_raises_a_clear_parsing_error(self, tmp_path):
        bad_file = tmp_path / "corrupt.docx"
        bad_file.write_bytes(b"PK not a real docx zip contents")

        with pytest.raises(DocumentParsingError):
            extract_paragraphs(str(bad_file))

    def test_a_missing_docx_raises_a_parsing_error(self, tmp_path):
        with pytest.raises(DocumentParsingError):
            extract_paragraphs(str(tmp_path / "missing.docx"))


class TestUnsupportedTypes:

    @pytest.mark.parametrize("suffix", [".txt", ".doc", ".md", ".rtf", ""])
    def test_unsupported_extensions_are_rejected_clearly(self, tmp_path, suffix):
        path = tmp_path / f"file{suffix}"
        path.write_text("some content")

        with pytest.raises(UnsupportedDocumentTypeError, match=suffix if suffix else "''"):
            extract_paragraphs(str(path))

    def test_extension_matching_is_case_insensitive(self, tmp_path):
        make_pdf(tmp_path / "POLICY.PDF", ["Some content"])

        result = extract_paragraphs(str(tmp_path / "POLICY.PDF"))

        assert result.paragraphs == ["Some content"]

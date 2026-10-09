# backend/tests/test_api_policy_documents.py

"""
Day 100: upload, list and delete policy documents over HTTP.

Uses real generated PDF/DOCX files (tests/policy_fixtures.py) and a real
in-memory SQLite database - only the authenticated app is the shared
ApiHarness. Admin only; everything else is 401/403.
"""

import pytest

from api.routes import policy_documents
from database.repositories import PolicyRepository
from database.session import init_db, make_engine, make_session_factory
from tests.api_harness import ADMIN_ID, ApiHarness
from tests.policy_fixtures import make_docx, make_pdf

REQUESTER = "user-003"
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

ENDPOINTS = [
    ("post", "/policy/documents", {"files": {"file": ("a.pdf", b"x", PDF)}}),
    ("get", "/policy/documents", {}),
    ("delete", "/policy/documents/1", {}),
]


@pytest.fixture
def factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def harness(factory):
    harness = ApiHarness()
    harness.app.state.session_factory = factory
    return harness


def call(harness, user_id, method, url, **kwargs):
    headers = harness.as_user(user_id).headers
    return harness.client.request(method, url, headers=headers, **kwargs)


def upload(harness, name, content, mime=PDF, title=None):
    data = {"title": title} if title is not None else {}
    return call(harness, ADMIN_ID, "post", "/policy/documents", files={"file": (name, content, mime)}, data=data)


def pdf_bytes(tmp_path, lines):
    return make_pdf(tmp_path / "source.pdf", lines).read_bytes()


class TestAccessControl:

    @pytest.mark.parametrize("method,url,kwargs", ENDPOINTS, ids=[f"{m}-{u}" for m, u, _ in ENDPOINTS])
    def test_no_token_is_401(self, harness, method, url, kwargs):
        response = harness.client.request(method, url, **kwargs)

        assert response.status_code == 401

    @pytest.mark.parametrize("method,url,kwargs", ENDPOINTS, ids=[f"{m}-{u}" for m, u, _ in ENDPOINTS])
    def test_non_admin_is_403(self, harness, method, url, kwargs):
        response = call(harness, REQUESTER, method, url, **kwargs)

        assert response.status_code == 403

    def test_admin_gets_503_when_no_library_is_configured(self):
        bare = ApiHarness()

        response = call(bare, ADMIN_ID, "get", "/policy/documents")

        assert response.status_code == 503


class TestUpload:

    def test_admin_uploads_a_pdf(self, harness, tmp_path):
        content = pdf_bytes(tmp_path, ["Access Control Policy", "Escalations time out after 30 minutes."])

        response = upload(harness, "access.pdf", content, title="Access Control")

        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Access Control"
        assert body["source_filename"] == "access.pdf"
        assert body["chunk_count"] == 2
        assert isinstance(body["id"], int)

    def test_admin_uploads_a_docx(self, harness, tmp_path):
        path = make_docx(tmp_path / "rules.docx", ["First rule.", "Second rule.", "Third rule."])

        response = upload(harness, "rules.docx", path.read_bytes(), mime=DOCX)

        assert response.status_code == 201
        assert response.json()["chunk_count"] == 3

    def test_title_defaults_to_the_filename_without_its_extension(self, harness, tmp_path):
        response = upload(harness, "Data Retention.pdf", pdf_bytes(tmp_path, ["Keep logs for 90 days."]))

        assert response.json()["title"] == "Data Retention"

    def test_unsupported_extension_is_415(self, harness):
        response = upload(harness, "notes.txt", b"plain text", mime="text/plain")

        assert response.status_code == 415

    def test_unreadable_pdf_is_422_and_stores_nothing(self, harness):
        response = upload(harness, "broken.pdf", b"this is not a pdf")

        assert response.status_code == 422
        assert call(harness, ADMIN_ID, "get", "/policy/documents").json()["count"] == 0

    def test_oversized_upload_is_413(self, harness, tmp_path, monkeypatch):
        monkeypatch.setattr(policy_documents, "MAX_UPLOAD_BYTES", 10)

        response = upload(harness, "big.pdf", pdf_bytes(tmp_path, ["Some policy text that is longer than ten bytes."]))

        assert response.status_code == 413

    def test_missing_file_is_422(self, harness):
        response = call(harness, ADMIN_ID, "post", "/policy/documents", data={"title": "No file"})

        assert response.status_code == 422

    def test_directory_parts_are_stripped_from_the_filename(self, harness, tmp_path):
        response = upload(harness, "../../evil.pdf", pdf_bytes(tmp_path, ["A rule."]))

        assert response.status_code == 201
        assert response.json()["source_filename"] == "evil.pdf"


class TestListAndDelete:

    def test_empty_library_lists_nothing(self, harness):
        response = call(harness, ADMIN_ID, "get", "/policy/documents")

        assert response.status_code == 200
        assert response.json() == {"count": 0, "documents": []}

    def test_list_shows_uploaded_documents_with_chunk_counts(self, harness, tmp_path):
        upload(harness, "one.pdf", pdf_bytes(tmp_path, ["Rule A.", "Rule B."]), title="One")
        upload(harness, "two.pdf", pdf_bytes(tmp_path, ["Rule C."]), title="Two")

        body = call(harness, ADMIN_ID, "get", "/policy/documents").json()

        assert body["count"] == 2
        assert [(d["title"], d["chunk_count"]) for d in body["documents"]] == [("One", 2), ("Two", 1)]

    def test_delete_removes_the_document_and_its_chunks(self, harness, factory, tmp_path):
        document_id = upload(harness, "gone.pdf", pdf_bytes(tmp_path, ["Rule A.", "Rule B."])).json()["id"]

        response = call(harness, ADMIN_ID, "delete", f"/policy/documents/{document_id}")

        assert response.status_code == 204
        assert call(harness, ADMIN_ID, "get", "/policy/documents").json()["count"] == 0
        session = factory()
        try:
            assert PolicyRepository(session).all_chunks() == []
        finally:
            session.close()

    def test_delete_of_an_unknown_document_is_404(self, harness):
        response = call(harness, ADMIN_ID, "delete", "/policy/documents/999")

        assert response.status_code == 404
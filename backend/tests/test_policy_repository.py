# backend/tests/test_policy_repository.py

"""Days 86-88: PolicyRepository and ingest_document(), against a real (in-memory) SQLite database."""

import pytest
from sqlalchemy.exc import IntegrityError

from ai.policy_ingestion import DocumentParsingError, ingest_document
from database.repositories import PolicyChunk, PolicyDocument, PolicyRepository, RecordNotFoundError
from database.session import init_db, make_engine, make_session_factory
from tests.policy_fixtures import make_docx, make_pdf

UPLOADED_AT = "2026-09-25T10:00:00+00:00"


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    s = make_session_factory(engine)()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def repo(session):
    return PolicyRepository(session)


class TestCreateAndRead:

    def test_create_then_get_document_reports_the_chunk_count(self, repo):
        doc_id = repo.create_document("Access Policy", "access.pdf", UPLOADED_AT, ["First rule", "Second rule"])

        document = repo.get_document(doc_id)

        assert document == PolicyDocument(
            id=doc_id, title="Access Policy", source_filename="access.pdf",
            uploaded_at=UPLOADED_AT, chunk_count=2,
        )

    def test_chunks_are_stored_in_order_with_stable_indices(self, repo):
        doc_id = repo.create_document("Policy", "p.pdf", UPLOADED_AT, ["Zeroth", "First", "Second"])

        chunks = repo.get_chunks(doc_id)

        assert chunks == [
            PolicyChunk(document_id=doc_id, chunk_index=0, text="Zeroth"),
            PolicyChunk(document_id=doc_id, chunk_index=1, text="First"),
            PolicyChunk(document_id=doc_id, chunk_index=2, text="Second"),
        ]

    def test_a_document_with_zero_chunks_is_allowed_and_stored(self, repo):
        doc_id = repo.create_document("Empty Source", "blank.pdf", UPLOADED_AT, [])

        assert repo.get_document(doc_id).chunk_count == 0
        assert repo.get_chunks(doc_id) == []

    def test_get_chunk_resolves_a_single_index(self, repo):
        doc_id = repo.create_document("Policy", "p.pdf", UPLOADED_AT, ["A", "B", "C"])

        assert repo.get_chunk(doc_id, 1) == PolicyChunk(document_id=doc_id, chunk_index=1, text="B")

    def test_get_chunk_unknown_index_is_not_found(self, repo):
        doc_id = repo.create_document("Policy", "p.pdf", UPLOADED_AT, ["A"])

        with pytest.raises(RecordNotFoundError):
            repo.get_chunk(doc_id, 5)

    def test_get_document_unknown_id_is_not_found_which_is_a_value_error(self, repo):
        with pytest.raises(RecordNotFoundError):
            repo.get_document(999)
        with pytest.raises(ValueError):
            repo.get_document(999)

    def test_list_documents_is_ordered_and_matches_creation(self, repo):
        first = repo.create_document("A", "a.pdf", UPLOADED_AT, ["x"])
        second = repo.create_document("B", "b.pdf", UPLOADED_AT, ["y", "z"])

        listed = repo.list_documents()

        assert [d.id for d in listed] == [first, second]
        assert [d.chunk_count for d in listed] == [1, 2]

    def test_all_chunks_spans_every_document_in_order(self, repo):
        first = repo.create_document("A", "a.pdf", UPLOADED_AT, ["a0", "a1"])
        second = repo.create_document("B", "b.pdf", UPLOADED_AT, ["b0"])

        all_chunks = repo.all_chunks()

        assert [(c.document_id, c.chunk_index, c.text) for c in all_chunks] == [
            (first, 0, "a0"), (first, 1, "a1"), (second, 0, "b0"),
        ]


class TestDeleteCascades:

    def test_deleting_a_document_deletes_its_chunks_too(self, repo, session):
        doc_id = repo.create_document("Policy", "p.pdf", UPLOADED_AT, ["A", "B"])

        repo.delete_document(doc_id)
        session.commit()

        with pytest.raises(RecordNotFoundError):
            repo.get_document(doc_id)
        assert repo.get_chunks(doc_id) == []

    def test_deleting_one_document_leaves_another_untouched(self, repo, session):
        keep = repo.create_document("Keep", "keep.pdf", UPLOADED_AT, ["stays"])
        drop = repo.create_document("Drop", "drop.pdf", UPLOADED_AT, ["goes"])

        repo.delete_document(drop)
        session.commit()

        assert repo.get_document(keep).chunk_count == 1
        assert repo.all_chunks() == [PolicyChunk(document_id=keep, chunk_index=0, text="stays")]

    def test_deleting_an_unknown_document_is_not_found(self, repo):
        with pytest.raises(RecordNotFoundError):
            repo.delete_document(999)


class TestChunkIndexUniqueness:

    def test_the_database_refuses_a_duplicate_index_within_one_document(self, repo, session):
        doc_id = repo.create_document("Policy", "p.pdf", UPLOADED_AT, ["A"])
        from database.models import PolicyChunkModel
        session.add(PolicyChunkModel(document_id=doc_id, chunk_index=0, text="duplicate"))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_two_different_documents_may_each_have_a_chunk_zero(self, repo):
        first = repo.create_document("A", "a.pdf", UPLOADED_AT, ["x"])
        second = repo.create_document("B", "b.pdf", UPLOADED_AT, ["y"])

        assert repo.get_chunk(first, 0).text == "x"
        assert repo.get_chunk(second, 0).text == "y"


class TestIngestDocument:

    def test_ingesting_a_real_pdf_stores_it_with_its_chunks(self, session, tmp_path):
        make_pdf(tmp_path / "policy.pdf", ["Rule one", "Rule two"])

        doc_id = ingest_document(session, "My Policy", str(tmp_path / "policy.pdf"))

        repo = PolicyRepository(session)
        document = repo.get_document(doc_id)
        assert document.title == "My Policy"
        assert document.source_filename == "policy.pdf"
        assert document.chunk_count == 2
        assert [c.text for c in repo.get_chunks(doc_id)] == ["Rule one", "Rule two"]

    def test_ingesting_a_real_docx_stores_it_too(self, session, tmp_path):
        make_docx(tmp_path / "policy.docx", ["Retention rule"])

        doc_id = ingest_document(session, "Retention Policy", str(tmp_path / "policy.docx"))

        assert [c.text for c in PolicyRepository(session).get_chunks(doc_id)] == ["Retention rule"]

    def test_a_custom_source_filename_overrides_the_path_basename(self, session, tmp_path):
        make_pdf(tmp_path / "temp_upload_8f3a.pdf", ["Content"])

        doc_id = ingest_document(
            session, "Policy", str(tmp_path / "temp_upload_8f3a.pdf"), source_filename="original-name.pdf"
        )

        assert PolicyRepository(session).get_document(doc_id).source_filename == "original-name.pdf"

    def test_a_corrupt_file_raises_and_stores_nothing(self, session, tmp_path):
        bad_file = tmp_path / "corrupt.pdf"
        bad_file.write_bytes(b"not a real pdf")

        with pytest.raises(DocumentParsingError):
            ingest_document(session, "Bad Policy", str(bad_file))

        assert PolicyRepository(session).list_documents() == []

    def test_uploaded_at_is_a_real_recent_iso_timestamp(self, session, tmp_path):
        from datetime import datetime, timezone
        make_pdf(tmp_path / "policy.pdf", ["Content"])
        before = datetime.now(timezone.utc)

        doc_id = ingest_document(session, "Policy", str(tmp_path / "policy.pdf"))

        uploaded_at = datetime.fromisoformat(PolicyRepository(session).get_document(doc_id).uploaded_at)
        assert before <= uploaded_at <= datetime.now(timezone.utc)

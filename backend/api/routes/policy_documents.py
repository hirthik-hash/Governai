# backend/api/routes/policy_documents.py

"""
Policy library management (Day 100): upload, list and delete the PDF/DOCX
documents the Policy Intelligence Agent answers from.

Admin only: these documents steer every AI answer, so who may change them
is an access-control question, not a convenience. They never reach the
FSM - the agent is advisory.

Plain `def` routes: parsing a PDF and writing to the database are
blocking work that FastAPI runs in its threadpool instead of the event
loop. Each route owns its session and commits explicitly, because
ingest_document() only flushes.
"""

import os
import tempfile
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import sessionmaker

from ai.policy_ingestion import DocumentParsingError, UnsupportedDocumentTypeError, ingest_document
from api.dependencies import get_session_factory, require_admin
from api.policy_document_schemas import PolicyDocumentListResponse, PolicyDocumentResponse
from database.repositories import PolicyRepository, RecordNotFoundError

router = APIRouter(prefix="/policy/documents", tags=["policy"], dependencies=[Depends(require_admin)])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_SUFFIXES = (".pdf", ".docx")
MAX_TITLE_LENGTH = 200
MAX_FILENAME_LENGTH = 200


def _safe_filename(raw: Optional[str]) -> str:
    """Only the final path component, with Windows separators handled; never a client-chosen directory."""
    name = PurePosixPath((raw or "").replace("\\", "/")).name
    return name[:MAX_FILENAME_LENGTH]


@router.post("", response_model=PolicyDocumentResponse, status_code=201)
def upload_policy_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None, max_length=MAX_TITLE_LENGTH),
    session_factory: sessionmaker = Depends(get_session_factory),
):
    filename = _safe_filename(file.filename)
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail="Only .pdf and .docx files are accepted")

    # Reads at most one byte past the cap, so an oversized upload is
    # refused without ever holding the whole thing in memory.
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")

    document_title = (title or "").strip() or PurePosixPath(filename).stem

    handle, temp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(content)

        session = session_factory()
        try:
            document_id = ingest_document(session, document_title, temp_path, source_filename=filename)
            session.commit()
            document = PolicyRepository(session).get_document(document_id)
        except (DocumentParsingError, UnsupportedDocumentTypeError):
            session.rollback()
            raise HTTPException(status_code=422, detail="The file could not be read as a PDF or DOCX document")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    return PolicyDocumentResponse(**asdict(document))


@router.get("", response_model=PolicyDocumentListResponse)
def list_policy_documents(session_factory: sessionmaker = Depends(get_session_factory)):
    session = session_factory()
    try:
        documents = PolicyRepository(session).list_documents()
    finally:
        session.close()
    return PolicyDocumentListResponse(
        count=len(documents),
        documents=[PolicyDocumentResponse(**asdict(d)) for d in documents],
    )


@router.delete("/{document_id}", status_code=204)
def delete_policy_document(document_id: int, session_factory: sessionmaker = Depends(get_session_factory)):
    """Deletes the document and, through the database's own foreign key, all of its chunks."""
    session = session_factory()
    try:
        PolicyRepository(session).delete_document(document_id)
        session.commit()
    except RecordNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail=f"No policy document with id {document_id}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return Response(status_code=204)
# backend/api/policy_document_schemas.py

"""Response models for the policy document routes (Day 100)."""

from pydantic import BaseModel, Field


class PolicyDocumentResponse(BaseModel):
    id: int
    title: str
    source_filename: str
    uploaded_at: str
    # 0 is legitimate: the file parsed but contained no citable text.
    chunk_count: int


class PolicyDocumentListResponse(BaseModel):
    count: int
    documents: list[PolicyDocumentResponse] = Field(default_factory=list)
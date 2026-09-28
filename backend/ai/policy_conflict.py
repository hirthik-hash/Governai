# backend/ai/policy_conflict.py

"""
Policy conflict detection (Days 94-95): a cheap keyword pre-filter finds
CANDIDATE chunk pairs; Ollama only judges the candidates, never every
possible pair. With N chunks there are O(N^2) possible pairs, and an
Ollama call per pair would make a large policy library prohibitively
slow (and expensive, model-time-wise) to check - the same "no fake
sophistication, cheap filter before the AI" principle already applied to
retrieval (Days 89-91) and to Agent 1's non-NLP design (Day 26).

Scope: CROSS-DOCUMENT pairs only. Two passages within the same document
contradicting each other is a document-authoring problem for whoever
wrote it, not the kind of conflict this feature targets - which is two
DIFFERENT policy sources disagreeing (e.g. one document says a 30-minute
timeout, another says 45).

find_candidate_pairs() is pure - no database, no Ollama - exactly like
policy_retrieval.py, for the same testability reasons.
"""

from dataclasses import dataclass

from ai.policy_retrieval import tokenize
from database.repositories import PolicyChunk

DEFAULT_MIN_SHARED_TERMS = 3


@dataclass(frozen=True)
class ConflictCandidate:
    chunk_a: PolicyChunk
    chunk_b: PolicyChunk
    shared_terms: frozenset[str]


def find_candidate_pairs(
    chunks: list[PolicyChunk], min_shared_terms: int = DEFAULT_MIN_SHARED_TERMS
) -> list[ConflictCandidate]:
    """
    Every cross-document chunk pair sharing at least min_shared_terms
    distinct non-stopword terms, ordered deterministically by
    (doc_a, index_a, doc_b, index_b) so the same library always produces
    the same candidate list in the same order - Ollama is only ever
    asked about a candidate once per run, and a caller capping how many
    candidates it checks always drops the same ones first.
    """
    tokenized = [(chunk, tokenize(chunk.text)) for chunk in chunks]
    candidates = []

    for i in range(len(tokenized)):
        chunk_a, tokens_a = tokenized[i]
        if not tokens_a:
            continue
        for j in range(i + 1, len(tokenized)):
            chunk_b, tokens_b = tokenized[j]
            if chunk_a.document_id == chunk_b.document_id:
                continue
            shared = tokens_a & tokens_b
            if len(shared) >= min_shared_terms:
                candidates.append(ConflictCandidate(chunk_a=chunk_a, chunk_b=chunk_b, shared_terms=frozenset(shared)))

    candidates.sort(
        key=lambda c: (c.chunk_a.document_id, c.chunk_a.chunk_index, c.chunk_b.document_id, c.chunk_b.chunk_index)
    )
    return candidates

# backend/ai/policy_retrieval.py

"""
Keyword-overlap retrieval over stored policy chunks (Days 89-91).

Deliberately NOT embeddings - this project's own principle throughout
has been "no embeddings before keyword-overlap proves insufficient"
(the same reasoning that delayed NLP in Agent 1 until Day 26). This
module is pure and has no database or Ollama dependency, so it is
testable purely on lists of strings.

Scoring: lowercase, split on non-alphanumeric runs, drop a small
stopword list (function words score is dominated by "the"/"of"/"and"
otherwise, which would make every chunk look equally relevant), then
score a chunk by how many DISTINCT query tokens it contains - not raw
token-count overlap, so a chunk repeating one matching word ten times
does not outrank a chunk containing three different matching words.
Ties are broken by (document_id, chunk_index) for determinism: retrieval
must give the same answer to the same question every time, since Day
92-93's citation grounding will depend on stable results.

Known, deliberate limitation: no stemming or lemmatization - "escalation"
and "escalations" are different tokens and will NOT match each other.
Adding stemming before this simple version has been shown insufficient
would be exactly the kind of premature sophistication this project's own
conventions warn against (see the "no embeddings before keyword-overlap
proves insufficient" principle applied the same way to NLP in Agent 1).
If real usage shows this matters, it is a small, contained change here.
"""

import re
from dataclasses import dataclass

from database.repositories import PolicyChunk

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

# Small and deliberately conservative: common English function words that
# would otherwise dominate overlap scoring without adding any real
# topical signal. Not a linguistic stopword list - just enough to stop
# "what is the policy on X" from matching every chunk containing "the".
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do",
    "does", "for", "from", "has", "have", "how", "i", "if", "in", "is",
    "it", "may", "of", "on", "or", "should", "that", "the", "this", "to",
    "was", "we", "what", "when", "where", "which", "who", "will", "with",
})


def tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_PATTERN.findall(text.lower()) if t not in STOPWORDS}


@dataclass(frozen=True)
class ScoredChunk:
    chunk: PolicyChunk
    score: int
    matched_terms: frozenset[str]


def retrieve_relevant_chunks(chunks: list[PolicyChunk], question: str, top_k: int = 3) -> list[ScoredChunk]:
    """
    The top_k highest-scoring chunks for `question`, best first, ties
    broken by (document_id, chunk_index). Chunks that match ZERO query
    terms are excluded entirely - a top_k slot is never filled with an
    irrelevant chunk just to reach the requested count.
    """
    query_terms = tokenize(question)
    if not query_terms:
        return []

    scored = []
    for chunk in chunks:
        matched = query_terms & tokenize(chunk.text)
        if matched:
            scored.append(ScoredChunk(chunk=chunk, score=len(matched), matched_terms=frozenset(matched)))

    scored.sort(key=lambda sc: (-sc.score, sc.chunk.document_id, sc.chunk.chunk_index))
    return scored[:top_k]

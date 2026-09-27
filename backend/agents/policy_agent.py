# backend/agents/policy_agent.py

"""
Policy Intelligence Agent (GovernAI Agent 7 of 7) - Q&A interface (Days
89-91) with citation-grounding verification (Days 92-93). ADVISORY ONLY:
nothing in this file, or anywhere in the ai/ layer it builds on, ever
reaches the FSM. RequestPipeline does not construct this agent and
never calls it - see core/orchestrator.py's own architecture diagram
comment ("Policy Intelligence Agent (Ollama) - ADVISORY ONLY, never
touches FSM decisions").

process({"question": str}) -> AgentResult with:
  data["answer"]             the model's raw generated text
  data["excerpts"]           the chunks retrieval selected, each labeled
                              "Excerpt N" for the prompt
  data["chunks_considered"]  how many chunks existed in the policy
                              library at query time (0 is a real, useful
                              answer: "no policy documents uploaded yet")
  data["citations_found"]    every "[Excerpt N]" number the ANSWER
                              itself cites, in ascending order
  data["invalid_citations"]  citations_found entries that do NOT
                              correspond to any excerpt actually
                              retrieved (Days 92-93 - see below)
  data["grounding_warning"]  True iff invalid_citations is non-empty -
                              the model referenced an excerpt that was
                              never given to it, i.e. a fabricated
                              citation, not merely an unfollowed style
                              instruction

Grounding verification (Days 92-93) is deliberately narrow: it ONLY
checks that a cited excerpt NUMBER was among those actually retrieved
for this query. It does NOT verify that the answer's claim about an
excerpt's content is accurate, and it does NOT flag an answer that
cites nothing at all (the model may correctly answer in its own words,
or correctly say the excerpts don't cover the question - neither is a
grounding failure). What it catches is the specific, verifiable failure
mode of a model inventing a reference to material it was never shown -
"only trusts [Excerpt N] markers pointing at chunks actually retrieved"
in this project's own original design language for this agent.

Conflict detection (Days 94-95) and decision explanation (Days 96-97)
are separate methods added to this same class later, sharing the same
injected dependencies.
"""

import re
from typing import Optional

from sqlalchemy.orm import sessionmaker

from agents.base_agent import AgentResult, BaseAgent
from ai.ollama_client import OllamaError
from ai.policy_retrieval import ScoredChunk, retrieve_relevant_chunks
from database.repositories import PolicyRepository

DEFAULT_TOP_K = 3

_CITATION_PATTERN = re.compile(r"\[Excerpt (\d+)\]")

_SYSTEM_PROMPT = (
    "You are a policy assistant for an access-governance system. Answer the "
    "question using ONLY the numbered excerpts below. Cite the excerpts you "
    "used with their exact label, like [Excerpt 1]. If the excerpts do not "
    "contain enough information to answer, say so plainly instead of "
    "guessing or using outside knowledge."
)


def _extract_cited_excerpt_numbers(answer: str) -> list[int]:
    """Every distinct "[Excerpt N]" number the answer text cites, ascending. Order in the text is not preserved - only distinctness and sort order matter to a caller checking validity."""
    return sorted({int(n) for n in _CITATION_PATTERN.findall(answer)})


class PolicyIntelligenceAgent(BaseAgent):

    def __init__(self, ollama_client, session_factory: sessionmaker, top_k: int = DEFAULT_TOP_K):
        super().__init__()
        self._ollama = ollama_client
        self._session_factory = session_factory
        self._top_k = top_k

    @property
    def agent_name(self) -> str:
        return "policy_intelligence"

    def process(self, input_data: dict) -> AgentResult:
        question = input_data.get("question", "").strip()
        if not question:
            return self._failure("Missing required field", errors=["question is required"])

        session = self._session_factory()
        try:
            all_chunks = PolicyRepository(session).all_chunks()
        finally:
            session.close()

        if not all_chunks:
            return self._success(
                data={
                    "answer": "", "excerpts": [], "chunks_considered": 0, "grounded": False,
                    "citations_found": [], "invalid_citations": [], "grounding_warning": False,
                },
                reasoning="No policy documents are in the library yet - nothing to answer from.",
            )

        matches = retrieve_relevant_chunks(all_chunks, question, top_k=self._top_k)
        if not matches:
            return self._success(
                data={
                    "answer": "", "excerpts": [], "chunks_considered": len(all_chunks), "grounded": False,
                    "citations_found": [], "invalid_citations": [], "grounding_warning": False,
                },
                reasoning=(
                    f"{len(all_chunks)} chunk(s) in the library, but none matched any term in the question - "
                    "no excerpt to answer from."
                ),
            )

        excerpts = self._label_excerpts(matches)
        prompt = self._build_prompt(question, excerpts)

        try:
            answer = self._ollama.generate(prompt, system=_SYSTEM_PROMPT)
        except OllamaError as error:
            return self._failure(f"Ollama call failed: {error}", errors=[str(error)])

        valid_numbers = set(range(1, len(excerpts) + 1))
        citations_found = _extract_cited_excerpt_numbers(answer)
        invalid_citations = [n for n in citations_found if n not in valid_numbers]
        grounding_warning = bool(invalid_citations)

        reasoning = (
            f"Retrieved {len(excerpts)} of {len(all_chunks)} chunk(s) via keyword overlap; "
            f"Ollama generated a {len(answer)}-character answer."
        )
        if grounding_warning:
            reasoning += f" WARNING: answer cited nonexistent excerpt(s) {invalid_citations} - not among those retrieved."

        return self._success(
            data={
                "answer": answer, "excerpts": excerpts, "chunks_considered": len(all_chunks), "grounded": True,
                "citations_found": citations_found, "invalid_citations": invalid_citations,
                "grounding_warning": grounding_warning,
            },
            reasoning=reasoning,
        )

    def _label_excerpts(self, matches: list[ScoredChunk]) -> list[dict]:
        return [
            {
                "label": f"Excerpt {i}",
                "document_id": m.chunk.document_id,
                "chunk_index": m.chunk.chunk_index,
                "text": m.chunk.text,
                "score": m.score,
            }
            for i, m in enumerate(matches, start=1)
        ]

    def _build_prompt(self, question: str, excerpts: list[dict]) -> str:
        excerpt_block = "\n\n".join(f"[{e['label']}] {e['text']}" for e in excerpts)
        return f"{excerpt_block}\n\nQuestion: {question}"

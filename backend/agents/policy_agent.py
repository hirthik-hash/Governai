# backend/agents/policy_agent.py

"""
Policy Intelligence Agent (GovernAI Agent 7 of 7) - Q&A interface (Days
89-91). ADVISORY ONLY: nothing in this file, or anywhere in the ai/
layer it builds on, ever reaches the FSM. RequestPipeline does not
construct this agent and never calls it - see core/orchestrator.py's own
architecture diagram comment ("Policy Intelligence Agent (Ollama) -
ADVISORY ONLY, never touches FSM decisions").

process({"question": str}) -> AgentResult with:
  data["answer"]            the model's raw generated text
  data["excerpts"]          the chunks retrieval selected, each labeled
                             "Excerpt N" for the prompt (citation
                             VERIFICATION - checking the answer's own
                             [Excerpt N] references against this list -
                             is Days 92-93, not built here)
  data["chunks_considered"] how many chunks existed in the policy
                             library at query time (0 is a real, useful
                             answer: "no policy documents uploaded yet")

Q&A only for now: conflict detection (Days 94-95) and decision
explanation (Days 96-97) are separate methods added to this same class
later, sharing the same injected dependencies.
"""

from typing import Optional

from sqlalchemy.orm import sessionmaker

from agents.base_agent import AgentResult, BaseAgent
from ai.ollama_client import OllamaError
from ai.policy_retrieval import ScoredChunk, retrieve_relevant_chunks
from database.repositories import PolicyRepository

DEFAULT_TOP_K = 3

_SYSTEM_PROMPT = (
    "You are a policy assistant for an access-governance system. Answer the "
    "question using ONLY the numbered excerpts below. Cite the excerpts you "
    "used with their exact label, like [Excerpt 1]. If the excerpts do not "
    "contain enough information to answer, say so plainly instead of "
    "guessing or using outside knowledge."
)


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
                data={"answer": "", "excerpts": [], "chunks_considered": 0, "grounded": False},
                reasoning="No policy documents are in the library yet - nothing to answer from.",
            )

        matches = retrieve_relevant_chunks(all_chunks, question, top_k=self._top_k)
        if not matches:
            return self._success(
                data={"answer": "", "excerpts": [], "chunks_considered": len(all_chunks), "grounded": False},
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

        return self._success(
            data={"answer": answer, "excerpts": excerpts, "chunks_considered": len(all_chunks), "grounded": True},
            reasoning=(
                f"Retrieved {len(excerpts)} of {len(all_chunks)} chunk(s) via keyword overlap; "
                f"Ollama generated a {len(answer)}-character answer."
            ),
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

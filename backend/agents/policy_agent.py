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

Conflict detection (Days 94-95, detect_conflicts()) and decision
explanation (Days 96-97, explain_decision()) are separate methods on
this same class, sharing the same injected dependencies (an Ollama
client and a session_factory - nothing else).

Day 98 (Phase 4 close-out): what is real, what is deliberately
simplified, and what is a genuine known gap, stated plainly per this
project's own convention for closing a phase.

Real and verified:
  - every method tested against FakeOllamaClient (logic), the real
    OllamaClient via httpx.MockTransport (the real HTTP code path), and
    a genuine local Ollama server when reachable (test_policy_agent_
    real_ollama.py, test_policy_agent_integration.py's
    TestFullRealWorkflow) - including a full Q&A -> conflict detection
    -> real-pipeline-decision -> explanation sequence against one
    shared library with a real model (mistral:7b-instruct in this
    project's own testing);
  - the agent holds no mutable state between calls - verified
    empirically (TestAgentHoldsNoStateBetweenCalls), not just claimed;
  - citation grounding and outcome-consistency checks are real, cheap,
    keyword-level verifications with mutation-tested logic (see Days
    92-93 and 96-97's own test suites), not merely trusted LLM output.

Deliberately simplified (documented, not hidden):
  - retrieval is keyword overlap, no stemming, no embeddings - the
    "cheap check before the AI" principle applied throughout this
    project (Agent 1's non-NLP design, retrieval, conflict detection);
  - citation grounding checks ONLY that a cited excerpt number existed
    - never the accuracy of what the model claims about it;
  - outcome-consistency checking is a keyword check (grant/deny/pending
    vocabulary), not semantic understanding of the narrative.

Known, real gaps (not yet built):
  - no caching: every call re-reads the full chunk list from the
    database and re-tokenizes it, which is fine at today's library
    sizes and would need revisiting at real scale;
  - detect_conflicts() is never scheduled or triggered automatically -
    nothing in RequestPipeline or the API calls it; it is a library
    function an operator or a future API route would invoke;
  - no API routes exist yet for any of these four capabilities (Q&A,
    conflict detection, explanation) - they are only reachable as
    direct Python calls today, which is consistent with this agent
    being advisory-only and outside RequestPipeline's own request path,
    but means a human still has to invoke them manually or through
    tooling built on top, which does not yet exist.
"""

import re
from typing import Optional

from sqlalchemy.orm import sessionmaker

from agents.base_agent import AgentResult, BaseAgent
from ai.ollama_client import OllamaError
from ai.policy_conflict import ConflictCandidate, find_candidate_pairs
from ai.policy_retrieval import ScoredChunk, retrieve_relevant_chunks
from database.repositories import AuditRecordRepository, PolicyRepository

DEFAULT_TOP_K = 3
DEFAULT_MIN_SHARED_TERMS = 3
DEFAULT_MAX_CANDIDATES = 20

_CITATION_PATTERN = re.compile(r"\[Excerpt (\d+)\]")

_SYSTEM_PROMPT = (
    "You are a policy assistant for an access-governance system. Answer the "
    "question using ONLY the numbered excerpts below. Cite the excerpts you "
    "used with their exact label, like [Excerpt 1]. If the excerpts do not "
    "contain enough information to answer, say so plainly instead of "
    "guessing or using outside knowledge."
)

_CONFLICT_SYSTEM_PROMPT = (
    "You compare two policy excerpts from DIFFERENT documents for a genuine "
    "factual conflict - one saying something the other contradicts (e.g. "
    "different numbers, different rules for the same situation). Excerpts on "
    "related but different topics, or that simply do not overlap enough to "
    "compare, are NOT a conflict. Respond with your verdict as the FIRST "
    "word of your reply: either NO_CONFLICT or CONFLICT, followed by one "
    "short sentence explaining why."
)

_EXPLANATION_SYSTEM_PROMPT = (
    "You explain access-governance decisions in plain English for a "
    "non-technical reader. Use ONLY the facts given below - do not invent "
    "any additional reasons, numbers, or outcomes. State the final outcome "
    "exactly as given; never claim the request was granted if it was denied, "
    "or the reverse, and never claim a final outcome for a request that is "
    "still pending. Keep it to two or three short sentences."
)

_GRANT_WORDS = ("granted", "approved", "allowed", "permitted")
_DENY_WORDS = ("denied", "rejected", "blocked", "refused")


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

    def detect_conflicts(
        self,
        min_shared_terms: int = DEFAULT_MIN_SHARED_TERMS,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ) -> AgentResult:
        """
        Days 94-95. Finds cross-document chunk pairs that share enough
        keywords to be worth checking (find_candidate_pairs, cheap, no
        Ollama), then asks Ollama to judge ONLY those candidates - never
        every possible pair, which would not scale.

        data["conflicts"]           candidates Ollama judged CONFLICT
        data["candidates_checked"]  how many candidate pairs were sent to Ollama
        data["undetermined"]        candidates whose response could not
                                     be parsed as either verdict - never
                                     silently counted as NO_CONFLICT
        data["chunks_considered"]   total chunks in the library at query time

        max_candidates caps real cost (each candidate is one Ollama
        call): candidates beyond the cap are simply not checked this run,
        reported via data["candidates_skipped"] rather than silently
        dropped.
        """
        session = self._session_factory()
        try:
            all_chunks = PolicyRepository(session).all_chunks()
        finally:
            session.close()

        if len(all_chunks) < 2:
            return self._success(
                data=self._empty_conflict_data(len(all_chunks)),
                reasoning=f"Only {len(all_chunks)} chunk(s) in the library - need at least 2 to compare.",
            )

        candidates = find_candidate_pairs(all_chunks, min_shared_terms=min_shared_terms)
        if not candidates:
            return self._success(
                data=self._empty_conflict_data(len(all_chunks)),
                reasoning=f"{len(all_chunks)} chunk(s) in the library, but no cross-document pair shared enough terms to check.",
            )

        to_check = candidates[:max_candidates]
        skipped = len(candidates) - len(to_check)

        conflicts = []
        undetermined = 0
        for candidate in to_check:
            prompt = self._build_conflict_prompt(candidate)
            try:
                response = self._ollama.generate(prompt, system=_CONFLICT_SYSTEM_PROMPT)
            except OllamaError as error:
                return self._failure(f"Ollama call failed during conflict check: {error}", errors=[str(error)])

            verdict = _parse_conflict_verdict(response)
            if verdict is True:
                conflicts.append(self._describe_conflict(candidate, response))
            elif verdict is None:
                undetermined += 1

        reasoning = (
            f"Checked {len(to_check)} candidate pair(s) out of {len(candidates)} found "
            f"(from {len(all_chunks)} chunk(s) total): {len(conflicts)} conflict(s), {undetermined} undetermined."
        )
        if skipped:
            reasoning += f" {skipped} candidate(s) were not checked (over the {max_candidates}-candidate cap)."

        return self._success(
            data={
                "conflicts": conflicts,
                "candidates_checked": len(to_check),
                "candidates_skipped": skipped,
                "undetermined": undetermined,
                "chunks_considered": len(all_chunks),
            },
            reasoning=reasoning,
        )

    def _empty_conflict_data(self, chunks_considered: int) -> dict:
        return {
            "conflicts": [], "candidates_checked": 0, "candidates_skipped": 0,
            "undetermined": 0, "chunks_considered": chunks_considered,
        }

    def explain_decision(self, request_id: str) -> AgentResult:
        """
        Days 96-97. A plain-English narrative of why a specific access
        decision was made, generated from the REAL recorded AuditRecord's
        own fields and reasoning trail - never from the FSM or agents
        directly, so this can never influence the decision it explains.

        If a request has more than one audit record (e.g. blocked by safe
        mode, then finalized later - see Day 78), the MOST RECENT one is
        explained, since that is the request's actual current outcome.

        data["outcome_consistent"] is the verification this project's own
        design calls for: True unless the narrative's own wording
        contradicts the real recorded final_decision (claims granted when
        it was denied, or the reverse, or claims either when the real
        record is still PENDING). This is a cheap keyword check, not a
        semantic one - see _check_outcome_consistency() - consistent with
        this agent's "cheap check before trusting the AI" pattern
        throughout (retrieval, conflict detection).
        """
        session = self._session_factory()
        try:
            records = AuditRecordRepository(session).for_request(request_id)
        finally:
            session.close()

        if not records:
            return self._failure(
                f"No audit record found for request_id {request_id}",
                errors=[f"request_id {request_id} has no recorded decision"],
            )
        record = records[-1]

        prompt = self._build_explanation_prompt(record)
        try:
            narrative = self._ollama.generate(prompt, system=_EXPLANATION_SYSTEM_PROMPT)
        except OllamaError as error:
            return self._failure(f"Ollama call failed during decision explanation: {error}", errors=[str(error)])

        consistent = _check_outcome_consistency(record.final_decision, narrative)

        reasoning = f"Explained request {request_id} (recorded outcome: {record.final_decision})."
        if not consistent:
            reasoning += " WARNING: the generated narrative's wording contradicts the recorded outcome."

        return self._success(
            data={
                "request_id": request_id,
                "narrative": narrative,
                "final_decision": record.final_decision,
                "outcome_consistent": consistent,
                "records_found": len(records),
            },
            reasoning=reasoning,
        )

    def _build_explanation_prompt(self, record) -> str:
        trail = "\n".join(f"- {line}" for line in record.agent_reasoning_trail) or "(no reasoning recorded)"
        approver = record.approver_user_id or "(none - not escalated)"
        policy_rule = record.policy_rule_cited or "(none cited)"
        return (
            f"Request: {record.request_id}\n"
            f"Requesting user: {record.user_id}\n"
            f"Resource: {record.resource_id}\n"
            f"Action requested: {record.action_requested}\n"
            f"Risk score: {record.risk_score} ({record.risk_level})\n"
            f"Approver (if escalated): {approver}\n"
            f"Policy rule cited: {policy_rule}\n"
            f"Final recorded outcome: {record.final_decision}\n\n"
            f"Reasoning trail recorded by the system, in order:\n{trail}\n\n"
            "Explain in plain English why this decision was made."
        )
        return {
            "conflicts": [], "candidates_checked": 0, "candidates_skipped": 0,
            "undetermined": 0, "chunks_considered": chunks_considered,
        }

    def _build_conflict_prompt(self, candidate: ConflictCandidate) -> str:
        return (
            f"Excerpt A (document {candidate.chunk_a.document_id}): {candidate.chunk_a.text}\n\n"
            f"Excerpt B (document {candidate.chunk_b.document_id}): {candidate.chunk_b.text}"
        )

    def _describe_conflict(self, candidate: ConflictCandidate, ollama_response: str) -> dict:
        return {
            "document_a": candidate.chunk_a.document_id, "chunk_index_a": candidate.chunk_a.chunk_index,
            "text_a": candidate.chunk_a.text,
            "document_b": candidate.chunk_b.document_id, "chunk_index_b": candidate.chunk_b.chunk_index,
            "text_b": candidate.chunk_b.text,
            "shared_terms": sorted(candidate.shared_terms),
            "ollama_explanation": ollama_response.strip(),
        }


def _parse_conflict_verdict(response: str) -> Optional[bool]:
    """
    True (CONFLICT), False (NO_CONFLICT), or None (unparseable - the
    response was neither, or malformed). Checks NO_CONFLICT first: it
    contains "CONFLICT" as a substring, so checking for plain "CONFLICT"
    first would misread every legitimate NO_CONFLICT verdict as a conflict.
    """
    normalized = response.strip().upper()
    if "NO_CONFLICT" in normalized or "NO CONFLICT" in normalized:
        return False
    if "CONFLICT" in normalized:
        return True
    return None


def _check_outcome_consistency(final_decision: str, narrative: str) -> bool:
    """
    True if narrative's own wording does not contradict final_decision.
    A GRANTED record is inconsistent only if the narrative uses deny
    language WITHOUT also using grant language (mentioning a risk that
    was overcome is fine; claiming the request was ultimately denied is
    not). Symmetric for DENIED. A PENDING record is inconsistent if the
    narrative claims EITHER a final grant or a final denial - a request
    that has not concluded should not be narrated as if it had.
    """
    text = narrative.lower()
    has_grant = any(word in text for word in _GRANT_WORDS)
    has_deny = any(word in text for word in _DENY_WORDS)

    if final_decision == "GRANTED":
        return not (has_deny and not has_grant)
    if final_decision == "DENIED":
        return not (has_grant and not has_deny)
    if final_decision == "PENDING":
        return not (has_grant or has_deny)
    return True

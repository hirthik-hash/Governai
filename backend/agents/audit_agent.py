# backend/agents/audit_agent.py

"""
Audit & Compliance Agent (GovernAI Agent 5 of 7).

Distinct from DecisionLogger (Day 11): DecisionLogger records WHAT
happened mechanically (every FSM state transition, timestamped).
This agent records WHY, holistically - it compiles one comprehensive
AuditRecord per completed request, consolidating every agent's own
reasoning (the AgentResult.reasoning string each of the first 4
agents has produced since Day 25) into a single human-readable
compliance trail entry, alongside the final FSM outcome.

Today's piece (Day 49): compile_record(), which takes the results
already produced by RequestUnderstandingAgent, AccessValidationAgent,
SecurityRiskAgent, and (if applicable) EscalationAgent, plus the
final FSM state, and produces one AuditRecord.

policy_rule_cited is a placeholder field (None until Phase 4's
Ollama-backed Policy Intelligence Agent exists) - included now so
the AuditRecord's shape doesn't need to change later, honest about
what isn't built yet rather than omitting the field entirely.

Export formats (JSON/CSV/PDF per the original design) are NOT part
of this file yet - that's later in this block (Days 50-51).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from agents.base_agent import BaseAgent, AgentResult


@dataclass
class AuditRecord:
    """
    One comprehensive, human-readable audit trail entry for a single
    completed request - the compliance-report view, not the
    transition-log view (that's DecisionLogger's job).
    """
    request_id: str
    timestamp: str
    user_id: str
    resource_id: str
    action_requested: str
    final_fsm_state: str
    risk_score: int
    risk_level: str
    final_decision: str  # "GRANTED", "DENIED", "PENDING" - derived from final_fsm_state
    approver_user_id: str  # "" if never escalated
    agent_reasoning_trail: list[str] = field(default_factory=list)
    policy_rule_cited: str = None  # placeholder until Phase 4


# Maps FSM final states to a plain-English decision label, for the
# audit record's final_decision field - this is presentation, not a
# new source of truth; the FSM's own state remains authoritative.
_DECISION_LABELS = {
    "closed": "GRANTED",
    "denied_final": "DENIED",
}


class AuditComplianceAgent(BaseAgent):
    """
    Compiles a comprehensive AuditRecord from the results already
    produced by the other agents plus the FSM's final state. Does
    not re-derive any decision logic itself - it only consolidates
    and presents what already happened.
    """

    @property
    def agent_name(self) -> str:
        return "audit_compliance"

    def process(self, input_data: dict) -> AgentResult:
        """
        Expects input_data to contain:
          - request_id, user_id, resolved_resource_id (or resource_id)
          - final_fsm_state (string value, e.g. "closed")
          - risk_score, risk_level
          - approver_user_id (optional, "" or absent if never escalated)
          - agent_results: list of AgentResult objects from the
            agents that processed this request, in order
        """
        request_id = input_data.get("request_id")
        user_id = input_data.get("user_id")
        final_fsm_state = input_data.get("final_fsm_state")
        agent_results = input_data.get("agent_results", [])

        if not request_id or not user_id or not final_fsm_state:
            return self._failure(
                "Missing required fields",
                errors=["request_id, user_id, and final_fsm_state are required"],
            )

        resource_id = input_data.get("resolved_resource_id") or input_data.get("resource_id", "unknown")
        risk_score = input_data.get("risk_score", 0)
        risk_level = input_data.get("risk_level", "LOW")
        approver_user_id = input_data.get("approver_user_id", "")

        final_decision = _DECISION_LABELS.get(final_fsm_state, "PENDING")

        reasoning_trail = [
            result.reasoning for result in agent_results
            if isinstance(result, AgentResult) and result.reasoning
        ]

        record = AuditRecord(
            request_id=request_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=user_id,
            resource_id=resource_id,
            action_requested=f"access request for {resource_id}",
            final_fsm_state=final_fsm_state,
            risk_score=risk_score,
            risk_level=risk_level,
            final_decision=final_decision,
            approver_user_id=approver_user_id,
            agent_reasoning_trail=reasoning_trail,
        )

        reasoning = (
            f"Audit record compiled for request {request_id}: "
            f"{final_decision} ({len(reasoning_trail)} agent reasoning entries recorded)"
        )

        return self._success({"audit_record": record}, reasoning)
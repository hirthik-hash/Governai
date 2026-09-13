# backend/agents/audit_agent.py

"""
Audit & Compliance Agent (GovernAI Agent 5 of 7).

Distinct from DecisionLogger (Day 11): DecisionLogger records WHAT
happened mechanically (every FSM state transition, timestamped).
This agent records WHY, holistically - it compiles one comprehensive
AuditRecord per completed request, consolidating every agent's own
reasoning into a single human-readable compliance trail entry.

Day 49: process() compiles one AuditRecord from a completed request's
agent results plus final FSM state.

Day 50 (this addition): export_to_json() and export_to_csv(), both
taking a LIST of AuditRecords (a real compliance export is almost
always "all records for a period," not one at a time) and returning
a string ready to write to a file or send in a response. PDF export
and the real-time dashboard feed are NOT part of this file - PDF
needs the pdf skill/library infrastructure (better suited once
Phase 5 has a frontend requesting it), and the real-time feed is
already DecisionLogger's job.

Export functions are module-level, not agent methods - exporting is
a utility operation on already-compiled records, not "processing a
request" in the BaseAgent sense.
"""

import json
import csv
import io
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from agents.base_agent import BaseAgent, AgentResult


@dataclass
class AuditRecord:
    request_id: str
    timestamp: str
    user_id: str
    resource_id: str
    action_requested: str
    final_fsm_state: str
    risk_score: int
    risk_level: str
    final_decision: str
    approver_user_id: str
    agent_reasoning_trail: list[str] = field(default_factory=list)
    policy_rule_cited: str = None


_DECISION_LABELS = {
    "closed": "GRANTED",
    "denied_final": "DENIED",
}


class AuditComplianceAgent(BaseAgent):

    @property
    def agent_name(self) -> str:
        return "audit_compliance"

    def process(self, input_data: dict) -> AgentResult:
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


def export_to_json(records: list[AuditRecord]) -> str:
    """
    Serializes a list of AuditRecords to a JSON array string. Each
    record becomes a JSON object with the same field names as
    AuditRecord - suitable for writing directly to a .json file or
    returning from an API endpoint later.
    """
    return json.dumps([asdict(r) for r in records], indent=2)


def export_to_csv(records: list[AuditRecord]) -> str:
    """
    Serializes a list of AuditRecords to a CSV string. Since
    agent_reasoning_trail is a list (not a flat value), it's joined
    with ' | ' as a single CSV cell - CSV has no native concept of a
    nested list, so this is the honest flattening rather than
    dropping the field or breaking CSV structure.
    """
    if not records:
        return ""

    output = io.StringIO()
    fieldnames = list(asdict(records[0]).keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for record in records:
        row = asdict(record)
        row["agent_reasoning_trail"] = " | ".join(row["agent_reasoning_trail"])
        writer.writerow(row)

    return output.getvalue()
# backend/agents/audit_agent.py

"""
Audit & Compliance Agent (GovernAI Agent 5 of 7).

Distinct from DecisionLogger (Day 11): DecisionLogger records WHAT
happened mechanically. This agent records WHY, holistically - one
comprehensive AuditRecord per completed request.

Day 49: process() compiles one AuditRecord.
Day 50: export_to_json() / export_to_csv() for a list of records.
Day 51: robustness-tested against real-world data shapes (CSV
special characters, failed-result inclusion, full escalation chains).
Day 52 (this addition): filter_records() and sort_records() for
querying a collection of AuditRecords by user/decision/risk level/
date range, and generate_summary() for aggregate compliance stats
(total requests, decision breakdown, average risk score, escalation
rate). generate_summary is deliberately rule-based aggregation, not
LLM-generated - reliable, deterministic compliance numbers matter
more here than natural-language flourish, and an LLM summary is
honestly out of scope until Phase 4's Ollama integration exists.

PDF export remains deferred (Phase 5, when a frontend can request
it). The real-time dashboard feed remains DecisionLogger's job.
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
    return json.dumps([asdict(r) for r in records], indent=2)


def export_to_csv(records: list[AuditRecord]) -> str:
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


def filter_records(
    records: list[AuditRecord],
    user_id: str = None,
    final_decision: str = None,
    risk_level: str = None,
    start_date: str = None,
    end_date: str = None,
) -> list[AuditRecord]:
    """
    Filters records by any combination of the given criteria - all
    provided filters must match (AND, not OR). start_date/end_date
    are ISO timestamp strings compared lexically, which works
    correctly since AuditRecord.timestamp is always ISO 8601 with a
    fixed-width format (from datetime.isoformat()).
    """
    result = records

    if user_id is not None:
        result = [r for r in result if r.user_id == user_id]
    if final_decision is not None:
        result = [r for r in result if r.final_decision == final_decision]
    if risk_level is not None:
        result = [r for r in result if r.risk_level == risk_level]
    if start_date is not None:
        result = [r for r in result if r.timestamp >= start_date]
    if end_date is not None:
        result = [r for r in result if r.timestamp <= end_date]

    return result


def sort_records(records: list[AuditRecord], by: str = "timestamp", descending: bool = False) -> list[AuditRecord]:
    """
    Sorts records by any AuditRecord field name. Invalid field names
    raise AttributeError naturally via getattr - not caught here,
    since a caller passing a bad field name is a programming error
    worth surfacing loudly, not silently swallowing.
    """
    return sorted(records, key=lambda r: getattr(r, by), reverse=descending)


def generate_summary(records: list[AuditRecord]) -> dict:
    """
    Rule-based aggregate compliance summary - deliberately NOT
    LLM-generated (that's out of scope until Phase 4's Ollama
    integration; deterministic numbers matter more here than
    natural-language flourish for compliance purposes).
    """
    total = len(records)

    if total == 0:
        return {
            "total_requests": 0,
            "decision_breakdown": {},
            "average_risk_score": 0,
            "escalation_rate": 0.0,
        }

    decision_breakdown = {}
    for record in records:
        decision_breakdown[record.final_decision] = decision_breakdown.get(record.final_decision, 0) + 1

    average_risk_score = round(sum(r.risk_score for r in records) / total, 1)
    escalated_count = sum(1 for r in records if r.approver_user_id)
    escalation_rate = round(escalated_count / total, 3)

    return {
        "total_requests": total,
        "decision_breakdown": decision_breakdown,
        "average_risk_score": average_risk_score,
        "escalation_rate": escalation_rate,
    }
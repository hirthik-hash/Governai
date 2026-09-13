# backend/tests/test_audit_agent.py

from agents.audit_agent import AuditComplianceAgent, AuditRecord
from agents.base_agent import AgentResult
import json
import csv
import io
from agents.audit_agent import export_to_json, export_to_csv



def make_fake_agent_result(reasoning: str) -> AgentResult:
    return AgentResult(success=True, data={}, reasoning=reasoning)


class TestAuditComplianceAgentBasicCompilation:

    def test_successful_compilation_returns_audit_record(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-001", "user_id": "user-001",
            "resolved_resource_id": "resource-001",
            "final_fsm_state": "closed", "risk_score": 10, "risk_level": "LOW",
        })

        assert result.success is True
        assert isinstance(result.data["audit_record"], AuditRecord)

    def test_granted_maps_from_closed_state(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-002", "user_id": "user-001",
            "final_fsm_state": "closed",
        })

        assert result.data["audit_record"].final_decision == "GRANTED"

    def test_denied_maps_from_denied_final_state(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-003", "user_id": "user-009",
            "final_fsm_state": "denied_final",
        })

        assert result.data["audit_record"].final_decision == "DENIED"

    def test_unmapped_state_defaults_to_pending(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-004", "user_id": "user-001",
            "final_fsm_state": "manager_review",
        })

        assert result.data["audit_record"].final_decision == "PENDING"

    def test_missing_required_fields_fails_gracefully(self):
        agent = AuditComplianceAgent()
        result = agent.process({"user_id": "user-001"})

        assert result.success is False


class TestAuditComplianceAgentReasoningTrail:

    def test_agent_reasoning_strings_are_consolidated_in_order(self):
        agent = AuditComplianceAgent()
        fake_results = [
            make_fake_agent_result("Request Understanding said X"),
            make_fake_agent_result("Access Validation said Y"),
            make_fake_agent_result("Security Risk said Z"),
        ]

        result = agent.process({
            "request_id": "req-005", "user_id": "user-001",
            "final_fsm_state": "closed", "agent_results": fake_results,
        })

        trail = result.data["audit_record"].agent_reasoning_trail
        assert trail == [
            "Request Understanding said X",
            "Access Validation said Y",
            "Security Risk said Z",
        ]

    def test_no_agent_results_gives_empty_trail_not_a_crash(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-006", "user_id": "user-001",
            "final_fsm_state": "closed",
        })

        assert result.data["audit_record"].agent_reasoning_trail == []

    def test_policy_rule_cited_defaults_to_none(self):
        """
        Locks in the Day 49 design decision: policy_rule_cited exists
        as a field now (so the record's shape won't need to change
        later) but is always None until Phase 4's Policy Intelligence
        Agent exists.
        """
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-007", "user_id": "user-001",
            "final_fsm_state": "closed",
        })

        assert result.data["audit_record"].policy_rule_cited is None


class TestAuditComplianceAgentApproverField:

    def test_approver_recorded_when_escalated(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-008", "user_id": "user-003",
            "final_fsm_state": "closed", "approver_user_id": "user-004",
        })

        assert result.data["audit_record"].approver_user_id == "user-004"

    def test_approver_empty_when_never_escalated(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-009", "user_id": "user-007",
            "final_fsm_state": "closed",
        })

        assert result.data["audit_record"].approver_user_id == ""


class TestAuditComplianceAgentFullChainIntegration:

    def test_compiles_record_from_a_real_full_chain_run(self):
        from agents.request_agent import RequestUnderstandingAgent
        from agents.validation_agent import AccessValidationAgent
        from agents.security_agent import SecurityRiskAgent
        from fsm.governance_fsm import GovernanceFSM
        from datetime import datetime

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-007", "resource_id": "resource-001",
        })

        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        validation_agent = AccessValidationAgent(now_fn=fixed_now)
        combined = dict(request_result.data)
        combined["session_token"] = "abc"
        validation_result = validation_agent.process(combined)
        combined.update(validation_result.data)

        security_agent = SecurityRiskAgent()
        combined["user_id"] = "user-007"
        security_result = security_agent.process(combined)
        combined.update(security_result.data)

        fsm = GovernanceFSM(request_id="req-full-audit-001")
        final_state = fsm.run_until_stuck(combined)

        audit_agent = AuditComplianceAgent()
        audit_input = dict(combined)
        audit_input["request_id"] = "req-full-audit-001"
        audit_input["final_fsm_state"] = final_state.value
        audit_input["agent_results"] = [request_result, validation_result, security_result]

        audit_result = audit_agent.process(audit_input)

        assert audit_result.success is True
        record = audit_result.data["audit_record"]
        assert record.final_decision == "GRANTED"
        assert len(record.agent_reasoning_trail) == 3


def make_sample_record(request_id="req-001", reasoning_trail=None) -> AuditRecord:
    return AuditRecord(
        request_id=request_id,
        timestamp="2026-09-09T14:00:00+00:00",
        user_id="user-001",
        resource_id="resource-001",
        action_requested="access request for resource-001",
        final_fsm_state="closed",
        risk_score=10,
        risk_level="LOW",
        final_decision="GRANTED",
        approver_user_id="",
        agent_reasoning_trail=(
        ["reasoning one", "reasoning two"]
        if reasoning_trail is None
         else reasoning_trail
        ),
    )


class TestExportToJson:

    def test_exports_valid_json_array(self):
        records = [make_sample_record()]
        result = export_to_json(records)

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    def test_json_preserves_all_field_values(self):
        records = [make_sample_record()]
        parsed = json.loads(export_to_json(records))[0]

        assert parsed["request_id"] == "req-001"
        assert parsed["final_decision"] == "GRANTED"
        assert parsed["agent_reasoning_trail"] == ["reasoning one", "reasoning two"]

    def test_multiple_records_all_present_in_export(self):
        records = [make_sample_record("req-A"), make_sample_record("req-B")]
        parsed = json.loads(export_to_json(records))

        assert len(parsed) == 2
        assert {r["request_id"] for r in parsed} == {"req-A", "req-B"}

    def test_empty_list_exports_empty_json_array(self):
        result = export_to_json([])
        assert json.loads(result) == []


class TestExportToCsv:

    def test_exports_valid_csv_with_header(self):
        records = [make_sample_record()]
        result = export_to_csv(records)

        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1
        assert "request_id" in reader.fieldnames

    def test_csv_flattens_reasoning_trail_with_pipe_separator(self):
        records = [make_sample_record(reasoning_trail=["first", "second", "third"])]
        result = export_to_csv(records)

        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["agent_reasoning_trail"] == "first | second | third"

    def test_empty_reasoning_trail_produces_empty_csv_cell(self):
        records = [make_sample_record(reasoning_trail=[])]
        result = export_to_csv(records)

        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["agent_reasoning_trail"] == ""

    def test_empty_records_list_returns_empty_string(self):
        assert export_to_csv([]) == ""

    def test_multiple_records_produce_multiple_csv_rows(self):
        records = [make_sample_record("req-A"), make_sample_record("req-B")]
        result = export_to_csv(records)

        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2


class TestExportRoundTripFromRealAgentOutput:

    def test_agent_produced_record_exports_cleanly_to_both_formats(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-export-001", "user_id": "user-001",
            "final_fsm_state": "closed", "risk_score": 15, "risk_level": "LOW",
        })
        record = result.data["audit_record"]

        json_output = export_to_json([record])
        csv_output = export_to_csv([record])

        assert json.loads(json_output)[0]["request_id"] == "req-export-001"
        assert "req-export-001" in csv_output
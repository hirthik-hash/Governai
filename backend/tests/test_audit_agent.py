# backend/tests/test_audit_agent.py

from agents.audit_agent import AuditComplianceAgent, AuditRecord
from agents.base_agent import AgentResult
import json
import csv
import io
from agents.audit_agent import export_to_json, export_to_csv
from agents.audit_agent import filter_records, sort_records, generate_summary



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

class TestAuditComplianceAgentSpecialCharactersInCsv:

    def test_reasoning_with_commas_does_not_break_csv_structure(self):
        record = make_sample_record(reasoning_trail=[
            "Risk score 45, from triggered factors: unusual_hour, department_mismatch",
        ])
        csv_output = export_to_csv([record])

        reader = csv.DictReader(io.StringIO(csv_output))
        row = next(reader)
        # csv module should have quoted the field correctly - the
        # comma-containing string should come back exactly as it went in
        assert row["agent_reasoning_trail"] == (
            "Risk score 45, from triggered factors: unusual_hour, department_mismatch"
        )

    def test_reasoning_with_quotes_does_not_break_csv_structure(self):
        record = make_sample_record(reasoning_trail=[
            'Approver said "approved" for this request',
        ])
        csv_output = export_to_csv([record])

        reader = csv.DictReader(io.StringIO(csv_output))
        row = next(reader)
        assert row["agent_reasoning_trail"] == 'Approver said "approved" for this request'

    def test_multiple_reasoning_entries_with_commas_join_correctly(self):
        record = make_sample_record(reasoning_trail=[
            "First, with a comma",
            "Second, also with a comma",
        ])
        csv_output = export_to_csv([record])

        reader = csv.DictReader(io.StringIO(csv_output))
        row = next(reader)
        assert row["agent_reasoning_trail"] == "First, with a comma | Second, also with a comma"


class TestAuditComplianceAgentFailedResultsInTrail:

    def test_failed_agent_result_reasoning_is_still_included(self):
        """
        Documents current behavior explicitly: process()'s filter
        only checks isinstance and a non-empty reasoning string - it
        does NOT check result.success. A failed agent's reasoning
        (e.g. 'Unknown user_id: user-999') is currently included in
        the audit trail. This is arguably correct for a compliance
        record - a failure IS part of what happened to this request -
        but it's a deliberate behavior to lock in and revisit
        consciously, not leave undiscovered.
        """
        agent = AuditComplianceAgent()
        failed_result = AgentResult(
            success=False, data={}, reasoning="Access denied: insufficient clearance",
            errors=["insufficient clearance"],
        )
        successful_result = make_fake_agent_result("Request processed normally")

        result = agent.process({
            "request_id": "req-mixed-001", "user_id": "user-001",
            "final_fsm_state": "denied_final",
            "agent_results": [successful_result, failed_result],
        })

        trail = result.data["audit_record"].agent_reasoning_trail
        assert "Access denied: insufficient clearance" in trail
        assert "Request processed normally" in trail


class TestAuditComplianceAgentNoneFieldsInExports:

    def test_none_policy_rule_cited_serializes_as_json_null(self):
        record = make_sample_record()
        json_output = export_to_json([record])
        parsed = json.loads(json_output)[0]

        assert parsed["policy_rule_cited"] is None

    def test_none_policy_rule_cited_serializes_as_empty_csv_cell(self):
        record = make_sample_record()
        csv_output = export_to_csv([record])

        reader = csv.DictReader(io.StringIO(csv_output))
        row = next(reader)
        # Python's csv module writes None as an empty string, not "None"
        assert row["policy_rule_cited"] == ""


class TestAuditComplianceAgentFullChainWithEscalation:

    def test_compiles_correct_record_after_real_escalation_and_approval(self):
        from agents.request_agent import RequestUnderstandingAgent
        from agents.validation_agent import AccessValidationAgent
        from agents.security_agent import SecurityRiskAgent
        from agents.escalation_agent import EscalationAgent
        from fsm.governance_fsm import GovernanceFSM
        from fsm.states import RequestState
        from datetime import datetime

        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-003", "resource_id": "resource-003",
        })

        validation_agent = AccessValidationAgent(now_fn=fixed_now)
        combined = dict(request_result.data)
        combined["session_token"] = "abc"
        validation_result = validation_agent.process(combined)
        combined.update(validation_result.data)

        security_agent = SecurityRiskAgent()
        combined["user_id"] = "user-003"
        security_result = security_agent.process(combined)
        combined.update(security_result.data)

        fsm = GovernanceFSM(request_id="req-audit-escalation-001")
        fsm.transition(combined)
        fsm.transition(combined)
        fsm.transition(combined)
        state = fsm.transition(combined)
        assert state == RequestState.ESCALATION_REQUIRED

        escalation_agent = EscalationAgent()
        combined["request_id"] = "req-audit-escalation-001"
        escalation_result = escalation_agent.process(combined)
        combined.update(escalation_result.data)
        fsm.transition(combined)  # -> MANAGER_REVIEW

        decision_result = escalation_agent.resolve_decision(
            "req-audit-escalation-001", human_decision="approved",
        )
        combined.update(decision_result.data)
        fsm.transition(combined)  # -> ACCESS_GRANTED
        fsm.transition(combined)  # -> AUDIT_LOGGING
        final_state = fsm.transition(combined)  # -> CLOSED

        audit_agent = AuditComplianceAgent()
        audit_input = dict(combined)
        audit_input["final_fsm_state"] = final_state.value
        audit_input["agent_results"] = [
            request_result, validation_result, security_result,
            escalation_result, decision_result,
        ]

        audit_result = audit_agent.process(audit_input)
        record = audit_result.data["audit_record"]

        assert record.final_decision == "GRANTED"
        assert record.approver_user_id == "user-004"
        assert len(record.agent_reasoning_trail) == 5
        assert any("approved" in r.lower() for r in record.agent_reasoning_trail)


def make_varied_records() -> list:
    return [
        AuditRecord("req-1", "2026-09-01T10:00:00+00:00", "user-001", "resource-001",
                    "access request", "closed", 10, "LOW", "GRANTED", ""),
        AuditRecord("req-2", "2026-09-02T10:00:00+00:00", "user-001", "resource-002",
                    "access request", "denied_final", 90, "CRITICAL", "DENIED", ""),
        AuditRecord("req-3", "2026-09-03T10:00:00+00:00", "user-003", "resource-003",
                    "access request", "closed", 45, "HIGH", "GRANTED", "user-004"),
        AuditRecord("req-4", "2026-09-04T10:00:00+00:00", "user-001", "resource-001",
                    "access request", "closed", 5, "LOW", "GRANTED", ""),
    ]


class TestFilterRecords:

    def test_filter_by_user_id(self):
        records = make_varied_records()
        result = filter_records(records, user_id="user-001")

        assert len(result) == 3
        assert all(r.user_id == "user-001" for r in result)

    def test_filter_by_final_decision(self):
        records = make_varied_records()
        result = filter_records(records, final_decision="DENIED")

        assert len(result) == 1
        assert result[0].request_id == "req-2"

    def test_filter_by_risk_level(self):
        records = make_varied_records()
        result = filter_records(records, risk_level="LOW")

        assert len(result) == 2

    def test_filter_by_date_range(self):
        records = make_varied_records()
        result = filter_records(
            records, start_date="2026-09-02T00:00:00+00:00",
            end_date="2026-09-03T23:59:59+00:00",
        )

        assert len(result) == 2
        assert {r.request_id for r in result} == {"req-2", "req-3"}

    def test_combined_filters_are_and_not_or(self):
        records = make_varied_records()
        result = filter_records(records, user_id="user-001", final_decision="GRANTED")

        assert len(result) == 2
        assert all(r.user_id == "user-001" and r.final_decision == "GRANTED" for r in result)

    def test_no_filters_returns_all_records(self):
        records = make_varied_records()
        result = filter_records(records)

        assert len(result) == 4

    def test_filter_matching_nothing_returns_empty_list(self):
        records = make_varied_records()
        result = filter_records(records, user_id="nonexistent-user")

        assert result == []


class TestSortRecords:

    def test_sort_by_timestamp_ascending_default(self):
        records = make_varied_records()
        result = sort_records(records)

        assert [r.request_id for r in result] == ["req-1", "req-2", "req-3", "req-4"]

    def test_sort_by_risk_score_descending(self):
        records = make_varied_records()
        result = sort_records(records, by="risk_score", descending=True)

        assert [r.request_id for r in result] == ["req-2", "req-3", "req-1", "req-4"]

    def test_sort_does_not_mutate_original_list(self):
        records = make_varied_records()
        original_order = [r.request_id for r in records]
        sort_records(records, by="risk_score", descending=True)

        assert [r.request_id for r in records] == original_order

    def test_sort_by_invalid_field_raises_attribute_error(self):
        records = make_varied_records()
        with __import__("pytest").raises(AttributeError):
            sort_records(records, by="nonexistent_field")


class TestGenerateSummary:

    def test_total_requests_count(self):
        summary = generate_summary(make_varied_records())
        assert summary["total_requests"] == 4

    def test_decision_breakdown_counts_correctly(self):
        summary = generate_summary(make_varied_records())
        assert summary["decision_breakdown"] == {"GRANTED": 3, "DENIED": 1}

    def test_average_risk_score_computed_correctly(self):
        summary = generate_summary(make_varied_records())
        # (10 + 90 + 45 + 5) / 4 = 37.5
        assert summary["average_risk_score"] == 37.5

    def test_escalation_rate_computed_correctly(self):
        summary = generate_summary(make_varied_records())
        # 1 of 4 records has a non-empty approver_user_id
        assert summary["escalation_rate"] == 0.25

    def test_empty_records_list_gives_sensible_zero_summary(self):
        summary = generate_summary([])

        assert summary["total_requests"] == 0
        assert summary["decision_breakdown"] == {}
        assert summary["average_risk_score"] == 0
        assert summary["escalation_rate"] == 0.0
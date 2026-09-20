# backend/tests/test_orchestrator_pipeline_part5.py

"""
Tests two reporting layers together, across a realistic multi-
request session, for the first time: DecisionLogger (Day 11, written
automatically by every SystemAwareRequestProcessor call) and
AuditComplianceAgent's export/filter/summary functions (Days 50-52,
which need a LIST of records to be meaningful). Neither has been
exercised across more than one or two requests through the pipeline
until now.
"""

from core.orchestrator import RequestPipeline
from agents.audit_agent import export_to_json, export_to_csv, filter_records, sort_records, generate_summary
from core.decision_logger import LogEntryType
import json


def run_a_realistic_session(pipeline: RequestPipeline) -> list:
    """Runs a handful of varied requests and returns their audit records."""
    records = []

    r1 = pipeline.submit_request({
        "request_id": "session-001", "user_id": "user-007",
        "resource_id": "resource-001", "session_token": "abc",
    })
    records.append(r1.audit_record)

    r2 = pipeline.submit_request({
        "request_id": "session-002", "user_id": "user-009",
        "resource_id": "resource-001", "session_token": "abc",
    })
    records.append(r2.audit_record)

    r3 = pipeline.submit_request({
        "request_id": "session-003", "user_id": "user-003",
        "resource_id": "resource-003", "session_token": "abc",
    })
    assert r3.status == "pending_approval"
    r3_resolved = pipeline.resolve_escalation("session-003", human_decision="approved")
    records.append(r3_resolved.audit_record)

    r4 = pipeline.submit_request({
        "request_id": "session-004", "user_id": "user-001",
        "resource_id": "resource-002", "session_token": "abc",
    })
    records.append(r4.audit_record)

    return [r for r in records if r is not None]


class TestAuditExportAcrossRealSession:

    def test_all_records_from_session_export_to_valid_json(self):
        pipeline = RequestPipeline()
        records = run_a_realistic_session(pipeline)

        json_output = export_to_json(records)
        parsed = json.loads(json_output)

        assert len(parsed) == len(records)
        assert len(parsed) == 4

    def test_all_records_from_session_export_to_valid_csv(self):
        pipeline = RequestPipeline()
        records = run_a_realistic_session(pipeline)

        csv_output = export_to_csv(records)

        for record in records:
            assert record.request_id in csv_output


class TestFilteringAndSortingRealSessionRecords:

    def test_filter_by_granted_decision_returns_expected_subset(self):
        pipeline = RequestPipeline()
        records = run_a_realistic_session(pipeline)

        granted = filter_records(records, final_decision="GRANTED")

        # session-001 (CISO, low risk) and session-003 (escalated+approved) should grant;
        # session-002 (blacklisted) should deny.
        assert len(granted) >= 2
        assert all(r.final_decision == "GRANTED" for r in granted)

    def test_filter_by_specific_user_returns_only_their_requests(self):
        pipeline = RequestPipeline()
        records = run_a_realistic_session(pipeline)

        user_007_records = filter_records(records, user_id="user-007")

        assert len(user_007_records) == 1
        assert user_007_records[0].request_id == "session-001"

    def test_sort_by_risk_score_orders_correctly(self):
        pipeline = RequestPipeline()
        records = run_a_realistic_session(pipeline)

        sorted_records = sort_records(records, by="risk_score", descending=True)

        scores = [r.risk_score for r in sorted_records]
        assert scores == sorted(scores, reverse=True)


class TestSummaryOfRealSession:

    def test_summary_reflects_the_actual_session(self):
        pipeline = RequestPipeline()
        records = run_a_realistic_session(pipeline)

        summary = generate_summary(records)

        assert summary["total_requests"] == len(records)
        assert "GRANTED" in summary["decision_breakdown"]
        assert "DENIED" in summary["decision_breakdown"]
        assert summary["escalation_rate"] > 0  # session-003 was escalated


class TestDecisionLoggerCapturedTheSameSession:

    def test_decision_logger_has_entries_for_every_submitted_request(self):
        pipeline = RequestPipeline()
        run_a_realistic_session(pipeline)

        all_entries = pipeline.decision_logger.all_entries()
        request_transition_entries = pipeline.decision_logger.entries_by_type(
            LogEntryType.REQUEST_TRANSITION
        )

        session_ids = {"session-001", "session-002", "session-003", "session-004"}
        logged_ids = {e.request_id for e in request_transition_entries}

        assert session_ids.issubset(logged_ids)

    def test_decision_logger_and_audit_agent_agree_on_final_outcomes(self):
        """
        Cross-checks the two reporting layers against each other: for
        each request, DecisionLogger's last transition and
        AuditComplianceAgent's final_decision should tell a
        consistent story - neither should contradict the other about
        what actually happened.
        """
        pipeline = RequestPipeline()
        records = run_a_realistic_session(pipeline)

        for record in records:
            request_entries = pipeline.decision_logger.entries_for_request(record.request_id)
            assert len(request_entries) > 0, f"No DecisionLogger entries for {record.request_id}"

            last_logged_state = request_entries[-1].to_state
            if record.final_decision == "GRANTED":
                assert last_logged_state in ("closed", "access_granted", "audit_logging")
            elif record.final_decision == "DENIED":
                assert last_logged_state in ("denied_final", "hard_denied")
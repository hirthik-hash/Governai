# backend/tests/test_api_day69.py

"""
Day 69: HTTP layer over RequestPipeline and FailureRecoveryAgent.
Rewritten on Day 77 for the authenticated API: every call now carries a
real bearer token (see tests/api_harness.py), health endpoints need the
admin role, and escalations are resolved by their designated approver.
"""

import pytest

from fsm.states import SystemState
from tests.api_harness import ADMIN_ID, ApiHarness

ESCALATING = {"resource_id": "resource-003"}          # user-003 -> approver user-004
GRANTED = {"resource_id": "resource-001"}
REQUESTER, APPROVER = "user-003", "user-004"


@pytest.fixture
def harness():
    return ApiHarness()


class TestSystemHealth:

    def test_healthy_system_reports_normal(self, harness):
        response = harness.as_user(ADMIN_ID).get("/system/health")

        assert response.status_code == 200
        body = response.json()
        assert body["system_state"] == "system_normal"
        assert body["safe_mode"] is False
        assert body["overall_healthy"] is True
        assert body["unhealthy_checks"] == []
        assert {c["name"] for c in body["checks"]} == {
            "database_connectivity", "agent_heartbeats", "fsm_integrity",
        }

    def test_get_health_is_read_only_even_when_unhealthy(self):
        admin = ApiHarness(failing_health=True).as_user(ADMIN_ID)

        for _ in range(3):
            body = admin.get("/system/health").json()

        assert body["overall_healthy"] is False
        assert body["unhealthy_checks"] == ["fsm_integrity"]
        assert body["system_state"] == "system_normal"  # GET never transitions

    def test_evaluate_drives_recovery_fsm_into_safe_mode(self):
        admin = ApiHarness(failing_health=True).as_user(ADMIN_ID)

        first = admin.post("/system/health/evaluate").json()
        second = admin.post("/system/health/evaluate").json()

        assert first["fsm_transitioned"] is True
        assert first["critical_failure_detected"] is True
        assert first["system_state"] == "degraded_warning"
        assert second["system_state"] == "safe_mode_active"
        assert second["safe_mode"] is True
        assert admin.get("/system/health").json()["safe_mode"] is True

    def test_evaluate_on_healthy_system_does_not_transition(self, harness):
        body = harness.as_user(ADMIN_ID).post("/system/health/evaluate").json()

        assert body["fsm_transitioned"] is False
        assert body["system_state"] == "system_normal"


class TestSubmitRequest:

    def test_granted_request_returns_audit_record_about_the_caller(self, harness):
        response = harness.as_user("user-007").post("/requests", json=GRANTED)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "granted"
        assert body["request_id"].startswith("req-")
        assert body["audit_record"]["final_decision"] == "GRANTED"
        assert body["audit_record"]["user_id"] == "user-007"

    def test_client_supplied_request_id_is_preserved(self, harness):
        body = harness.as_user("user-007").post("/requests", json={**GRANTED, "request_id": "req-mine-001"}).json()

        assert body["request_id"] == "req-mine-001"

    def test_denied_request_is_http_200_with_denied_status(self, harness):
        response = harness.as_user("user-009").post("/requests", json=GRANTED)  # blacklisted

        assert response.status_code == 200
        assert response.json()["status"] == "denied"

    def test_ambiguous_resource_name_needs_clarification(self, harness):
        response = harness.as_user("user-001").post("/requests", json={"resource_name": "e"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "clarification_needed"
        assert len(body["candidate_resource_ids"]) >= 2

    def test_expired_token_is_a_401_before_the_pipeline_runs(self, harness):
        session = harness.as_user("user-007")
        harness.clock.advance(3600)

        assert session.post("/requests", json=GRANTED).status_code == 401

    def test_unknown_fields_are_rejected_not_ignored(self, harness):
        response = harness.as_user("user-007").post("/requests", json={**GRANTED, "session_token": "abc"})

        assert response.status_code == 422

    def test_request_blocked_with_503_when_safe_mode_reached_via_api(self):
        harness = ApiHarness(failing_health=True)
        harness.force_safe_mode()

        response = harness.as_user("user-007").post("/requests", json=GRANTED)

        assert response.status_code == 503
        assert response.json()["status"] == "blocked_safe_mode"


class TestEscalationResolution:

    def test_escalation_is_202_then_approval_grants(self, harness):
        pending = harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-esc-001"})

        assert pending.status_code == 202
        body = pending.json()
        assert body["status"] == "pending_approval"
        assert body["notification"]["approver_user_id"] == APPROVER

        resolved = harness.as_user(APPROVER).post("/requests/req-esc-001/resolve", json={"decision": "approved"})

        assert resolved.status_code == 200
        assert resolved.json()["status"] == "granted"

    def test_rejection_denies(self, harness):
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-esc-002"})

        resolved = harness.as_user(APPROVER).post("/requests/req-esc-002/resolve", json={"decision": "rejected"})

        assert resolved.status_code == 200
        assert resolved.json()["status"] == "denied"

    def test_unknown_request_id_is_a_404(self, harness):
        response = harness.as_user(APPROVER).post("/requests/req-nope/resolve", json={"decision": "approved"})

        assert response.status_code == 404
        assert response.json()["status"] == "error"

    def test_resolving_twice_fails_the_second_time(self, harness):
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-esc-003"})
        approver = harness.as_user(APPROVER)
        approver.post("/requests/req-esc-003/resolve", json={"decision": "approved"})

        second = approver.post("/requests/req-esc-003/resolve", json={"decision": "approved"})

        assert second.status_code == 404

    def test_invalid_decision_is_rejected_by_schema(self, harness):
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-esc-004"})

        response = harness.as_user(APPROVER).post("/requests/req-esc-004/resolve", json={"decision": "maybe"})

        assert response.status_code == 422

    def test_no_decision_and_no_timeout_keeps_request_pending(self, harness):
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-esc-005"})
        approver = harness.as_user(APPROVER)

        undecided = approver.post("/requests/req-esc-005/resolve", json={})
        later = approver.post("/requests/req-esc-005/resolve", json={"decision": "approved"})

        assert undecided.status_code == 400
        assert later.json()["status"] == "granted"

    def test_approval_is_blocked_by_safe_mode_and_succeeds_after_recovery(self):
        harness = ApiHarness(failing_health=True)
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-esc-006"})
        harness.force_safe_mode()
        approver = harness.as_user(APPROVER)

        blocked = approver.post("/requests/req-esc-006/resolve", json={"decision": "approved"})
        assert blocked.status_code == 503

        harness.pipeline.recovery_fsm.state = SystemState.SYSTEM_NORMAL
        resolved = approver.post("/requests/req-esc-006/resolve", json={"decision": "approved"})

        assert resolved.json()["status"] == "granted"


class TestSharedStateAndIsolation:

    def test_pending_escalation_survives_between_http_calls(self, harness):
        # Proves one shared pipeline per app: request 1 parks state,
        # request 2 (a separate HTTP call) finds it.
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-share-001"})

        response = harness.as_user(APPROVER).post("/requests/req-share-001/resolve", json={"decision": "approved"})

        assert response.status_code == 200

    def test_two_apps_do_not_share_state(self):
        app_a, app_b = ApiHarness(), ApiHarness()
        app_a.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-iso-001"})

        response = app_b.as_user(APPROVER).post("/requests/req-iso-001/resolve", json={"decision": "approved"})

        assert response.status_code == 404

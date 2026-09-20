# backend/tests/test_api_day69.py

"""
Day 69: HTTP layer over RequestPipeline and FailureRecoveryAgent.

Uses FastAPI's TestClient against a fresh create_app() per test - no
real server, no network, and no state shared between tests. The
pipeline underneath is the real one; only the health checks are
swapped for a failing double where a test needs a real safe-mode
transition.
"""

import pytest
from fastapi.testclient import TestClient

from agents.recovery_agent import FailureRecoveryAgent, HealthCheckResult
from api.app import create_app
from core.orchestrator import RequestPipeline

ESCALATING = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"}
GRANTED = {"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"}


@pytest.fixture
def client():
    return TestClient(create_app())


def _failing_integrity() -> HealthCheckResult:
    return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="simulated failure")


def _client_with_failing_health():
    pipeline = RequestPipeline()
    agent = FailureRecoveryAgent(checks=[_failing_integrity], recovery_fsm=pipeline.recovery_fsm)
    return TestClient(create_app(pipeline=pipeline, recovery_agent=agent))


class TestSystemHealth:

    def test_healthy_system_reports_normal(self, client):
        response = client.get("/system/health")

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
        client = _client_with_failing_health()

        for _ in range(3):
            body = client.get("/system/health").json()

        assert body["overall_healthy"] is False
        assert body["unhealthy_checks"] == ["fsm_integrity"]
        assert body["system_state"] == "system_normal"  # GET never transitions

    def test_evaluate_drives_recovery_fsm_into_safe_mode(self):
        client = _client_with_failing_health()

        first = client.post("/system/health/evaluate").json()
        second = client.post("/system/health/evaluate").json()

        assert first["fsm_transitioned"] is True
        assert first["critical_failure_detected"] is True
        assert first["system_state"] == "degraded_warning"
        assert second["system_state"] == "safe_mode_active"
        assert second["safe_mode"] is True
        assert client.get("/system/health").json()["safe_mode"] is True

    def test_evaluate_on_healthy_system_does_not_transition(self, client):
        body = client.post("/system/health/evaluate").json()

        assert body["fsm_transitioned"] is False
        assert body["system_state"] == "system_normal"


class TestSubmitRequest:

    def test_granted_request_returns_audit_record(self, client):
        response = client.post("/requests", json=GRANTED)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "granted"
        assert body["request_id"].startswith("req-")
        assert body["audit_record"]["final_decision"] == "GRANTED"

    def test_client_supplied_request_id_is_preserved(self, client):
        body = client.post("/requests", json={**GRANTED, "request_id": "req-mine-001"}).json()

        assert body["request_id"] == "req-mine-001"

    def test_denied_request_is_http_200_with_denied_status(self, client):
        response = client.post("/requests", json={
            "user_id": "user-009", "resource_id": "resource-001", "session_token": "abc",
        })

        assert response.status_code == 200
        assert response.json()["status"] == "denied"

    def test_ambiguous_resource_name_needs_clarification(self, client):
        response = client.post("/requests", json={
            "user_id": "user-001", "resource_name": "e", "session_token": "abc",
        })

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "clarification_needed"
        assert len(body["candidate_resource_ids"]) >= 2

    def test_expired_session_is_a_400_error(self, client):
        response = client.post("/requests", json={**GRANTED, "session_expired": True})

        assert response.status_code == 400
        assert response.json()["status"] == "error"

    def test_missing_user_id_is_rejected_by_schema(self, client):
        response = client.post("/requests", json={"resource_id": "resource-001"})

        assert response.status_code == 422

    def test_request_blocked_with_503_when_safe_mode_reached_via_api(self):
        client = _client_with_failing_health()
        client.post("/system/health/evaluate")
        client.post("/system/health/evaluate")

        response = client.post("/requests", json=GRANTED)

        assert response.status_code == 503
        assert response.json()["status"] == "blocked_safe_mode"


class TestEscalationResolution:

    def test_escalation_is_202_then_approval_grants(self, client):
        pending = client.post("/requests", json={**ESCALATING, "request_id": "req-esc-001"})

        assert pending.status_code == 202
        body = pending.json()
        assert body["status"] == "pending_approval"
        assert body["notification"]["approver_user_id"]

        resolved = client.post("/requests/req-esc-001/resolve", json={"decision": "approved"})

        assert resolved.status_code == 200
        assert resolved.json()["status"] == "granted"

    def test_rejection_denies(self, client):
        client.post("/requests", json={**ESCALATING, "request_id": "req-esc-002"})

        resolved = client.post("/requests/req-esc-002/resolve", json={"decision": "rejected"})

        assert resolved.status_code == 200
        assert resolved.json()["status"] == "denied"

    def test_unknown_request_id_is_an_error(self, client):
        response = client.post("/requests/req-nope/resolve", json={"decision": "approved"})

        assert response.status_code == 400
        assert response.json()["status"] == "error"

    def test_resolving_twice_fails_the_second_time(self, client):
        client.post("/requests", json={**ESCALATING, "request_id": "req-esc-003"})
        client.post("/requests/req-esc-003/resolve", json={"decision": "approved"})

        second = client.post("/requests/req-esc-003/resolve", json={"decision": "approved"})

        assert second.status_code == 400

    def test_invalid_decision_is_rejected_by_schema(self, client):
        client.post("/requests", json={**ESCALATING, "request_id": "req-esc-004"})

        response = client.post("/requests/req-esc-004/resolve", json={"decision": "maybe"})

        assert response.status_code == 422

    def test_no_decision_and_no_timeout_keeps_request_pending(self, client):
        client.post("/requests", json={**ESCALATING, "request_id": "req-esc-005"})

        undecided = client.post("/requests/req-esc-005/resolve", json={})
        later = client.post("/requests/req-esc-005/resolve", json={"decision": "approved"})

        assert undecided.status_code == 400
        assert later.json()["status"] == "granted"

    def test_approval_is_blocked_by_safe_mode_and_succeeds_after_recovery(self):
        pipeline = RequestPipeline()
        agent = FailureRecoveryAgent(checks=[_failing_integrity], recovery_fsm=pipeline.recovery_fsm)
        client = TestClient(create_app(pipeline=pipeline, recovery_agent=agent))
        client.post("/requests", json={**ESCALATING, "request_id": "req-esc-006"})
        client.post("/system/health/evaluate")
        client.post("/system/health/evaluate")

        blocked = client.post("/requests/req-esc-006/resolve", json={"decision": "approved"})
        assert blocked.status_code == 503

        from fsm.states import SystemState
        pipeline.recovery_fsm.state = SystemState.SYSTEM_NORMAL
        resolved = client.post("/requests/req-esc-006/resolve", json={"decision": "approved"})

        assert resolved.json()["status"] == "granted"


class TestSharedStateAndIsolation:

    def test_pending_escalation_survives_between_http_calls(self, client):
        # Proves one shared pipeline per app: request 1 parks state,
        # request 2 (a separate HTTP call) finds it.
        client.post("/requests", json={**ESCALATING, "request_id": "req-share-001"})

        assert client.post("/requests/req-share-001/resolve", json={"decision": "approved"}).status_code == 200

    def test_two_apps_do_not_share_state(self):
        app_a, app_b = TestClient(create_app()), TestClient(create_app())
        app_a.post("/requests", json={**ESCALATING, "request_id": "req-iso-001"})

        response = app_b.post("/requests/req-iso-001/resolve", json={"decision": "approved"})

        assert response.status_code == 400

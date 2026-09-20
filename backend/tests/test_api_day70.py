# backend/tests/test_api_day70.py

"""
Day 70: duplicate-id conflicts, the pending-escalation list, and the
read-only audit endpoints.
"""

import pytest
from fastapi.testclient import TestClient

from api.app import create_app

ESCALATING = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"}
GRANTED = {"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"}
BLACKLISTED = {"user_id": "user-009", "resource_id": "resource-001", "session_token": "abc"}


@pytest.fixture
def client():
    return TestClient(create_app())


class TestDuplicateRequestConflict:

    def test_resubmitting_a_pending_id_is_409(self, client):
        client.post("/requests", json={**ESCALATING, "request_id": "req-409-001"})

        response = client.post("/requests", json={**ESCALATING, "request_id": "req-409-001"})

        assert response.status_code == 409
        assert "Duplicate request_id" in response.json()["errors"][0]

    def test_original_still_resolves_after_a_rejected_duplicate(self, client):
        client.post("/requests", json={**ESCALATING, "request_id": "req-409-002"})
        client.post("/requests", json={**ESCALATING, "request_id": "req-409-002"})

        resolved = client.post("/requests/req-409-002/resolve", json={"decision": "approved"})

        assert resolved.status_code == 200
        assert resolved.json()["status"] == "granted"


class TestPendingList:

    def test_empty_when_nothing_is_pending(self, client):
        assert client.get("/requests/pending").json() == {"count": 0, "pending": []}

    def test_lists_pending_and_shrinks_as_they_resolve(self, client):
        client.post("/requests", json={**ESCALATING, "request_id": "req-list-001"})
        client.post("/requests", json={**ESCALATING, "request_id": "req-list-002"})

        body = client.get("/requests/pending").json()
        assert body["count"] == 2
        assert [p["request_id"] for p in body["pending"]] == ["req-list-001", "req-list-002"]
        assert body["pending"][0]["approver_user_id"]

        client.post("/requests/req-list-001/resolve", json={"decision": "rejected"})

        assert [p["request_id"] for p in client.get("/requests/pending").json()["pending"]] == ["req-list-002"]

    def test_pending_route_is_not_swallowed_by_the_resolve_route(self, client):
        assert client.get("/requests/pending").status_code == 200


class TestAuditEndpoints:

    def test_empty_ledger(self, client):
        assert client.get("/audit").json() == {"count": 0, "records": []}
        summary = client.get("/audit/summary").json()
        assert summary["total_requests"] == 0

    def test_records_appear_after_requests_complete(self, client):
        client.post("/requests", json={**GRANTED, "request_id": "req-aud-001"})
        client.post("/requests", json={**BLACKLISTED, "request_id": "req-aud-002"})

        body = client.get("/audit").json()

        assert body["count"] == 2
        assert {r["request_id"] for r in body["records"]} == {"req-aud-001", "req-aud-002"}

    def test_pending_escalation_is_not_in_the_ledger_until_resolved(self, client):
        client.post("/requests", json={**ESCALATING, "request_id": "req-aud-003"})
        assert client.get("/audit").json()["count"] == 0

        client.post("/requests/req-aud-003/resolve", json={"decision": "approved"})

        body = client.get("/audit").json()
        assert body["count"] == 1
        assert body["records"][0]["final_decision"] == "GRANTED"
        assert body["records"][0]["approver_user_id"]

    def test_filters_combine(self, client):
        client.post("/requests", json={**GRANTED, "request_id": "req-aud-004"})
        client.post("/requests", json={**BLACKLISTED, "request_id": "req-aud-005"})

        assert client.get("/audit", params={"final_decision": "DENIED"}).json()["count"] == 1
        assert client.get("/audit", params={"user_id": "user-007"}).json()["count"] == 1
        assert client.get("/audit", params={"user_id": "user-007", "final_decision": "DENIED"}).json()["count"] == 0
        assert client.get("/audit", params={"start_date": "9999-01-01"}).json()["count"] == 0

    def test_sorting_by_risk_score_descending(self, client):
        client.post("/requests", json={**GRANTED, "request_id": "req-aud-006"})
        client.post("/requests", json={**BLACKLISTED, "request_id": "req-aud-007"})

        scores = [
            r["risk_score"]
            for r in client.get("/audit", params={"sort_by": "risk_score", "descending": True}).json()["records"]
        ]

        assert scores == sorted(scores, reverse=True)

    def test_invalid_sort_field_is_422(self, client):
        assert client.get("/audit", params={"sort_by": "password"}).status_code == 422

    def test_summary_matches_the_ledger(self, client):
        client.post("/requests", json={**GRANTED, "request_id": "req-aud-008"})
        client.post("/requests", json={**BLACKLISTED, "request_id": "req-aud-009"})

        summary = client.get("/audit/summary").json()

        assert summary["total_requests"] == 2
        assert summary["decision_breakdown"] == {"GRANTED": 1, "DENIED": 1}

    def test_summary_honors_filters(self, client):
        client.post("/requests", json={**GRANTED, "request_id": "req-aud-010"})
        client.post("/requests", json={**BLACKLISTED, "request_id": "req-aud-011"})

        summary = client.get("/audit/summary", params={"final_decision": "DENIED"}).json()

        assert summary["total_requests"] == 1

# backend/tests/test_api_day70.py

"""
Day 70: duplicate-id conflicts, the pending-escalation list, and the
read-only audit endpoints. Rewritten on Day 77 for the authenticated API
(audit is admin-only; the pending list is filtered by caller).
"""

import pytest

from tests.api_harness import ADMIN_ID, ApiHarness

ESCALATING = {"resource_id": "resource-003"}   # user-003 -> approver user-004
GRANTED = {"resource_id": "resource-001"}
REQUESTER, APPROVER = "user-003", "user-004"


@pytest.fixture
def harness():
    return ApiHarness()


@pytest.fixture
def admin(harness):
    return harness.as_user(ADMIN_ID)


class TestDuplicateRequestConflict:

    def test_resubmitting_a_pending_id_is_409(self, harness):
        requester = harness.as_user(REQUESTER)
        requester.post("/requests", json={**ESCALATING, "request_id": "req-409-001"})

        response = requester.post("/requests", json={**ESCALATING, "request_id": "req-409-001"})

        assert response.status_code == 409
        assert "Duplicate request_id" in response.json()["errors"][0]

    def test_original_still_resolves_after_a_rejected_duplicate(self, harness):
        requester = harness.as_user(REQUESTER)
        requester.post("/requests", json={**ESCALATING, "request_id": "req-409-002"})
        requester.post("/requests", json={**ESCALATING, "request_id": "req-409-002"})

        resolved = harness.as_user(APPROVER).post("/requests/req-409-002/resolve", json={"decision": "approved"})

        assert resolved.status_code == 200
        assert resolved.json()["status"] == "granted"


class TestPendingList:

    def test_empty_when_nothing_is_pending(self, admin):
        assert admin.get("/requests/pending").json() == {"count": 0, "pending": []}

    def test_lists_pending_and_shrinks_as_they_resolve(self, harness, admin):
        requester = harness.as_user(REQUESTER)
        requester.post("/requests", json={**ESCALATING, "request_id": "req-list-001"})
        requester.post("/requests", json={**ESCALATING, "request_id": "req-list-002"})

        body = admin.get("/requests/pending").json()
        assert body["count"] == 2
        assert [p["request_id"] for p in body["pending"]] == ["req-list-001", "req-list-002"]
        assert body["pending"][0]["approver_user_id"] == APPROVER

        harness.as_user(APPROVER).post("/requests/req-list-001/resolve", json={"decision": "rejected"})

        assert [p["request_id"] for p in admin.get("/requests/pending").json()["pending"]] == ["req-list-002"]

    def test_pending_route_is_not_swallowed_by_the_resolve_route(self, admin):
        assert admin.get("/requests/pending").status_code == 200


class TestAuditEndpoints:

    def test_empty_ledger(self, admin):
        assert admin.get("/audit").json() == {"count": 0, "records": []}
        assert admin.get("/audit/summary").json()["total_requests"] == 0

    def test_records_appear_after_requests_complete(self, harness, admin):
        harness.as_user("user-007").post("/requests", json={**GRANTED, "request_id": "req-aud-001"})
        harness.as_user("user-009").post("/requests", json={**GRANTED, "request_id": "req-aud-002"})

        body = admin.get("/audit").json()

        assert body["count"] == 2
        assert {r["request_id"] for r in body["records"]} == {"req-aud-001", "req-aud-002"}

    def test_pending_escalation_is_not_in_the_ledger_until_resolved(self, harness, admin):
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-aud-003"})
        assert admin.get("/audit").json()["count"] == 0

        harness.as_user(APPROVER).post("/requests/req-aud-003/resolve", json={"decision": "approved"})

        body = admin.get("/audit").json()
        assert body["count"] == 1
        assert body["records"][0]["final_decision"] == "GRANTED"
        assert body["records"][0]["approver_user_id"] == APPROVER

    def test_filters_combine(self, harness, admin):
        harness.as_user("user-007").post("/requests", json={**GRANTED, "request_id": "req-aud-004"})
        harness.as_user("user-009").post("/requests", json={**GRANTED, "request_id": "req-aud-005"})

        assert admin.get("/audit", params={"final_decision": "DENIED"}).json()["count"] == 1
        assert admin.get("/audit", params={"user_id": "user-007"}).json()["count"] == 1
        assert admin.get("/audit", params={"user_id": "user-007", "final_decision": "DENIED"}).json()["count"] == 0
        assert admin.get("/audit", params={"start_date": "9999-01-01"}).json()["count"] == 0

    def test_sorting_by_risk_score_descending(self, harness, admin):
        harness.as_user("user-007").post("/requests", json={**GRANTED, "request_id": "req-aud-006"})
        harness.as_user("user-009").post("/requests", json={**GRANTED, "request_id": "req-aud-007"})

        scores = [
            r["risk_score"]
            for r in admin.get("/audit", params={"sort_by": "risk_score", "descending": True}).json()["records"]
        ]

        assert scores == sorted(scores, reverse=True)

    def test_invalid_sort_field_is_422(self, admin):
        assert admin.get("/audit", params={"sort_by": "password"}).status_code == 422

    def test_summary_matches_the_ledger(self, harness, admin):
        harness.as_user("user-007").post("/requests", json={**GRANTED, "request_id": "req-aud-008"})
        harness.as_user("user-009").post("/requests", json={**GRANTED, "request_id": "req-aud-009"})

        summary = admin.get("/audit/summary").json()

        assert summary["total_requests"] == 2
        assert summary["decision_breakdown"] == {"GRANTED": 1, "DENIED": 1}

    def test_summary_honors_filters(self, harness, admin):
        harness.as_user("user-007").post("/requests", json={**GRANTED, "request_id": "req-aud-010"})
        harness.as_user("user-009").post("/requests", json={**GRANTED, "request_id": "req-aud-011"})

        assert admin.get("/audit/summary", params={"final_decision": "DENIED"}).json()["total_requests"] == 1

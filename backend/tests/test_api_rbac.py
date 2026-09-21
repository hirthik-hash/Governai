# backend/tests/test_api_rbac.py

"""
Day 77: who may call what.

  - every route except /healthz and /auth/login needs a valid token (401);
  - the audit ledger and the health endpoints need the admin role (403);
  - requests are submitted only as yourself, with identity, session and
    job title taken from the server, never from the request body;
  - only the designated approver can resolve an escalation, and nobody
    else can even tell a pending request exists.
"""

import pytest

from fsm.states import SystemState
from tests.api_harness import ADMIN_ID, ApiHarness

REQUESTER, APPROVER = "user-003", "user-004"     # user-003's escalations go to user-004
ESCALATING = {"resource_id": "resource-003"}
GRANTED = {"resource_id": "resource-001"}

PROTECTED = [
    ("get", "/auth/me", None),
    ("post", "/requests", GRANTED),
    ("get", "/requests/pending", None),
    ("post", "/requests/req-x/resolve", {"decision": "approved"}),
    ("get", "/audit", None),
    ("get", "/audit/summary", None),
    ("get", "/system/health", None),
    ("post", "/system/health/evaluate", None),
]
ADMIN_ONLY = [
    ("get", "/audit"),
    ("get", "/audit/summary"),
    ("get", "/system/health"),
    ("post", "/system/health/evaluate"),
]


@pytest.fixture
def harness():
    return ApiHarness()


class TestAuthenticationIsRequired:

    @pytest.mark.parametrize("method,url,body", PROTECTED, ids=[f"{m}-{u}" for m, u, _ in PROTECTED])
    def test_no_token_is_401(self, harness, method, url, body):
        response = getattr(harness.client, method)(url, **({"json": body} if body is not None else {}))

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_authentication_is_checked_before_the_body(self, harness):
        # A malformed body must not be reported to an unauthenticated caller.
        assert harness.client.post("/requests", json={"totally": "wrong"}).status_code == 401

    def test_liveness_probe_is_public_and_reveals_nothing(self, harness):
        response = harness.client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_login_is_public(self, harness):
        assert harness.client.post("/auth/login", json={"user_id": "user-001", "password": "nope-nope-nope"}).status_code == 401


class TestAdminOnlyRoutes:

    @pytest.mark.parametrize("method,url", ADMIN_ONLY, ids=[f"{m}-{u}" for m, u in ADMIN_ONLY])
    def test_ordinary_user_is_403(self, harness, method, url):
        response = getattr(harness.as_user("user-001"), method)(url)

        assert response.status_code == 403
        assert response.json() == {"detail": "Forbidden"}

    @pytest.mark.parametrize("method,url", ADMIN_ONLY, ids=[f"{m}-{u}" for m, u in ADMIN_ONLY])
    def test_admin_is_allowed(self, harness, method, url):
        assert getattr(harness.as_user(ADMIN_ID), method)(url).status_code == 200

    def test_a_senior_job_title_is_not_an_api_role(self, harness):
        # user-006 is HR Director (clearance 4) - still not an admin.
        assert harness.as_user("user-006").get("/audit").status_code == 403

    def test_role_changes_take_effect_on_existing_tokens(self, harness):
        session = harness.as_user("user-001")
        assert session.get("/audit").status_code == 403

        harness.auth.set_role("user-001", "admin")
        assert session.get("/audit").status_code == 200

        harness.auth.set_role("user-001", "user")
        assert session.get("/audit").status_code == 403


class TestSubmittingAsYourself:

    def test_submitting_as_another_user_is_403(self, harness):
        response = harness.as_user("user-001").post("/requests", json={**GRANTED, "user_id": "user-002"})

        assert response.status_code == 403
        assert harness.pipeline.audit_records == []

    def test_admins_cannot_submit_on_behalf_of_others_either(self, harness):
        response = harness.as_user(ADMIN_ID).post("/requests", json={**GRANTED, "user_id": "user-001"})

        assert response.status_code == 403

    def test_naming_yourself_is_fine(self, harness):
        response = harness.as_user("user-007").post("/requests", json={**GRANTED, "user_id": "user-007"})

        assert response.status_code == 200

    @pytest.mark.parametrize("field,value", [
        ("role", "CISO"),
        ("session_token", "abc"),
        ("session_expired", False),
        ("is_public_readonly", True),
        ("clearance", 5),
    ])
    def test_fields_the_server_decides_are_rejected_not_ignored(self, harness, field, value):
        response = harness.as_user("user-001").post("/requests", json={**GRANTED, field: value})

        assert response.status_code == 422

    def test_identity_session_and_job_title_come_from_the_server(self, harness):
        seen = []
        original = harness.pipeline.submit_request
        harness.pipeline.submit_request = lambda data: (seen.append(dict(data)), original(data))[1]
        session = harness.as_user("user-001")

        session.post("/requests", json=GRANTED)

        assert seen[0]["user_id"] == "user-001"
        assert seen[0]["session_token"] == session.token
        assert seen[0]["role"] == "Software Engineer"   # user-001's job title in the directory

    def test_safe_mode_cannot_be_bypassed_even_for_a_public_resource(self):
        harness = ApiHarness(failing_health=True)
        harness.force_safe_mode()

        response = harness.as_user("user-007").post("/requests", json=GRANTED)  # resource-001 is public

        assert response.status_code == 503
        assert response.json()["status"] == "blocked_safe_mode"

    def test_a_token_only_works_for_its_own_user_inside_the_pipeline_too(self, harness):
        stolen_token = harness.as_user("user-001").token
        data = {"user_id": "user-007", "resource_id": "resource-001", "session_token": stolen_token}

        result = harness.pipeline.submit_request(data)

        assert result.status == "error"
        assert "does not belong" in result.errors[0]


class TestResolvingEscalations:

    def _pending(self, harness, request_id="req-rbac-1"):
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": request_id})
        return request_id

    def test_the_designated_approver_can_resolve(self, harness):
        request_id = self._pending(harness)

        response = harness.as_user(APPROVER).post(f"/requests/{request_id}/resolve", json={"decision": "approved"})

        assert response.status_code == 200 and response.json()["status"] == "granted"

    @pytest.mark.parametrize("who", ["user-001", "user-002", REQUESTER, ADMIN_ID])
    def test_everyone_else_gets_a_404_and_the_request_stays_pending(self, harness, who):
        request_id = self._pending(harness)

        response = harness.as_user(who).post(f"/requests/{request_id}/resolve", json={"decision": "approved"})

        assert response.status_code == 404
        assert harness.pipeline.has_pending_request(request_id)

    def test_a_non_approver_cannot_tell_a_pending_id_from_a_missing_one(self, harness):
        request_id = self._pending(harness)
        outsider = harness.as_user("user-001")

        real = outsider.post(f"/requests/{request_id}/resolve", json={"decision": "approved"})
        missing = outsider.post("/requests/req-does-not-exist/resolve", json={"decision": "approved"})

        assert real.status_code == missing.status_code == 404
        assert real.json()["errors"][0].replace(request_id, "X") == missing.json()["errors"][0].replace("req-does-not-exist", "X")

    def test_requesters_cannot_approve_their_own_request(self, harness):
        request_id = self._pending(harness)

        assert harness.as_user(REQUESTER).post(f"/requests/{request_id}/resolve", json={"decision": "approved"}).status_code == 404

    def test_an_admin_may_run_the_timeout_check_but_not_approve(self, harness):
        request_id = self._pending(harness)
        admin = harness.as_user(ADMIN_ID)

        timeout_check = admin.post(f"/requests/{request_id}/resolve", json={})
        approval = admin.post(f"/requests/{request_id}/resolve", json={"decision": "approved"})

        assert timeout_check.status_code == 400       # allowed through; simply still pending
        assert approval.status_code == 404

    def test_an_ordinary_user_cannot_run_the_timeout_check(self, harness):
        request_id = self._pending(harness)

        assert harness.as_user("user-001").post(f"/requests/{request_id}/resolve", json={}).status_code == 404

    def test_the_ciso_can_approve_what_was_routed_to_them(self, harness):
        # user-001 -> user-002 (clearance 2, too low) -> user-007.
        harness.as_user("user-001").post("/requests", json={**ESCALATING, "request_id": "req-ciso-1"})

        response = harness.as_user(ADMIN_ID).post("/requests/req-ciso-1/resolve", json={"decision": "approved"})

        assert response.status_code == 200


class TestPendingListVisibility:

    def _two_pending(self, harness):
        harness.as_user(REQUESTER).post("/requests", json={**ESCALATING, "request_id": "req-vis-1"})   # -> user-004
        harness.as_user("user-001").post("/requests", json={**ESCALATING, "request_id": "req-vis-2"})  # -> user-007

    def _ids(self, harness, who):
        return [p["request_id"] for p in harness.as_user(who).get("/requests/pending").json()["pending"]]

    def test_an_approver_sees_only_their_own(self, harness):
        self._two_pending(harness)

        assert self._ids(harness, APPROVER) == ["req-vis-1"]

    def test_an_admin_sees_all_of_them(self, harness):
        self._two_pending(harness)

        assert self._ids(harness, ADMIN_ID) == ["req-vis-1", "req-vis-2"]

    def test_a_user_with_nothing_assigned_sees_none(self, harness):
        self._two_pending(harness)

        assert self._ids(harness, "user-002") == []
        assert self._ids(harness, REQUESTER) == []


class TestTokenLifecycleThroughTheApi:

    def test_a_token_stops_working_when_it_expires_mid_session(self, harness):
        session = harness.as_user("user-007")
        assert session.get("/audit").status_code == 200

        harness.clock.advance(3600)

        assert session.get("/audit").status_code == 401

    def test_a_user_removed_from_the_directory_loses_access_immediately(self, harness):
        session = harness.as_user("user-007")
        harness.users.remove(next(u for u in harness.users if u.id == "user-007"))

        assert session.get("/audit").status_code == 401

    def test_safe_mode_state_is_unaffected_by_authorization_failures(self):
        harness = ApiHarness(failing_health=True)
        harness.as_user("user-001").post("/system/health/evaluate")  # 403, must not transition

        assert harness.pipeline.recovery_fsm.state == SystemState.SYSTEM_NORMAL

# backend/tests/test_seed_data.py

import pytest
from data.seed_data import (
    SEED_USERS,
    SEED_RESOURCES,
    Sensitivity,
    get_user,
    get_resource,
    build_fsm_context,
)
from fsm.governance_fsm import GovernanceFSM
from fsm.states import RequestState


class TestSeedDataIntegrity:

    def test_at_least_one_user_per_clearance_level_zero_to_five(self):
        levels_present = {user.clearance_level for user in SEED_USERS}
        for level in range(0, 6):
            assert level in levels_present, f"No seed user has clearance level {level}"

    def test_at_least_one_resource_per_sensitivity_level(self):
        sensitivities_present = {r.sensitivity for r in SEED_RESOURCES}
        for sensitivity in Sensitivity:
            assert sensitivity in sensitivities_present, f"No seed resource is {sensitivity.value}"

    def test_at_least_one_blacklisted_user_exists(self):
        assert any(user.is_blacklisted for user in SEED_USERS)

    def test_all_user_ids_are_unique(self):
        ids = [user.id for user in SEED_USERS]
        assert len(ids) == len(set(ids))

    def test_all_resource_ids_are_unique(self):
        ids = [r.id for r in SEED_RESOURCES]
        assert len(ids) == len(set(ids))

    def test_multiple_departments_represented(self):
        departments = {user.department for user in SEED_USERS}
        assert len(departments) >= 4


class TestLookupHelpers:

    def test_get_user_returns_correct_user(self):
        user = get_user("user-001")
        assert user.name == "Alex Chen"

    def test_get_user_raises_for_unknown_id(self):
        with pytest.raises(ValueError):
            get_user("user-999")

    def test_get_resource_returns_correct_resource(self):
        resource = get_resource("resource-005")
        assert resource.sensitivity == Sensitivity.TOP_SECRET

    def test_get_resource_raises_for_unknown_id(self):
        with pytest.raises(ValueError):
            get_resource("resource-999")


class TestBuildFsmContext:

    def test_sufficient_clearance_builds_authorizing_context(self):
        user = get_user("user-007")  # CISO, clearance 5
        resource = get_resource("resource-001")  # public, requires 0

        context = build_fsm_context(user, resource, risk_score=10)
        fsm = GovernanceFSM(request_id="seed-test-001")
        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED

    def test_insufficient_clearance_builds_escalating_context(self):
        user = get_user("user-008")  # Junior Developer, clearance 0
        resource = get_resource("resource-005")  # top secret, requires 5

        context = build_fsm_context(user, resource, risk_score=10)
        fsm = GovernanceFSM(request_id="seed-test-002")
        fsm.transition(context)
        fsm.transition(context)
        fsm.transition(context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.ESCALATION_REQUIRED

    def test_blacklisted_user_builds_hard_denying_context(self):
        user = get_user("user-009")  # blacklisted
        resource = get_resource("resource-007")  # public, requires 0 - shouldn't matter

        context = build_fsm_context(user, resource, risk_score=5)
        fsm = GovernanceFSM(request_id="seed-test-003")
        fsm.transition(context)
        fsm.transition(context)
        fsm.transition(context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.HARD_DENIED

    def test_required_clearance_derives_correctly_from_sensitivity(self):
        top_secret_resource = get_resource("resource-005")
        public_resource = get_resource("resource-001")

        assert top_secret_resource.required_clearance == 5
        assert public_resource.required_clearance == 0
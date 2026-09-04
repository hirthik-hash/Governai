# backend/tests/test_fsm_invalid_transitions.py

import pytest
from fsm.governance_fsm import GovernanceFSM, InvalidTransitionError
from fsm.states import RequestState


def make_context(**overrides) -> dict:
    base = {
        "ambiguity_flag": False,
        "clearance": 1,
        "required_clearance": 3,
        "risk_score": 10,
    }
    base.update(overrides)
    return base


class TestEscalationRequiredCannotSkipNotification:

    def test_transition_raises_without_escalation_sent(self):
        fsm = GovernanceFSM(request_id="inv-001")
        context = make_context()

        fsm.transition(context)  # -> REQUEST_RECEIVED
        fsm.transition(context)  # -> PARSING_REQUEST
        fsm.transition(context)  # -> VALIDATING_ACCESS
        fsm.transition(context)  # -> ESCALATION_REQUIRED

        with pytest.raises(InvalidTransitionError) as exc_info:
            fsm.transition(context)  # escalation_sent still False

        assert "inv-001" in str(exc_info.value)
        assert "escalation_required" in str(exc_info.value)


class TestManagerReviewCannotSkipDecision:

    def _reach_manager_review(self) -> tuple[GovernanceFSM, dict]:
        fsm = GovernanceFSM(request_id="inv-002")
        context = make_context()
        fsm.transition(context)
        fsm.transition(context)
        fsm.transition(context)
        fsm.transition(context)  # -> ESCALATION_REQUIRED
        context["escalation_sent"] = True
        fsm.transition(context)  # -> MANAGER_REVIEW
        return fsm, context

    def test_transition_raises_with_no_decision_made(self):
        fsm, context = self._reach_manager_review()

        with pytest.raises(InvalidTransitionError) as exc_info:
            fsm.transition(context)  # no approval, no rejection, no timeout

        assert "inv-002" in str(exc_info.value)
        assert "manager_review" in str(exc_info.value)

    def test_can_transition_agrees_with_transition_raising(self):
        """
        can_transition() and transition() must never disagree - if one
        says no, the other must refuse too. This is a consistency
        guarantee the engine should hold everywhere.
        """
        fsm, context = self._reach_manager_review()

        assert fsm.can_transition(context) is False

        with pytest.raises(InvalidTransitionError):
            fsm.transition(context)


class TestEmptyAndDefaultContexts:

    def test_completely_empty_context_does_not_crash_idle(self):
        """
        Early, unconditional transitions should work even with zero
        context - they don't depend on any fields.
        """
        fsm = GovernanceFSM(request_id="inv-003")
        new_state = fsm.transition({})
        assert new_state == RequestState.REQUEST_RECEIVED

    def test_missing_keys_use_safe_defaults_at_validating_access(self):
        fsm = GovernanceFSM(request_id="inv-004")
        context = {"ambiguity_flag": False}

        fsm.transition(context)  # -> REQUEST_RECEIVED
        fsm.transition(context)  # -> PARSING_REQUEST
        fsm.transition(context)  # -> VALIDATING_ACCESS
        new_state = fsm.transition(context)  # -> AUTHORIZED

        assert new_state == RequestState.AUTHORIZED
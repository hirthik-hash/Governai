# backend/tests/test_fsm_escalation.py

import pytest
from fsm.governance_fsm import GovernanceFSM
from fsm.states import RequestState
from fsm.governance_fsm import TerminalStateError


def make_context(**overrides) -> dict:
    base = {
        "ambiguity_flag": False,
        "clearance": 3,
        "required_clearance": 2,
        "risk_score": 10,
        "blacklist_match": False,
        "escalation_sent": False,
        "approval_token_valid": False,
        "rejected": False,
        "timed_out": False,
    }
    base.update(overrides)
    return base


def advance_to_validating_access(fsm: GovernanceFSM, context: dict):
    """Helper: walks the FSM from IDLE up to VALIDATING_ACCESS."""
    fsm.transition(context)  # IDLE -> REQUEST_RECEIVED
    fsm.transition(context)  # REQUEST_RECEIVED -> PARSING_REQUEST
    fsm.transition(context)  # PARSING_REQUEST -> VALIDATING_ACCESS


class TestEscalationTriggers:

    def test_insufficient_clearance_triggers_escalation(self):
        fsm = GovernanceFSM(request_id="esc-001")
        context = make_context(clearance=1, required_clearance=3, risk_score=10)

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.ESCALATION_REQUIRED

    def test_elevated_risk_triggers_escalation_even_with_sufficient_clearance(self):
        fsm = GovernanceFSM(request_id="esc-002")
        context = make_context(clearance=5, required_clearance=2, risk_score=60)

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.ESCALATION_REQUIRED

    def test_risk_score_at_escalation_floor_escalates(self):
        fsm = GovernanceFSM(request_id="esc-003")
        context = make_context(risk_score=40)

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.ESCALATION_REQUIRED

    def test_risk_score_just_under_hard_denial_still_escalates(self):
        fsm = GovernanceFSM(request_id="esc-004")
        context = make_context(risk_score=84)

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.ESCALATION_REQUIRED

    def test_risk_score_at_hard_denial_floor_does_not_escalate(self):
        fsm = GovernanceFSM(request_id="esc-005")
        context = make_context(risk_score=85)

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.HARD_DENIED


class TestEscalationRequiresNotification:

    def test_cannot_advance_without_escalation_sent(self):
        fsm = GovernanceFSM(request_id="esc-006")
        context = make_context(clearance=1, required_clearance=3)

        advance_to_validating_access(fsm, context)
        fsm.transition(context)  # -> ESCALATION_REQUIRED

        context["escalation_sent"] = False
        assert fsm.can_transition(context) is False

    def test_advances_to_manager_review_once_sent(self):
        fsm = GovernanceFSM(request_id="esc-007")
        context = make_context(clearance=1, required_clearance=3)

        advance_to_validating_access(fsm, context)
        fsm.transition(context)  # -> ESCALATION_REQUIRED

        context["escalation_sent"] = True
        new_state = fsm.transition(context)

        assert new_state == RequestState.MANAGER_REVIEW


class TestManagerReviewOutcomes:

    def _reach_manager_review(self, request_id: str) -> tuple[GovernanceFSM, dict]:
        fsm = GovernanceFSM(request_id=request_id)
        context = make_context(clearance=1, required_clearance=3)
        advance_to_validating_access(fsm, context)
        fsm.transition(context)  # -> ESCALATION_REQUIRED
        context["escalation_sent"] = True
        fsm.transition(context)  # -> MANAGER_REVIEW
        return fsm, context

    def test_manager_approval_grants_access(self):
        fsm, context = self._reach_manager_review("esc-008")

        context["approval_token_valid"] = True
        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED
        assert RequestState.ACCESS_GRANTED in [r.to_state for r in fsm.history]

    def test_manager_rejection_denies_access(self):
        fsm, context = self._reach_manager_review("esc-009")

        context["rejected"] = True
        new_state = fsm.transition(context)

        assert new_state == RequestState.DENIED_FINAL

    def test_timeout_denies_access(self):
        fsm, context = self._reach_manager_review("esc-010")

        context["timed_out"] = True
        new_state = fsm.transition(context)

        assert new_state == RequestState.DENIED_FINAL

    def test_rejection_and_timeout_are_distinguishable_in_history(self):
        fsm, context = self._reach_manager_review("esc-011")
        context["rejected"] = True
        fsm.transition(context)

        last_record = fsm.history[-1]
        assert "rejected" in last_record.description.lower()

    def test_denied_final_is_terminal(self):
        fsm, context = self._reach_manager_review("esc-012")
        context["rejected"] = True
        fsm.transition(context)  # -> DENIED_FINAL

        with pytest.raises(TerminalStateError):
            fsm.transition(context)
            
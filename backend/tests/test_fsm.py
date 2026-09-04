# backend/tests/test_fsm.py

import pytest
from fsm.governance_fsm import GovernanceFSM
from fsm.states import RequestState


def make_context(**overrides) -> dict:
    """
    Baseline context representing a well-formed, low-risk request.
    Individual tests override only the fields they care about.
    """
    base = {
        "ambiguity_flag": False,
        "clearance": 3,
        "required_clearance": 2,
        "risk_score": 10,
        "blacklist_match": False,
    }
    base.update(overrides)
    return base


class TestHappyPathAuthorization:

    def test_sufficient_clearance_low_risk_reaches_closed(self):
        fsm = GovernanceFSM(request_id="test-001")
        context = make_context()

        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED

    def test_full_transition_sequence_is_correct(self):
        fsm = GovernanceFSM(request_id="test-002")
        context = make_context()

        fsm.run_until_stuck(context)

        expected_sequence = [
            RequestState.REQUEST_RECEIVED,
            RequestState.PARSING_REQUEST,
            RequestState.VALIDATING_ACCESS,
            RequestState.AUTHORIZED,
            RequestState.ACCESS_GRANTED,
            RequestState.AUDIT_LOGGING,
            RequestState.CLOSED,
        ]
        actual_sequence = [record.to_state for record in fsm.history]

        assert actual_sequence == expected_sequence

    def test_risk_score_just_under_threshold_is_authorized(self):
        fsm = GovernanceFSM(request_id="test-003")
        context = make_context(risk_score=39)

        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED

    def test_risk_score_at_threshold_is_not_authorized(self):
        fsm = GovernanceFSM(request_id="test-004")
        context = make_context(risk_score=40)

        fsm.transition(context)  # IDLE -> REQUEST_RECEIVED
        fsm.transition(context)  # REQUEST_RECEIVED -> PARSING_REQUEST
        fsm.transition(context)  # PARSING_REQUEST -> VALIDATING_ACCESS
        new_state = fsm.transition(context)  # should NOT go to AUTHORIZED

        assert new_state == RequestState.ESCALATION_REQUIRED

    def test_exact_clearance_match_is_authorized(self):
        fsm = GovernanceFSM(request_id="test-005")
        context = make_context(clearance=2, required_clearance=2)

        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED

    def test_can_transition_reports_true_when_valid(self):
        fsm = GovernanceFSM(request_id="test-006")
        context = make_context()

        assert fsm.can_transition(context) is True

    def test_history_is_empty_before_any_transition(self):
        fsm = GovernanceFSM(request_id="test-007")

        assert fsm.history == []
        assert fsm.state == RequestState.IDLE
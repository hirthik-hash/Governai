# backend/tests/test_fsm_hard_denial.py

import pytest
from fsm.governance_fsm import GovernanceFSM, TerminalStateError
from fsm.states import RequestState
from fsm.transitions import TRANSITIONS


def make_context(**overrides) -> dict:
    base = {
        "ambiguity_flag": False,
        "clearance": 3,
        "required_clearance": 2,
        "risk_score": 10,
        "blacklist_match": False,
    }
    base.update(overrides)
    return base


def advance_to_validating_access(fsm: GovernanceFSM, context: dict):
    fsm.transition(context)  # IDLE -> REQUEST_RECEIVED
    fsm.transition(context)  # REQUEST_RECEIVED -> PARSING_REQUEST
    fsm.transition(context)  # PARSING_REQUEST -> VALIDATING_ACCESS


class TestHardDenialTriggers:

    def test_critical_risk_score_hard_denies(self):
        fsm = GovernanceFSM(request_id="hd-001")
        context = make_context(risk_score=90)

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.HARD_DENIED

    def test_blacklist_match_alone_hard_denies_even_with_zero_risk(self):
        fsm = GovernanceFSM(request_id="hd-002")
        context = make_context(risk_score=0, blacklist_match=True)

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.HARD_DENIED

    def test_maximum_clearance_does_not_override_blacklist(self):
        fsm = GovernanceFSM(request_id="hd-003")
        context = make_context(
            clearance=5,
            required_clearance=1,
            risk_score=5,
            blacklist_match=True,
        )

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.HARD_DENIED

    def test_maximum_clearance_does_not_override_critical_risk(self):
        fsm = GovernanceFSM(request_id="hd-004")
        context = make_context(
            clearance=5,
            required_clearance=1,
            risk_score=100,
        )

        advance_to_validating_access(fsm, context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.HARD_DENIED


class TestHardDenialFullLifecycle:

    def test_hard_denial_reaches_denied_final(self):
        fsm = GovernanceFSM(request_id="hd-005")
        context = make_context(risk_score=95)

        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.DENIED_FINAL

    def test_hard_denial_is_terminal(self):
        fsm = GovernanceFSM(request_id="hd-006")
        context = make_context(risk_score=95)

        fsm.run_until_stuck(context)

        with pytest.raises(TerminalStateError):
            fsm.transition(context)

    def test_hard_denial_history_shows_correct_path(self):
        fsm = GovernanceFSM(request_id="hd-007")
        context = make_context(risk_score=95)

        fsm.run_until_stuck(context)

        path = [record.to_state for record in fsm.history]
        assert RequestState.HARD_DENIED in path
        assert RequestState.ESCALATION_REQUIRED not in path
        assert RequestState.DENIED_FINAL == path[-1]


class TestRulebookIntegrityForHardDenial:
    """
    These tests check properties of the rulebook itself, not a
    specific FSM run — this is the kind of check that catches a
    dangerous rulebook edit before it ever reaches a live request.
    """

    def test_hard_denied_has_exactly_one_outgoing_transition(self):
        outgoing = [t for t in TRANSITIONS if t.from_state == RequestState.HARD_DENIED]

        assert len(outgoing) == 1
        assert outgoing[0].to_state == RequestState.DENIED_FINAL

    def test_hard_denied_outgoing_transition_is_unconditional(self):
        outgoing = [t for t in TRANSITIONS if t.from_state == RequestState.HARD_DENIED][0]

        assert outgoing.condition({}) is True

    def test_no_transition_exists_from_hard_denied_back_to_access_granted(self):
        outgoing = [t for t in TRANSITIONS if t.from_state == RequestState.HARD_DENIED]
        destinations = [t.to_state for t in outgoing]

        assert RequestState.ACCESS_GRANTED not in destinations
        assert RequestState.AUTHORIZED not in destinations
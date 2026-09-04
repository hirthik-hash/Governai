# backend/tests/test_fsm_ambiguity.py

from fsm.governance_fsm import GovernanceFSM
from fsm.states import RequestState
from core.orchestrator import SystemAwareRequestProcessor
from core.decision_logger import DecisionLogger, LogEntryType
from fsm.recovery_fsm import RecoveryFSM


def make_context(**overrides) -> dict:
    base = {
        "ambiguity_flag": True,
        "clearance": 3,
        "required_clearance": 2,
        "risk_score": 10,
    }
    base.update(overrides)
    return base


class TestAmbiguityLoop:

    def test_ambiguous_request_moves_to_clarification_requested(self):
        fsm = GovernanceFSM(request_id="amb-001")
        context = make_context()

        fsm.transition(context)  # IDLE -> REQUEST_RECEIVED
        new_state = fsm.transition(context)  # -> PARSING_REQUEST
        new_state = fsm.transition(context)  # ambiguous -> CLARIFICATION_REQUESTED

        assert new_state == RequestState.CLARIFICATION_REQUESTED

    def test_clarifying_returns_to_parsing_then_proceeds(self):
        fsm = GovernanceFSM(request_id="amb-002")
        context = make_context()

        fsm.transition(context)
        fsm.transition(context)
        fsm.transition(context)  # -> CLARIFICATION_REQUESTED

        context["ambiguity_flag"] = False
        new_state = fsm.transition(context)  # -> PARSING_REQUEST
        assert new_state == RequestState.PARSING_REQUEST

        new_state = fsm.transition(context)  # -> VALIDATING_ACCESS
        assert new_state == RequestState.VALIDATING_ACCESS

    def test_ambiguity_can_loop_more_than_once(self):
        fsm = GovernanceFSM(request_id="amb-003")
        context = make_context()

        fsm.transition(context)  # -> REQUEST_RECEIVED
        fsm.transition(context)  # -> PARSING_REQUEST
        fsm.transition(context)  # -> CLARIFICATION_REQUESTED (round 1)

        context["ambiguity_flag"] = False
        fsm.transition(context)  # -> PARSING_REQUEST

        context["ambiguity_flag"] = True
        new_state = fsm.transition(context)  # ambiguous again -> CLARIFICATION_REQUESTED (round 2)
        assert new_state == RequestState.CLARIFICATION_REQUESTED

        context["ambiguity_flag"] = False
        fsm.transition(context)  # -> PARSING_REQUEST
        final = fsm.transition(context)  # -> VALIDATING_ACCESS

        assert final == RequestState.VALIDATING_ACCESS

    def test_good_context_still_blocked_while_ambiguous(self):
        fsm = GovernanceFSM(request_id="amb-004")
        context = make_context(clearance=5, required_clearance=1, risk_score=0)

        fsm.transition(context)
        fsm.transition(context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.CLARIFICATION_REQUESTED

    def test_full_ambiguity_cycle_is_visible_in_logger(self):
        logger = DecisionLogger()
        recovery = RecoveryFSM()
        processor = SystemAwareRequestProcessor(recovery, logger)

        fsm = GovernanceFSM(request_id="amb-005")
        context = make_context()

        processor.process(fsm, context)  # -> REQUEST_RECEIVED
        processor.process(fsm, context)  # -> PARSING_REQUEST
        processor.process(fsm, context)  # -> CLARIFICATION_REQUESTED

        context["ambiguity_flag"] = False
        processor.process_until_stuck(fsm, context)  # -> ... -> CLOSED

        request_entries = logger.entries_for_request("amb-005")
        visited = [e.to_state for e in request_entries]

        assert "clarification_requested" in visited
        assert visited[-1] == "closed"
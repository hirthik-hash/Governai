# backend/tests/test_rulebook_completeness.py

from fsm.states import RequestState, SystemState, TERMINAL_REQUEST_STATES
from fsm.transitions import TRANSITIONS, SYSTEM_TRANSITIONS


class TestRequestRulebookCompleteness:

    def test_every_non_terminal_request_state_has_an_outgoing_transition(self):
        for state in RequestState:
            if state in TERMINAL_REQUEST_STATES:
                continue
            outgoing = [t for t in TRANSITIONS if t.from_state == state]
            assert len(outgoing) > 0, (
                f"{state.value} is not terminal but has no outgoing "
                f"transitions - a request could get permanently stuck here."
            )

    def test_terminal_states_have_no_outgoing_transitions(self):
        for state in TERMINAL_REQUEST_STATES:
            outgoing = [t for t in TRANSITIONS if t.from_state == state]
            assert len(outgoing) == 0, (
                f"{state.value} is marked terminal but has outgoing "
                f"transitions defined - this contradicts TERMINAL_REQUEST_STATES."
            )

    def test_every_request_state_is_reachable_as_a_destination(self):
        """
        Every state except IDLE (the fixed starting point) should be
        reachable as the 'to_state' of at least one transition -
        otherwise it's dead code sitting in the enum.
        """
        destinations = {t.to_state for t in TRANSITIONS}
        for state in RequestState:
            if state == RequestState.IDLE:
                continue
            assert state in destinations, (
                f"{state.value} is never a destination of any transition - "
                f"it may be unreachable."
            )


class TestSystemRulebookCompleteness:

    def test_every_system_state_has_an_outgoing_transition(self):
        """
        No SystemState is terminal - every one must have a way out,
        since the whole point of RecoveryFSM is that it recovers.
        """
        for state in SystemState:
            outgoing = [t for t in SYSTEM_TRANSITIONS if t.from_state == state]
            assert len(outgoing) > 0, (
                f"{state.value} has no outgoing transitions - the system "
                f"could get permanently stuck here."
            )

    def test_every_system_state_is_reachable_as_a_destination(self):
        destinations = {t.to_state for t in SYSTEM_TRANSITIONS}
        for state in SystemState:
            if state == SystemState.SYSTEM_NORMAL:
                continue  # the fixed starting point
            assert state in destinations, (
                f"{state.value} is never a destination of any transition."
            )
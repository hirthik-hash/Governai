# backend/fsm/governance_fsm.py

from datetime import datetime, timezone
from dataclasses import dataclass, field
from fsm.states import RequestState, TERMINAL_REQUEST_STATES
from fsm.transitions import TRANSITIONS


class InvalidTransitionError(Exception):
    """Raised when no transition rule matches the current state + context."""
    pass


class AmbiguousTransitionError(Exception):
    """Raised when more than one transition rule matches — a rulebook bug."""
    pass


class TerminalStateError(Exception):
    """Raised when attempting to transition a request already in a terminal state."""
    pass


@dataclass
class TransitionRecord:
    from_state: RequestState
    to_state: RequestState
    description: str
    timestamp: str
    context_snapshot: dict = field(default_factory=dict)


class GovernanceFSM:
    """
    Drives a single access request through its lifecycle using the
    rulebook defined in transitions.py. Holds no external
    dependencies — pure state machine logic only.
    """

    def __init__(self, request_id: str, initial_state: RequestState = RequestState.IDLE):
        self.request_id = request_id
        self.state = initial_state
        self.history: list[TransitionRecord] = []

    def _candidate_transitions(self, context: dict) -> list:
        candidates = []
        for t in TRANSITIONS:
            if t.from_state == self.state and t.condition(context):
                candidates.append(t)
        return candidates

    def can_transition(self, context: dict) -> bool:
        return len(self._candidate_transitions(context)) > 0

    def transition(self, context: dict) -> RequestState:
        """
        Attempts to move the FSM forward based on the given context.
        Returns the new state on success. Raises on invalid conditions.
        """
        if self.state in TERMINAL_REQUEST_STATES:
            raise TerminalStateError(
                f"Request {self.request_id} is in terminal state "
                f"{self.state.value}; no further transitions allowed."
            )

        candidates = self._candidate_transitions(context)

        if len(candidates) == 0:
            raise InvalidTransitionError(
                f"No valid transition from {self.state.value} "
                f"for request {self.request_id} with context {context}."
            )

        if len(candidates) > 1:
            descriptions = [c.description for c in candidates]
            raise AmbiguousTransitionError(
                f"Multiple transitions matched from {self.state.value} "
                f"for request {self.request_id}: {descriptions}. "
                f"This indicates overlapping conditions in the rulebook."
            )

        chosen = candidates[0]
        record = TransitionRecord(
            from_state=chosen.from_state,
            to_state=chosen.to_state,
            description=chosen.description,
            timestamp=datetime.now(timezone.utc).isoformat(),
            context_snapshot=dict(context),
        )
        self.history.append(record)
        self.state = chosen.to_state
        return self.state

    def run_until_stuck(self, context: dict) -> RequestState:
        """
        Repeatedly applies transitions using the same context until
        either a terminal state is reached or no further transition
        is possible. Useful for 'always' chained transitions like
        AUTHORIZED -> ACCESS_GRANTED -> AUDIT_LOGGING -> CLOSED that
        don't depend on new information.
        """
        while self.state not in TERMINAL_REQUEST_STATES and self.can_transition(context):
            self.transition(context)
        return self.state

    def print_history(self):
        for record in self.history:
            print(
                f"[{record.timestamp}] "
                f"{record.from_state.value} -> {record.to_state.value} "
                f"| {record.description}"
            )
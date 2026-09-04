# backend/fsm/base_fsm.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generic, TypeVar

from fsm.exceptions import InvalidTransitionError, AmbiguousTransitionError, TerminalStateError

StateT = TypeVar("StateT")


@dataclass
class TransitionRecord(Generic[StateT]):
    from_state: StateT
    to_state: StateT
    description: str
    timestamp: str
    context_snapshot: dict = field(default_factory=dict)


class BaseFSM(Generic[StateT]):
    """
    Generic finite state machine engine, shared by GovernanceFSM and
    RecoveryFSM. Holds no external dependencies - pure state, history,
    and rulebook lookup. Subclasses supply the rulebook, the initial
    state, and (optionally) which states count as terminal.
    """

    def __init__(self, transitions: list, initial_state: StateT, owner_id: str = ""):
        self.state = initial_state
        self.history: list[TransitionRecord] = []
        self._transitions = transitions
        self.owner_id = owner_id  # e.g. request_id; blank for system-level FSMs

    def _candidate_transitions(self, context: dict) -> list:
        return [
            t for t in self._transitions
            if t.from_state == self.state and t.condition(context)
        ]

    def can_transition(self, context: dict) -> bool:
        return len(self._candidate_transitions(context)) > 0

    def _is_terminal(self, state: StateT) -> bool:
        """Override in subclasses with terminal states. Default: nothing is terminal."""
        return False

    def transition(self, context: dict) -> StateT:
        if self._is_terminal(self.state):
            raise TerminalStateError(
                f"{self.owner_id or 'FSM'} is in terminal state "
                f"{self.state.value}; no further transitions allowed."
            )

        candidates = self._candidate_transitions(context)

        if len(candidates) == 0:
            raise InvalidTransitionError(
                f"No valid transition from {self.state.value} "
                f"for {self.owner_id or 'FSM'} with context {context}."
            )

        if len(candidates) > 1:
            descriptions = [c.description for c in candidates]
            raise AmbiguousTransitionError(
                f"Multiple transitions matched from {self.state.value} "
                f"for {self.owner_id or 'FSM'}: {descriptions}. "
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

    def run_until_stuck(self, context: dict) -> StateT:
        while not self._is_terminal(self.state) and self.can_transition(context):
            self.transition(context)
        return self.state

    def print_history(self) -> None:
        for record in self.history:
            print(
                f"[{record.timestamp}] "
                f"{record.from_state.value} -> {record.to_state.value} "
                f"| {record.description}"
            )
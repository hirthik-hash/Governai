# backend/fsm/recovery_fsm.py

from datetime import datetime, timezone
from dataclasses import dataclass, field
from fsm.states import SystemState
from fsm.transitions import SYSTEM_TRANSITIONS
from fsm.governance_fsm import (
    InvalidTransitionError,
    AmbiguousTransitionError,
)


@dataclass
class SystemTransitionRecord:
    from_state: SystemState
    to_state: SystemState
    description: str
    timestamp: str
    context_snapshot: dict = field(default_factory=dict)


class RecoveryFSM:
    """
    Tracks overall system health, independent of any single request.
    Deliberately simple: no escalation branching, just a linear
    health-driven cycle between normal operation and safe mode.
    """

    def __init__(self, initial_state: SystemState = SystemState.SYSTEM_NORMAL):
        self.state = initial_state
        self.history: list[SystemTransitionRecord] = []

    def _candidate_transitions(self, context: dict) -> list:
        return [
            t for t in SYSTEM_TRANSITIONS
            if t.from_state == self.state and t.condition(context)
        ]

    def can_transition(self, context: dict) -> bool:
        return len(self._candidate_transitions(context)) > 0

    def transition(self, context: dict) -> SystemState:
        candidates = self._candidate_transitions(context)

        if len(candidates) == 0:
            raise InvalidTransitionError(
                f"No valid system transition from {self.state.value} "
                f"with context {context}."
            )

        if len(candidates) > 1:
            descriptions = [c.description for c in candidates]
            raise AmbiguousTransitionError(
                f"Multiple system transitions matched from {self.state.value}: "
                f"{descriptions}. This indicates overlapping conditions."
            )

        chosen = candidates[0]
        record = SystemTransitionRecord(
            from_state=chosen.from_state,
            to_state=chosen.to_state,
            description=chosen.description,
            timestamp=datetime.now(timezone.utc).isoformat(),
            context_snapshot=dict(context),
        )
        self.history.append(record)
        self.state = chosen.to_state
        return self.state

    def is_safe_mode(self) -> bool:
        return self.state == SystemState.SAFE_MODE_ACTIVE

    def print_history(self):
        for record in self.history:
            print(
                f"[{record.timestamp}] "
                f"{record.from_state.value} -> {record.to_state.value} "
                f"| {record.description}"
            )
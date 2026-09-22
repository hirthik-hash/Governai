# backend/core/timeout_tracker.py

from datetime import datetime, timezone
from typing import Callable


class UnknownEscalationError(Exception):
    """Raised when checking timeout status for a request that was never recorded as escalated."""
    pass


class EscalationTimeoutTracker:
    """
    Tracks when each escalated request was sent for approval, and
    determines whether it has exceeded its timeout window. Pure
    bookkeeping - does not itself deny or approve anything; the
    Escalation Agent (Phase 2) will consult this and set the FSM
    context's 'timed_out' flag accordingly.
    """

    def __init__(
        self,
        default_timeout_seconds: int = 1800,
        now_fn: Callable[[], datetime] = None,
    ):
        self.default_timeout_seconds = default_timeout_seconds
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._sent_at: dict[str, datetime] = {}
        self._timeout_overrides: dict[str, int] = {}

    def record_escalation_sent(self, request_id: str, timeout_seconds: int = None) -> None:
        self._sent_at[request_id] = self._now_fn()
        if timeout_seconds is not None:
            self._timeout_overrides[request_id] = timeout_seconds

    def is_timed_out(self, request_id: str) -> bool:
        if request_id not in self._sent_at:
            raise UnknownEscalationError(
                f"No escalation recorded for request {request_id}. "
                f"Call record_escalation_sent() first."
            )

        sent_at = self._sent_at[request_id]
        timeout_seconds = self._timeout_overrides.get(request_id, self.default_timeout_seconds)
        elapsed = (self._now_fn() - sent_at).total_seconds()

        return elapsed >= timeout_seconds

    def restore(self, request_id: str, sent_at: datetime, timeout_seconds: int = None) -> None:
        """
        Re-registers an escalation with a KNOWN prior sent_at (Day 79),
        e.g. when resuming a pending escalation from persistence after a
        restart. Unlike record_escalation_sent() (which always stamps
        "now"), this preserves the original clock so the timeout window
        is computed from when the escalation was actually sent, not from
        when the process happened to restart.
        """
        self._sent_at[request_id] = sent_at
        if timeout_seconds is not None:
            self._timeout_overrides[request_id] = timeout_seconds

    def sent_at(self, request_id: str) -> datetime:
        if request_id not in self._sent_at:
            raise UnknownEscalationError(f"No escalation recorded for request {request_id}.")
        return self._sent_at[request_id]

    def timeout_seconds_for(self, request_id: str) -> int:
        return self._timeout_overrides.get(request_id, self.default_timeout_seconds)

    def seconds_remaining(self, request_id: str) -> float:
        if request_id not in self._sent_at:
            raise UnknownEscalationError(
                f"No escalation recorded for request {request_id}."
            )

        sent_at = self._sent_at[request_id]
        timeout_seconds = self._timeout_overrides.get(request_id, self.default_timeout_seconds)
        elapsed = (self._now_fn() - sent_at).total_seconds()

        return max(0.0, timeout_seconds - elapsed)
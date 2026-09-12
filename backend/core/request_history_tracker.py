# backend/core/request_history_tracker.py

"""
Tracks per-user request history (failures and timestamps) so the
Security & Risk Agent can detect repeated-failure and rapid-succession
patterns, which cannot be computed from a single request's data alone.

Same clock-injection pattern as core.timeout_tracker.EscalationTimeoutTracker
(Day 14) - a now_fn is injected so tests can control time precisely
instead of sleeping for real.

Consumed by agents.security_agent.SecurityRiskAgent as an injected
dependency (repeated_failures and rapid_succession factors).
"""

from datetime import datetime, timezone, timedelta
from typing import Callable
from dataclasses import dataclass, field


@dataclass
class RequestRecord:
    timestamp: datetime
    was_denied: bool


class RequestHistoryTracker:
    """
    In-memory, per-user request history. Phase 3 will back this with
    real persistence (database/Redis) - this in-memory version is
    sufficient for building and testing the risk factors themselves.
    """

    def __init__(
        self,
        failure_window_seconds: int = 600,       # 10 minutes
        failure_threshold: int = 3,
        rapid_window_seconds: int = 30,
        rapid_threshold: int = 5,
        now_fn: Callable[[], datetime] = None,
    ):
        self.failure_window_seconds = failure_window_seconds
        self.failure_threshold = failure_threshold
        self.rapid_window_seconds = rapid_window_seconds
        self.rapid_threshold = rapid_threshold
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._history: dict[str, list[RequestRecord]] = {}

    def record_request(self, user_id: str, was_denied: bool = False) -> None:
        record = RequestRecord(timestamp=self._now_fn(), was_denied=was_denied)
        self._history.setdefault(user_id, []).append(record)

    def has_repeated_failures(self, user_id: str) -> bool:
        records = self._history.get(user_id, [])
        now = self._now_fn()
        cutoff = now - timedelta(seconds=self.failure_window_seconds)

        recent_failures = [
            r for r in records if r.was_denied and r.timestamp >= cutoff
        ]
        return len(recent_failures) >= self.failure_threshold

    def has_rapid_succession(self, user_id: str) -> bool:
        records = self._history.get(user_id, [])
        now = self._now_fn()
        cutoff = now - timedelta(seconds=self.rapid_window_seconds)

        recent_requests = [r for r in records if r.timestamp >= cutoff]
        return len(recent_requests) >= self.rapid_threshold

    def clear(self, user_id: str) -> None:
        self._history.pop(user_id, None)
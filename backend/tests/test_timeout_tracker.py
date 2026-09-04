# backend/tests/test_timeout_tracker.py

import pytest
from datetime import datetime, timezone, timedelta
from core.timeout_tracker import EscalationTimeoutTracker, UnknownEscalationError


class FakeClock:
    """Lets tests control 'now' precisely instead of sleeping for real."""

    def __init__(self, start: datetime):
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class TestEscalationTimeoutTracker:

    def test_not_timed_out_immediately_after_sending(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = EscalationTimeoutTracker(default_timeout_seconds=1800, now_fn=clock.now)

        tracker.record_escalation_sent("req-001")

        assert tracker.is_timed_out("req-001") is False

    def test_timed_out_after_default_window_elapses(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = EscalationTimeoutTracker(default_timeout_seconds=1800, now_fn=clock.now)

        tracker.record_escalation_sent("req-002")
        clock.advance(1801)  # 30 minutes + 1 second

        assert tracker.is_timed_out("req-002") is True

    def test_not_timed_out_one_second_before_window(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = EscalationTimeoutTracker(default_timeout_seconds=1800, now_fn=clock.now)

        tracker.record_escalation_sent("req-003")
        clock.advance(1799)

        assert tracker.is_timed_out("req-003") is False

    def test_per_request_override_timeout(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = EscalationTimeoutTracker(default_timeout_seconds=1800, now_fn=clock.now)

        tracker.record_escalation_sent("req-004", timeout_seconds=60)
        clock.advance(61)

        assert tracker.is_timed_out("req-004") is True

    def test_unknown_request_id_raises(self):
        tracker = EscalationTimeoutTracker()

        with pytest.raises(UnknownEscalationError):
            tracker.is_timed_out("never-recorded")

    def test_seconds_remaining_counts_down(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = EscalationTimeoutTracker(default_timeout_seconds=1800, now_fn=clock.now)

        tracker.record_escalation_sent("req-005")
        clock.advance(600)  # 10 minutes in

        remaining = tracker.seconds_remaining("req-005")
        assert remaining == pytest.approx(1200, abs=1)
        
# backend/tests/test_request_history_tracker.py

from datetime import datetime, timezone, timedelta
from core.request_history_tracker import RequestHistoryTracker


class FakeClock:
    def __init__(self, start: datetime):
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class TestRepeatedFailures:

    def test_no_history_means_no_repeated_failures(self):
        tracker = RequestHistoryTracker()
        assert tracker.has_repeated_failures("user-001") is False

    def test_below_threshold_failures_not_flagged(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(failure_threshold=3, now_fn=clock.now)

        tracker.record_request("user-001", was_denied=True)
        tracker.record_request("user-001", was_denied=True)

        assert tracker.has_repeated_failures("user-001") is False

    def test_at_threshold_failures_flagged(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(failure_threshold=3, now_fn=clock.now)

        tracker.record_request("user-001", was_denied=True)
        tracker.record_request("user-001", was_denied=True)
        tracker.record_request("user-001", was_denied=True)

        assert tracker.has_repeated_failures("user-001") is True

    def test_successful_requests_do_not_count_as_failures(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(failure_threshold=3, now_fn=clock.now)

        tracker.record_request("user-001", was_denied=False)
        tracker.record_request("user-001", was_denied=False)
        tracker.record_request("user-001", was_denied=False)

        assert tracker.has_repeated_failures("user-001") is False

    def test_old_failures_outside_window_do_not_count(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(
            failure_window_seconds=600, failure_threshold=3, now_fn=clock.now
        )

        tracker.record_request("user-001", was_denied=True)
        tracker.record_request("user-001", was_denied=True)
        clock.advance(601)  # past the 10-minute window
        tracker.record_request("user-001", was_denied=True)

        assert tracker.has_repeated_failures("user-001") is False

    def test_different_users_tracked_independently(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(failure_threshold=3, now_fn=clock.now)

        tracker.record_request("user-001", was_denied=True)
        tracker.record_request("user-001", was_denied=True)
        tracker.record_request("user-001", was_denied=True)

        assert tracker.has_repeated_failures("user-002") is False


class TestRapidSuccession:

    def test_below_threshold_requests_not_flagged(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(rapid_threshold=5, now_fn=clock.now)

        for _ in range(3):
            tracker.record_request("user-001")

        assert tracker.has_rapid_succession("user-001") is False

    def test_at_threshold_requests_flagged(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(rapid_threshold=5, now_fn=clock.now)

        for _ in range(5):
            tracker.record_request("user-001")

        assert tracker.has_rapid_succession("user-001") is True

    def test_requests_spread_outside_window_not_flagged(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(
            rapid_window_seconds=30, rapid_threshold=5, now_fn=clock.now
        )

        for _ in range(5):
            tracker.record_request("user-001")
            clock.advance(10)  # 5 requests over 40+ seconds, window is 30

        assert tracker.has_rapid_succession("user-001") is False

    def test_clear_resets_a_users_history(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = RequestHistoryTracker(rapid_threshold=5, now_fn=clock.now)

        for _ in range(5):
            tracker.record_request("user-001")
        assert tracker.has_rapid_succession("user-001") is True

        tracker.clear("user-001")
        assert tracker.has_rapid_succession("user-001") is False
# backend/tests/test_escalation_agent.py

from agents.escalation_agent import EscalationAgent
from datetime import datetime, timezone, timedelta
from core.timeout_tracker import EscalationTimeoutTracker
from agents.escalation_agent import NotificationRecord


class TestEscalationAgentBasicRouting:

    def test_employee_routes_to_team_lead_when_sufficient(self):
        # user-001 (Alex Chen, clearance 1) reports to user-002 (Priya Nair, Team Lead, clearance 2)
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001", "required_clearance": 2})

        assert result.success is True
        assert result.data["approver_user_id"] == "user-002"

    def test_skips_insufficiently_cleared_approver_in_chain(self):
        # user-008 (Rina Sato, clearance 0) reports to user-002 (Priya Nair, clearance 2)
        # who reports to user-007 (CISO, clearance 5). Requesting
        # required_clearance=5 should skip Priya (only clearance 2) and land on CISO.
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-008", "required_clearance": 5})

        assert result.data["approver_user_id"] == "user-007"

    def test_direct_manager_routed_to_when_exactly_sufficient(self):
        # user-003 (Marcus Webb, clearance 1) reports to user-004 (Sofia Ricci, clearance 3)
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-003", "required_clearance": 3})

        assert result.data["approver_user_id"] == "user-004"


class TestEscalationAgentReachesTopOfChain:

    def test_reaching_ciso_flags_top_of_chain(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-008", "required_clearance": 5})

        assert result.data["escalation_reached_top_of_chain"] is True

    def test_non_top_approver_does_not_flag_top_of_chain(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001", "required_clearance": 2})

        assert result.data["escalation_reached_top_of_chain"] is False

    def test_ciso_is_final_approver_even_for_impossible_clearance(self):
        """
        If required_clearance exceeds even the CISO's own clearance
        (5), the CISO is still returned as the final approver - there
        is nowhere higher to escalate to, so they're the ultimate
        decision-maker by default.
        """
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-008", "required_clearance": 99})

        assert result.data["approver_user_id"] == "user-007"
        assert result.data["escalation_reached_top_of_chain"] is True


class TestEscalationAgentInvalidInput:

    def test_missing_user_id_fails_gracefully(self):
        agent = EscalationAgent()
        result = agent.process({"required_clearance": 3})

        assert result.success is False

    def test_missing_required_clearance_fails_gracefully(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001"})

        assert result.success is False

    def test_unknown_user_id_fails_gracefully_not_raises(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-999", "required_clearance": 3})

        assert result.success is False


class TestEscalationAgentIntegratesWithPriorAgents:

    def test_chains_from_request_and_validation_agents(self):
        from agents.request_agent import RequestUnderstandingAgent

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-008",  # Rina Sato, clearance 0
            "resource_id": "resource-005",  # Salary Records, requires 5
        })

        escalation_agent = EscalationAgent()
        combined = dict(request_result.data)
        combined["user_id"] = "user-008"
        escalation_result = escalation_agent.process(combined)

        assert escalation_result.success is True
        assert escalation_result.data["approver_user_id"] == "user-007"


class FakeClock:
    def __init__(self, start: datetime):
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class TestEscalationAgentNotificationRecord:

    def test_successful_escalation_includes_notification(self):
        agent = EscalationAgent()
        result = agent.process({
            "user_id": "user-001", "required_clearance": 2,
            "request_id": "req-abc", "resolved_resource_id": "resource-002",
        })

        assert "notification" in result.data
        notification = result.data["notification"]
        assert isinstance(notification, NotificationRecord)
        assert notification.request_id == "req-abc"
        assert notification.approver_user_id == "user-002"
        assert notification.requester_user_id == "user-001"
        assert notification.resource_summary == "resource-002"

    def test_notification_includes_timeout_seconds(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001", "required_clearance": 2})

        assert result.data["notification"].timeout_seconds == 1800  # default

    def test_missing_request_id_defaults_gracefully(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001", "required_clearance": 2})

        assert result.data["notification"].request_id == "unknown-request"

    def test_escalation_sent_flag_is_true_on_success(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001", "required_clearance": 2})

        assert result.data["escalation_sent"] is True


class TestEscalationAgentTimeoutTrackerIntegration:

    def test_uses_injected_tracker_with_custom_timeout(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = EscalationTimeoutTracker(default_timeout_seconds=60, now_fn=clock.now)

        agent = EscalationAgent(timeout_tracker=tracker)
        result = agent.process({
            "user_id": "user-001", "required_clearance": 2, "request_id": "req-fast",
        })

        assert result.data["notification"].timeout_seconds == 60

    def test_escalation_actually_starts_the_timeout_clock(self):
        """
        Confirms process() genuinely calls record_escalation_sent()
        on the tracker, not just constructs a notification with a
        timeout number - the tracker itself should now know about
        this request and correctly report timeout status over time.
        """
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = EscalationTimeoutTracker(default_timeout_seconds=1800, now_fn=clock.now)

        agent = EscalationAgent(timeout_tracker=tracker)
        agent.process({
            "user_id": "user-001", "required_clearance": 2, "request_id": "req-clock-test",
        })

        assert tracker.is_timed_out("req-clock-test") is False

        clock.advance(1801)
        assert tracker.is_timed_out("req-clock-test") is True

    def test_two_different_requests_tracked_independently(self):
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        tracker = EscalationTimeoutTracker(default_timeout_seconds=1800, now_fn=clock.now)
        agent = EscalationAgent(timeout_tracker=tracker)

        agent.process({"user_id": "user-001", "required_clearance": 2, "request_id": "req-A"})
        clock.advance(900)
        agent.process({"user_id": "user-003", "required_clearance": 3, "request_id": "req-B"})
        clock.advance(901)  # req-A now at 1801s, req-B at 901s

        assert tracker.is_timed_out("req-A") is True
        assert tracker.is_timed_out("req-B") is False


class TestEscalationAgentFullChainWithNotification:

    def test_full_chain_produces_notification_for_realistic_scenario(self):
        from agents.request_agent import RequestUnderstandingAgent

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-008", "resource_id": "resource-005",
        })

        escalation_agent = EscalationAgent()
        combined = dict(request_result.data)
        combined["user_id"] = "user-008"
        combined["request_id"] = "req-full-chain-001"
        escalation_result = escalation_agent.process(combined)

        assert escalation_result.success is True
        notification = escalation_result.data["notification"]
        assert notification.approver_user_id == "user-007"  # CISO
        assert notification.requester_user_id == "user-008"
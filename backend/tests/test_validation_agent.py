# backend/tests/test_validation_agent.py

from datetime import datetime

from agents.validation_agent import (
    AccessValidationAgent,
    SessionValidator,
)


class TestAccessValidationAgentAfterHours:

    def test_business_hours_weekday_not_flagged(self):
        # Wednesday 2:00 PM
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert result.data["after_hours_access"] is False

    def test_late_night_weekday_flagged_for_top_secret(self):
        # Wednesday 11:00 PM
        fixed_now = lambda: datetime(2026, 9, 9, 23, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert result.data["after_hours_access"] is True

    def test_weekend_flagged_for_restricted(self):
        # Saturday 10:00 AM - within "business hours" clock time but wrong day
        fixed_now = lambda: datetime(2026, 9, 12, 10, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "resource_sensitivity": "restricted",
            "session_token": "abc123",
        })

        assert result.data["after_hours_access"] is True

    def test_after_hours_not_flagged_for_public_resource(self):
        fixed_now = lambda: datetime(2026, 9, 9, 23, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5,
            "required_clearance": 0,
            "resource_sensitivity": "public",
            "session_token": "abc123",
        })

        assert result.data["after_hours_access"] is False

    def test_after_hours_not_flagged_for_internal_resource(self):
        fixed_now = lambda: datetime(2026, 9, 9, 23, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5,
            "required_clearance": 1,
            "resource_sensitivity": "internal",
            "session_token": "abc123",
        })

        assert result.data["after_hours_access"] is False

    def test_exact_boundary_start_of_business_hours_counts_as_business_hours(self):
        # Exactly 8:00 AM Wednesday
        fixed_now = lambda: datetime(2026, 9, 9, 8, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert result.data["after_hours_access"] is False

    def test_reasoning_mentions_after_hours_when_flagged(self):
        fixed_now = lambda: datetime(2026, 9, 9, 23, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert "business hours" in result.reasoning.lower()

    def test_default_now_fn_used_when_not_injected(self):
        """
        Confirms the agent works without explicit clock injection too -
        just uses real current time, same pattern as
        EscalationTimeoutTracker's default.
        """
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "resource_sensitivity": "public",
            "session_token": "abc123",
        })

        assert result.success is True
        assert "after_hours_access" in result.data


class TestAccessValidationAgentBasicClearance:

    def test_sufficient_clearance_passes(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 3,
            "required_clearance": 2,
            "session_token": "abc123",
        })

        assert result.success is True
        assert result.data["clearance_sufficient"] is True
        assert result.data["role_override_applied"] is False

    def test_insufficient_clearance_without_role_fails_check(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 1,
            "required_clearance": 3,
            "session_token": "abc123",
        })

        assert result.success is True  # the agent itself succeeded at reasoning
        assert result.data["clearance_sufficient"] is False

    def test_exact_clearance_match_passes(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 2,
            "required_clearance": 2,
            "session_token": "abc123",
        })

        assert result.data["clearance_sufficient"] is True

    def test_missing_clearance_fields_fails_gracefully(self):
        agent = AccessValidationAgent()
        result = agent.process({"role": "CISO"})

        assert result.success is False
        assert len(result.errors) > 0


class TestAccessValidationAgentRoleOverride:

    def test_ciso_override_applies_for_security_resource(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 1,
            "required_clearance": 5,
            "role": "CISO",
            "resource_sensitivity": "security",
            "session_token": "abc123",
        })

        assert result.data["clearance_sufficient"] is True
        assert result.data["role_override_applied"] is True

    def test_ciso_override_applies_for_top_secret_resource(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 0,
            "required_clearance": 5,
            "role": "CISO",
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert result.data["role_override_applied"] is True

    def test_ciso_override_does_not_apply_for_unrelated_sensitivity(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 1,
            "required_clearance": 5,
            "role": "CISO",
            "resource_sensitivity": "internal",
            "session_token": "abc123",
        })

        assert result.data["clearance_sufficient"] is False
        assert result.data["role_override_applied"] is False

    def test_override_never_applies_when_role_not_in_table(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 1,
            "required_clearance": 5,
            "role": "Software Engineer",
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert result.data["role_override_applied"] is False

    def test_override_never_needed_when_clearance_already_sufficient(self):
        """
        A CISO with genuinely sufficient clearance shouldn't show
        role_override_applied=True - the override only exists to
        explain shortfalls, not to relabel a normal pass.
        """
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "role": "CISO",
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert result.data["clearance_sufficient"] is True
        assert result.data["role_override_applied"] is False


class TestAccessValidationAgentIntegratesWithRequestAgent:

    def test_chains_cleanly_after_request_understanding_agent(self):
        from agents.request_agent import RequestUnderstandingAgent

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-008",  # Rina Sato, clearance 0
            "resource_id": "resource-005",  # Salary Records, requires 5
        })

        validation_agent = AccessValidationAgent()
        validation_result = validation_agent.process({
            **request_result.data,
            "session_token": "abc123",
        })

        assert validation_result.success is True
        assert validation_result.data["clearance_sufficient"] is False

    def test_combined_output_still_feeds_fsm_correctly(self):
        from agents.request_agent import RequestUnderstandingAgent
        from fsm.governance_fsm import GovernanceFSM
        from fsm.states import RequestState

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-007",  # CISO, clearance 5
            "resource_id": "resource-001",  # public, requires 0
        })

        validation_agent = AccessValidationAgent()
        validation_result = validation_agent.process({
            **request_result.data,
            "session_token": "abc123",
        })

        context = request_result.data.copy()
        context.update(validation_result.data)
        context["risk_score"] = 10

        fsm = GovernanceFSM(request_id="validation-integration-001")
        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED


class TestSessionValidatorDefault:

    def test_missing_token_is_invalid(self):
        validator = SessionValidator()
        is_valid, reason = validator.is_valid({})

        assert is_valid is False
        assert "no session token" in reason.lower()

    def test_present_token_not_expired_is_valid(self):
        validator = SessionValidator()
        is_valid, reason = validator.is_valid({
            "session_token": "abc123",
        })

        assert is_valid is True

    def test_expired_token_is_invalid(self):
        validator = SessionValidator()
        is_valid, reason = validator.is_valid({
            "session_token": "abc123",
            "session_expired": True,
        })

        assert is_valid is False
        assert "expired" in reason.lower()


class TestAccessValidationAgentSessionChecks:

    def test_missing_session_token_fails_the_agent(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
        })

        assert result.success is False
        assert "session" in result.reasoning.lower()

    def test_expired_session_fails_even_with_sufficient_clearance(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "session_token": "abc123",
            "session_expired": True,
        })

        assert result.success is False
        assert "expired" in result.errors[0].lower()

    def test_valid_session_with_sufficient_clearance_succeeds(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "session_token": "abc123",
        })

        assert result.success is True
        assert result.data["session_valid"] is True

    def test_custom_session_validator_can_be_injected(self):
        """
        Confirms the validator is genuinely swappable - Phase 3 will
        inject a real JWT-checking validator this same way.
        """

        class AlwaysValidValidator(SessionValidator):
            def is_valid(self, input_data):
                return True, "Always valid (test double)"

        agent = AccessValidationAgent(
            session_validator=AlwaysValidValidator()
        )

        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            # deliberately no session_token - the injected validator
            # should be consulted instead of the default logic
        })

        assert result.success is True

    def test_session_failure_is_distinguishable_from_clearance_failure(self):
        """
        A session failure and a clearance failure should not look the
        same in the output - the Explainability Center will need to
        tell these apart.
        """
        agent = AccessValidationAgent()

        session_failure = agent.process({
            "clearance": 5,
            "required_clearance": 2,
        })

        assert "session" in session_failure.reasoning.lower()
        assert "clearance" not in session_failure.errors[0].lower()
from datetime import datetime as dt


class TestAccessValidationAgentCheckOrdering:

    def test_session_failure_takes_precedence_over_clearance_failure(self):
        """
        Both problems exist at once: no session token AND insufficient
        clearance. Documents that session is checked first, so that's
        the failure reason reported - this is a deliberate ordering
        choice (can't reason about permissions for an unauthenticated
        request), not an accident, and this test locks it in.
        """
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 1, "required_clearance": 5,
            # no session_token provided
        })

        assert result.success is False
        assert "session" in result.reasoning.lower()

    def test_expired_session_with_no_token_reports_missing_token_first(self):
        """
        session_expired=True is meaningless without a token in the
        first place - SessionValidator checks token presence before
        expiry, so 'no token' should win over 'expired'.
        """
        validator = SessionValidator()
        is_valid, reason = validator.is_valid({"session_expired": True})

        assert is_valid is False
        assert "no session token" in reason.lower()


class TestAccessValidationAgentBusinessHoursEndBoundary:

    def test_exact_boundary_end_of_business_hours_counts_as_business_hours(self):
        # Exactly 6:00 PM Wednesday
        fixed_now = lambda: dt(2026, 9, 9, 18, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5, "required_clearance": 2,
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert result.data["after_hours_access"] is False

    def test_one_minute_after_business_hours_end_is_after_hours(self):
        # 6:01 PM Wednesday
        fixed_now = lambda: dt(2026, 9, 9, 18, 1)
        agent = AccessValidationAgent(now_fn=fixed_now)
        result = agent.process({
            "clearance": 5, "required_clearance": 2,
            "resource_sensitivity": "top_secret",
            "session_token": "abc123",
        })

        assert result.data["after_hours_access"] is True


class TestAccessValidationAgentAllFeaturesCombined:

    def test_ciso_override_after_hours_valid_session_all_together(self):
        """
        Combines all three pieces this agent has built across Days
        31-33: a CISO whose raw clearance is insufficient (needs the
        role override), accessing after business hours (should be
        flagged), with a valid session (should pass the gate).
        """
        # Sunday 10:00 PM - after hours by both day and time
        fixed_now = lambda: dt(2026, 9, 13, 22, 0)
        agent = AccessValidationAgent(now_fn=fixed_now)

        result = agent.process({
            "clearance": 1,
            "required_clearance": 5,
            "role": "CISO",
            "resource_sensitivity": "top_secret",
            "session_token": "valid-token-abc",
        })

        assert result.success is True
        assert result.data["clearance_sufficient"] is True
        assert result.data["role_override_applied"] is True
        assert result.data["after_hours_access"] is True
        assert result.data["session_valid"] is True

    def test_full_chain_from_request_agent_through_validation_agent_after_hours(self):
        """
        End-to-end: RequestUnderstandingAgent's real output feeding
        into AccessValidationAgent, at a fixed after-hours timestamp,
        confirming the two agents compose correctly under this
        specific combined scenario.
        """
        from agents.request_agent import RequestUnderstandingAgent

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-007",  # James Whitfield, CISO, clearance 5
            "resource_id": "resource-005",  # Salary Records, top_secret, requires 5
        })

        fixed_now = lambda: dt(2026, 9, 9, 23, 0)  # Wednesday 11 PM
        validation_agent = AccessValidationAgent(now_fn=fixed_now)

        combined_input = dict(request_result.data)
        combined_input["session_token"] = "abc123"

        validation_result = validation_agent.process(combined_input)

        assert validation_result.success is True
        assert validation_result.data["clearance_sufficient"] is True
        assert validation_result.data["after_hours_access"] is True
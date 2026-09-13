# backend/tests/test_audit_agent.py

from agents.audit_agent import AuditComplianceAgent, AuditRecord
from agents.base_agent import AgentResult


def make_fake_agent_result(reasoning: str) -> AgentResult:
    return AgentResult(success=True, data={}, reasoning=reasoning)


class TestAuditComplianceAgentBasicCompilation:

    def test_successful_compilation_returns_audit_record(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-001", "user_id": "user-001",
            "resolved_resource_id": "resource-001",
            "final_fsm_state": "closed", "risk_score": 10, "risk_level": "LOW",
        })

        assert result.success is True
        assert isinstance(result.data["audit_record"], AuditRecord)

    def test_granted_maps_from_closed_state(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-002", "user_id": "user-001",
            "final_fsm_state": "closed",
        })

        assert result.data["audit_record"].final_decision == "GRANTED"

    def test_denied_maps_from_denied_final_state(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-003", "user_id": "user-009",
            "final_fsm_state": "denied_final",
        })

        assert result.data["audit_record"].final_decision == "DENIED"

    def test_unmapped_state_defaults_to_pending(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-004", "user_id": "user-001",
            "final_fsm_state": "manager_review",
        })

        assert result.data["audit_record"].final_decision == "PENDING"

    def test_missing_required_fields_fails_gracefully(self):
        agent = AuditComplianceAgent()
        result = agent.process({"user_id": "user-001"})

        assert result.success is False


class TestAuditComplianceAgentReasoningTrail:

    def test_agent_reasoning_strings_are_consolidated_in_order(self):
        agent = AuditComplianceAgent()
        fake_results = [
            make_fake_agent_result("Request Understanding said X"),
            make_fake_agent_result("Access Validation said Y"),
            make_fake_agent_result("Security Risk said Z"),
        ]

        result = agent.process({
            "request_id": "req-005", "user_id": "user-001",
            "final_fsm_state": "closed", "agent_results": fake_results,
        })

        trail = result.data["audit_record"].agent_reasoning_trail
        assert trail == [
            "Request Understanding said X",
            "Access Validation said Y",
            "Security Risk said Z",
        ]

    def test_no_agent_results_gives_empty_trail_not_a_crash(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-006", "user_id": "user-001",
            "final_fsm_state": "closed",
        })

        assert result.data["audit_record"].agent_reasoning_trail == []

    def test_policy_rule_cited_defaults_to_none(self):
        """
        Locks in the Day 49 design decision: policy_rule_cited exists
        as a field now (so the record's shape won't need to change
        later) but is always None until Phase 4's Policy Intelligence
        Agent exists.
        """
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-007", "user_id": "user-001",
            "final_fsm_state": "closed",
        })

        assert result.data["audit_record"].policy_rule_cited is None


class TestAuditComplianceAgentApproverField:

    def test_approver_recorded_when_escalated(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-008", "user_id": "user-003",
            "final_fsm_state": "closed", "approver_user_id": "user-004",
        })

        assert result.data["audit_record"].approver_user_id == "user-004"

    def test_approver_empty_when_never_escalated(self):
        agent = AuditComplianceAgent()
        result = agent.process({
            "request_id": "req-009", "user_id": "user-007",
            "final_fsm_state": "closed",
        })

        assert result.data["audit_record"].approver_user_id == ""


class TestAuditComplianceAgentFullChainIntegration:

    def test_compiles_record_from_a_real_full_chain_run(self):
        from agents.request_agent import RequestUnderstandingAgent
        from agents.validation_agent import AccessValidationAgent
        from agents.security_agent import SecurityRiskAgent
        from fsm.governance_fsm import GovernanceFSM
        from datetime import datetime

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-007", "resource_id": "resource-001",
        })

        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        validation_agent = AccessValidationAgent(now_fn=fixed_now)
        combined = dict(request_result.data)
        combined["session_token"] = "abc"
        validation_result = validation_agent.process(combined)
        combined.update(validation_result.data)

        security_agent = SecurityRiskAgent()
        combined["user_id"] = "user-007"
        security_result = security_agent.process(combined)
        combined.update(security_result.data)

        fsm = GovernanceFSM(request_id="req-full-audit-001")
        final_state = fsm.run_until_stuck(combined)

        audit_agent = AuditComplianceAgent()
        audit_input = dict(combined)
        audit_input["request_id"] = "req-full-audit-001"
        audit_input["final_fsm_state"] = final_state.value
        audit_input["agent_results"] = [request_result, validation_result, security_result]

        audit_result = audit_agent.process(audit_input)

        assert audit_result.success is True
        record = audit_result.data["audit_record"]
        assert record.final_decision == "GRANTED"
        assert len(record.agent_reasoning_trail) == 3
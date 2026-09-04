# backend/tests/test_decision_logger.py

from core.decision_logger import DecisionLogger, LogEntryType
from core.orchestrator import SystemAwareRequestProcessor
from fsm.governance_fsm import GovernanceFSM
from fsm.recovery_fsm import RecoveryFSM


class TestDecisionLoggerFiltering:

    def test_entries_by_type_returns_only_matching_entries(self):
        logger = DecisionLogger()
        recovery = RecoveryFSM()
        processor = SystemAwareRequestProcessor(recovery, logger)

        req = GovernanceFSM(request_id="cov-001")
        context = {"ambiguity_flag": False, "clearance": 3, "required_clearance": 2, "risk_score": 10}
        processor.process_until_stuck(req, context)

        request_entries = logger.entries_by_type(LogEntryType.REQUEST_TRANSITION)

        assert len(request_entries) > 0
        assert all(e.entry_type == LogEntryType.REQUEST_TRANSITION for e in request_entries)

    def test_system_transitions_are_now_logged(self):
        """
        Closes the gap: RecoveryFSM transitions must appear in the
        logger when driven through transition_system(), not just
        request transitions and safe-mode blocks.
        """
        logger = DecisionLogger()
        recovery = RecoveryFSM()
        processor = SystemAwareRequestProcessor(recovery, logger)

        processor.transition_system({"system_healthy": False})

        system_entries = logger.entries_by_type(LogEntryType.SYSTEM_TRANSITION)

        assert len(system_entries) == 1
        assert system_entries[0].from_state == "system_normal"
        assert system_entries[0].to_state == "degraded_warning"


class TestPrintHistoryDoesNotCrash:

    def test_base_fsm_print_history_produces_output(self, capsys):
        fsm = GovernanceFSM(request_id="cov-002")
        context = {"ambiguity_flag": False, "clearance": 3, "required_clearance": 2, "risk_score": 10}
        fsm.run_until_stuck(context)

        fsm.print_history()
        captured = capsys.readouterr()

        assert "idle -> request_received" in captured.out
        assert "closed" in captured.out

    def test_decision_logger_print_all_produces_output(self, capsys):
        logger = DecisionLogger()
        recovery = RecoveryFSM()
        processor = SystemAwareRequestProcessor(recovery, logger)

        req = GovernanceFSM(request_id="cov-003")
        context = {"ambiguity_flag": False, "clearance": 3, "required_clearance": 2, "risk_score": 10}
        processor.process_until_stuck(req, context)

        logger.print_all()
        captured = capsys.readouterr()

        assert "request_transition" in captured.out
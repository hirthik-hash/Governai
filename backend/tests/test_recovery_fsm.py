# backend/tests/test_recovery_fsm.py

import pytest
from fsm.recovery_fsm import RecoveryFSM
from fsm.states import SystemState
from fsm.transitions import SYSTEM_TRANSITIONS
from fsm.governance_fsm import InvalidTransitionError, AmbiguousTransitionError


class TestNormalToWarning:

    def test_starts_in_system_normal(self):
        rfsm = RecoveryFSM()
        assert rfsm.state == SystemState.SYSTEM_NORMAL

    def test_unhealthy_signal_moves_to_degraded_warning(self):
        rfsm = RecoveryFSM()
        new_state = rfsm.transition({"system_healthy": False})
        assert new_state == SystemState.DEGRADED_WARNING

    def test_healthy_signal_does_not_move_out_of_normal(self):
        rfsm = RecoveryFSM()
        assert rfsm.can_transition({"system_healthy": True}) is False


class TestWarningResolvesWithoutSafeMode:

    def test_warning_recovers_directly_to_normal_without_critical(self):
        rfsm = RecoveryFSM()
        rfsm.transition({"system_healthy": False})  # -> DEGRADED_WARNING

        new_state = rfsm.transition({"system_healthy": True})

        assert new_state == SystemState.SYSTEM_NORMAL

    def test_warning_never_touched_safe_mode_if_resolved_early(self):
        rfsm = RecoveryFSM()
        rfsm.transition({"system_healthy": False})
        rfsm.transition({"system_healthy": True})

        visited = [r.to_state for r in rfsm.history]
        assert SystemState.SAFE_MODE_ACTIVE not in visited


class TestWarningEscalatesToSafeMode:

    def test_critical_failure_moves_to_safe_mode(self):
        rfsm = RecoveryFSM()
        rfsm.transition({"system_healthy": False})  # -> DEGRADED_WARNING

        new_state = rfsm.transition({"critical_failure": True})

        assert new_state == SystemState.SAFE_MODE_ACTIVE

    def test_is_safe_mode_reports_true_only_in_safe_mode_active(self):
        rfsm = RecoveryFSM()
        assert rfsm.is_safe_mode() is False

        rfsm.transition({"system_healthy": False})
        assert rfsm.is_safe_mode() is False

        rfsm.transition({"critical_failure": True})
        assert rfsm.is_safe_mode() is True


class TestSafeModeIsNotTerminal:

    def test_safe_mode_can_transition_onward(self):
        rfsm = RecoveryFSM()
        rfsm.transition({"system_healthy": False})
        rfsm.transition({"critical_failure": True})  # -> SAFE_MODE_ACTIVE

        assert rfsm.can_transition({"system_healthy": True}) is True

    def test_safe_mode_moves_to_restoring_on_recovery_signal(self):
        rfsm = RecoveryFSM()
        rfsm.transition({"system_healthy": False})
        rfsm.transition({"critical_failure": True})

        new_state = rfsm.transition({"system_healthy": True})

        assert new_state == SystemState.RESTORING


class TestRestorationOutcomes:

    def _reach_restoring(self) -> RecoveryFSM:
        rfsm = RecoveryFSM()
        rfsm.transition({"system_healthy": False})
        rfsm.transition({"critical_failure": True})
        rfsm.transition({"system_healthy": True})  # -> RESTORING
        return rfsm

    def test_restoring_succeeds_to_system_normal(self):
        rfsm = self._reach_restoring()

        new_state = rfsm.transition({"system_healthy": True})

        assert new_state == SystemState.SYSTEM_NORMAL

    def test_restoring_failure_reverts_to_safe_mode(self):
        rfsm = self._reach_restoring()

        new_state = rfsm.transition({"system_healthy": False})

        assert new_state == SystemState.SAFE_MODE_ACTIVE

    def test_full_failure_and_recovery_cycle_history(self):
        rfsm = self._reach_restoring()
        rfsm.transition({"system_healthy": True})  # -> SYSTEM_NORMAL

        path = [r.to_state for r in rfsm.history]
        assert path == [
            SystemState.DEGRADED_WARNING,
            SystemState.SAFE_MODE_ACTIVE,
            SystemState.RESTORING,
            SystemState.SYSTEM_NORMAL,
        ]


class TestNoInvalidTransitionRaisesCorrectly:

    def test_transitioning_with_no_matching_condition_raises(self):
        rfsm = RecoveryFSM()
        with pytest.raises(InvalidTransitionError):
            rfsm.transition({"system_healthy": True})  # nothing fires from NORMAL


class TestRulebookIntegrityForSafeMode:

    def test_no_direct_path_from_normal_to_safe_mode(self):
        outgoing = [t for t in SYSTEM_TRANSITIONS if t.from_state == SystemState.SYSTEM_NORMAL]
        destinations = [t.to_state for t in outgoing]

        assert SystemState.SAFE_MODE_ACTIVE not in destinations

    def test_system_normal_has_exactly_one_outgoing_transition(self):
        outgoing = [t for t in SYSTEM_TRANSITIONS if t.from_state == SystemState.SYSTEM_NORMAL]

        assert len(outgoing) == 1
        assert outgoing[0].to_state == SystemState.DEGRADED_WARNING
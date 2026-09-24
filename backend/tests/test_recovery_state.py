# backend/tests/test_recovery_state.py

"""
Day 81: RedisRecoveryStateStore, and RecoveryFSM sharing state across
two SEPARATE instances via one fakeredis server (standing in for two
worker processes sharing one real Redis, the same pattern used for
DistributedLock in Day 80).
"""

import fakeredis
import pytest
import redis

from database.recovery_state import RecoveryStateUnavailableError, RedisRecoveryStateStore
from fsm.recovery_fsm import RecoveryFSM
from fsm.states import SystemState


@pytest.fixture
def server():
    return fakeredis.FakeServer()


def _client(server):
    return fakeredis.FakeStrictRedis(server=server, decode_responses=True)


class TestRedisRecoveryStateStoreStandalone:

    def test_returns_the_default_when_nothing_was_ever_written(self, server):
        store = RedisRecoveryStateStore(_client(server))

        assert store.get(default=SystemState.SYSTEM_NORMAL) == SystemState.SYSTEM_NORMAL

    def test_set_then_get_round_trips(self, server):
        store = RedisRecoveryStateStore(_client(server))

        store.set(SystemState.SAFE_MODE_ACTIVE)

        assert store.get(default=SystemState.SYSTEM_NORMAL) == SystemState.SAFE_MODE_ACTIVE

    def test_a_second_client_sees_the_first_clients_write(self, server):
        RedisRecoveryStateStore(_client(server)).set(SystemState.DEGRADED_WARNING)

        assert RedisRecoveryStateStore(_client(server)).get(default=SystemState.SYSTEM_NORMAL) == SystemState.DEGRADED_WARNING

    def test_every_system_state_value_round_trips(self, server):
        store = RedisRecoveryStateStore(_client(server))
        for state in SystemState:
            store.set(state)
            assert store.get(default=SystemState.SYSTEM_NORMAL) == state

    def test_unreachable_redis_raises_rather_than_silently_defaulting(self):
        broken_client = redis.Redis.from_url("redis://localhost:1/0")
        store = RedisRecoveryStateStore(broken_client)

        with pytest.raises(RecoveryStateUnavailableError):
            store.get(default=SystemState.SYSTEM_NORMAL)
        with pytest.raises(RecoveryStateUnavailableError):
            store.set(SystemState.SAFE_MODE_ACTIVE)

    def test_a_garbage_value_in_redis_is_reported_not_silently_ignored(self, server):
        client = _client(server)
        client.set("governai:recovery:state", "not-a-real-state")
        store = RedisRecoveryStateStore(client)

        with pytest.raises(RecoveryStateUnavailableError):
            store.get(default=SystemState.SYSTEM_NORMAL)

    def test_a_custom_key_is_isolated_from_the_default_key(self, server):
        default_key_store = RedisRecoveryStateStore(_client(server))
        custom_key_store = RedisRecoveryStateStore(_client(server), key="some:other:key")

        default_key_store.set(SystemState.SAFE_MODE_ACTIVE)

        assert custom_key_store.get(default=SystemState.SYSTEM_NORMAL) == SystemState.SYSTEM_NORMAL


class TestRecoveryFSMWithNoStoreIsUnchanged:

    def test_behaves_as_a_plain_in_memory_attribute(self):
        fsm = RecoveryFSM()
        context = {"system_healthy": False, "critical_failure": True}

        assert fsm.state == SystemState.SYSTEM_NORMAL
        fsm.transition(context)  # -> DEGRADED_WARNING
        assert fsm.state == SystemState.DEGRADED_WARNING
        fsm.transition(context)  # -> SAFE_MODE_ACTIVE
        assert fsm.state == SystemState.SAFE_MODE_ACTIVE

    def test_two_instances_with_no_store_do_not_share_anything(self):
        fsm_a = RecoveryFSM()
        fsm_b = RecoveryFSM()
        context = {"system_healthy": False, "critical_failure": True}

        fsm_a.transition(context)
        fsm_a.transition(context)

        assert fsm_a.state == SystemState.SAFE_MODE_ACTIVE
        assert fsm_b.state == SystemState.SYSTEM_NORMAL


class TestTwoRecoveryFSMInstancesSharingState:
    """
    The actual Day 81 property: two SEPARATE RecoveryFSM objects (as two
    worker processes would each have their own) must agree, because both
    read/write through the SAME shared store.
    """

    CRITICAL = {"system_healthy": False, "critical_failure": True}

    def _fsm(self, server):
        return RecoveryFSM(state_store=RedisRecoveryStateStore(_client(server)))

    def _drive_to_safe_mode(self, fsm):
        fsm.transition(self.CRITICAL)  # SYSTEM_NORMAL -> DEGRADED_WARNING
        fsm.transition(self.CRITICAL)  # DEGRADED_WARNING -> SAFE_MODE_ACTIVE

    def test_a_transition_by_one_instance_is_immediately_visible_to_the_other(self, server):
        fsm_a = self._fsm(server)
        fsm_b = self._fsm(server)

        self._drive_to_safe_mode(fsm_a)

        assert fsm_b.state == SystemState.SAFE_MODE_ACTIVE
        assert fsm_b.is_safe_mode() is True

    def test_the_intermediate_state_is_visible_too_not_just_the_final_one(self, server):
        fsm_a = self._fsm(server)
        fsm_b = self._fsm(server)

        fsm_a.transition(self.CRITICAL)  # -> DEGRADED_WARNING only

        assert fsm_b.state == SystemState.DEGRADED_WARNING
        assert fsm_b.is_safe_mode() is False

    def test_a_freshly_constructed_third_instance_also_sees_the_shared_state(self, server):
        fsm_a = self._fsm(server)
        self._drive_to_safe_mode(fsm_a)

        fsm_c = self._fsm(server)

        assert fsm_c.is_safe_mode() is True

    def test_recovery_is_also_visible_across_instances(self, server):
        fsm_a = self._fsm(server)
        fsm_b = self._fsm(server)
        self._drive_to_safe_mode(fsm_a)
        assert fsm_b.is_safe_mode() is True

        fsm_a.transition({"system_healthy": True})  # SAFE_MODE_ACTIVE -> RESTORING
        fsm_a.transition({"system_healthy": True})  # RESTORING -> SYSTEM_NORMAL

        assert fsm_b.is_safe_mode() is False
        assert fsm_b.state == SystemState.SYSTEM_NORMAL

    def test_each_instance_still_keeps_its_own_local_fallback_cache(self, server):
        # Not exercised while Redis is reachable, but proves the local
        # cache tracks every write - the fallback a fully offline
        # process would still have, however stale.
        fsm_a = self._fsm(server)
        self._drive_to_safe_mode(fsm_a)

        assert fsm_a._local_state == SystemState.SAFE_MODE_ACTIVE

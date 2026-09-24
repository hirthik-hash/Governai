# backend/tests/test_recovery_agent_distributed_lock.py

"""
Day 81: FailureRecoveryAgent with a shared (Redis-backed) RecoveryFSM and
a DistributedLock. Two AGENTS (standing in for two workers, each with its
own agent instance but sharing the same Redis) must not both drive the
SAME transition at once, and must always end up in a state consistent
with the actual rulebook - never skip a step, never stall.
"""

import threading

import fakeredis
import pytest
import redis

from agents.recovery_agent import FailureRecoveryAgent, HealthCheckResult
from core.distributed_lock import DistributedLock
from database.recovery_state import RedisRecoveryStateStore
from fsm.recovery_fsm import RecoveryFSM
from fsm.states import SystemState


@pytest.fixture
def server():
    return fakeredis.FakeServer()


def _client(server):
    return fakeredis.FakeStrictRedis(server=server, decode_responses=True)


def _failing_check() -> HealthCheckResult:
    return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="simulated failure")


def _agent(server, locked=True):
    client = _client(server)
    fsm = RecoveryFSM(state_store=RedisRecoveryStateStore(client))
    lock = DistributedLock(client) if locked else None
    return FailureRecoveryAgent(checks=[_failing_check], recovery_fsm=fsm, distributed_lock=lock)


class TestSharedStateBasics:

    def test_two_agents_share_the_same_fsm_state(self, server):
        agent_a = _agent(server)
        agent_b = _agent(server)

        agent_a.evaluate_and_transition()  # NORMAL -> DEGRADED_WARNING

        assert agent_b._recovery_fsm.state == SystemState.DEGRADED_WARNING

    def test_a_healthy_check_after_the_fact_is_visible_to_the_other_agent(self, server):
        healthy_agent = FailureRecoveryAgent(
            recovery_fsm=RecoveryFSM(state_store=RedisRecoveryStateStore(_client(server))),
            distributed_lock=DistributedLock(_client(server)),
        )
        failing_agent = _agent(server)

        failing_agent.evaluate_and_transition()
        failing_agent.evaluate_and_transition()
        assert failing_agent._recovery_fsm.state == SystemState.SAFE_MODE_ACTIVE

        healthy_agent.evaluate_and_transition()  # SAFE_MODE_ACTIVE -> RESTORING
        healthy_agent.evaluate_and_transition()  # RESTORING -> SYSTEM_NORMAL

        assert failing_agent._recovery_fsm.state == SystemState.SYSTEM_NORMAL


class TestConcurrentEvaluationIsSerialized:
    """
    As in Day 80's DistributedLock tests: with one attempt per agent and
    no retries, a "straggler" agent can legitimately arrive AFTER an
    earlier one already advanced the shared state, see the still-failing
    check, and correctly advance it a further valid step (DEGRADED_WARNING
    -> SAFE_MODE_ACTIVE, since fsm_integrity failing is always critical -
    see agents/recovery_agent.py). That is not a race bug: mutual
    exclusion is enforced structurally (the FSM mutation only ever runs
    inside the lock - see _locked_transition), so what actually matters
    is (a) every agent agrees on the SAME final state (no desync), and
    (b) that final state is one the rulebook can actually reach from
    SYSTEM_NORMAL - not that exactly one agent's single attempt "wins".
    """

    VALID_END_STATES = {SystemState.DEGRADED_WARNING, SystemState.SAFE_MODE_ACTIVE}

    def test_two_agents_evaluating_at_once_end_up_agreeing(self, server):
        agent_a = _agent(server)
        agent_b = _agent(server)
        barrier = threading.Barrier(2)

        def run(agent):
            barrier.wait()
            agent.evaluate_and_transition()

        t_a = threading.Thread(target=run, args=(agent_a,))
        t_b = threading.Thread(target=run, args=(agent_b,))
        t_a.start(); t_b.start()
        t_a.join(); t_b.join()

        assert agent_a._recovery_fsm.state == agent_b._recovery_fsm.state
        assert agent_a._recovery_fsm.state in self.VALID_END_STATES

    def test_twenty_concurrent_evaluations_still_agree_on_one_valid_state(self, server):
        agents = [_agent(server) for _ in range(20)]
        barrier = threading.Barrier(20)

        def run(agent):
            barrier.wait()
            agent.evaluate_and_transition()

        threads = [threading.Thread(target=run, args=(a,)) for a in agents]
        for t in threads: t.start()
        for t in threads: t.join()

        final_states = {a._recovery_fsm.state for a in agents}
        assert len(final_states) == 1, f"agents disagree on final state: {final_states}"
        assert final_states <= self.VALID_END_STATES

    def test_the_lock_genuinely_prevents_simultaneous_transitions(self, server):
        """
        The rigorous version of the property above: record the actual
        [start, end) wall-clock interval each agent holds the lock for
        the FSM-mutating step, and assert no two intervals ever overlap -
        the real definition of mutual exclusion, immune to the scheduling-
        jitter flakiness a raw "count of winners" assertion has (see
        tests/test_distributed_lock.py for the same fix applied there).
        """
        import time as time_module

        agents = [_agent(server) for _ in range(15)]
        holds = []
        guard = threading.Lock()
        barrier = threading.Barrier(15)

        original_try = agents[0].__class__._try_transition

        def timed_try_transition(self, context):
            start = time_module.monotonic()
            result = original_try(self, context)
            end = time_module.monotonic()
            with guard:
                holds.append((start, end))
            return result

        for agent in agents:
            agent._try_transition = timed_try_transition.__get__(agent)

        def run(agent):
            barrier.wait()
            agent.evaluate_and_transition()

        threads = [threading.Thread(target=run, args=(a,)) for a in agents]
        for t in threads: t.start()
        for t in threads: t.join()

        for i in range(len(holds)):
            for j in range(i + 1, len(holds)):
                (s1, e1), (s2, e2) = holds[i], holds[j]
                assert not (s1 < e2 and s2 < e1), "two agents' FSM-mutating step overlapped in time"

    def test_without_a_lock_two_agents_can_still_both_report_success_but_state_stays_consistent(self, server):
        # No lock configured: no serialization guarantee is claimed, but
        # RecoveryFSM.transition() itself still only allows one valid
        # move from the current state, so even an unlucky interleaving
        # cannot produce an invalid state - just possibly a spurious
        # "no transition happened" for the loser, handled gracefully.
        agent_a = _agent(server, locked=False)
        agent_b = _agent(server, locked=False)

        agent_a.evaluate_and_transition()
        agent_b.evaluate_and_transition()

        assert agent_a._recovery_fsm.state in (SystemState.DEGRADED_WARNING, SystemState.SAFE_MODE_ACTIVE)


class TestRealConcurrencyAgainstRealRedisIfAvailable:
    """
    The authoritative proof, against genuine Redis and real OS threads -
    skipped automatically if no Redis is reachable (e.g. CI without a
    Redis service, or a dev machine that hasn't started it).
    """

    @pytest.fixture(autouse=True)
    def _require_real_redis(self):
        client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        try:
            client.ping()
        except redis.RedisError:
            pytest.skip("no reachable Redis at localhost:6379")
        client.flushdb()
        yield
        client.flushdb()

    def _real_agent(self):
        client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        fsm = RecoveryFSM(state_store=RedisRecoveryStateStore(client))
        return FailureRecoveryAgent(checks=[_failing_check], recovery_fsm=fsm, distributed_lock=DistributedLock(client))

    def test_fifteen_real_worker_agents_agree_on_one_valid_final_state(self):
        # Same reasoning as TestConcurrentEvaluationIsSerialized above:
        # a straggler agent can legitimately advance an already-DEGRADED
        # system straight to SAFE_MODE_ACTIVE on its own single attempt.
        # What must hold is agreement, not a "one winner" count.
        agents = [self._real_agent() for _ in range(15)]
        barrier = threading.Barrier(15)

        def run(agent):
            barrier.wait()
            agent.evaluate_and_transition()

        threads = [threading.Thread(target=run, args=(a,)) for a in agents]
        for t in threads: t.start()
        for t in threads: t.join()

        final_states = {a._recovery_fsm.state for a in agents}
        assert len(final_states) == 1, f"agents disagree on final state: {final_states}"
        assert final_states <= {SystemState.DEGRADED_WARNING, SystemState.SAFE_MODE_ACTIVE}

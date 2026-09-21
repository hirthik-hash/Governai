# backend/tests/test_directory_parity.py

"""
Day 75: the database-backed pipeline must behave IDENTICALLY to the
seed-data pipeline.

Two pipelines are built per scenario - one with SeedDirectory, one with
DatabaseDirectory over a seeded in-memory database - fed the same
request, and compared field by field (minus the per-call timestamps).
The matrix covers every user x every resource, at a business-hours and
an after-hours moment, including escalations resolved both ways. This is
the safety net for moving the API onto the database (Days 78-79).

The clock is fixed through the validation agent's injected now_fn so a
test can never straddle a business-hours boundary.
"""

from dataclasses import asdict
from datetime import datetime

import pytest

from core.orchestrator import RequestPipeline
from data.demo_data import DEMO_RESOURCES, DEMO_USERS
from data.directory import SeedDirectory
from data.seed_data import SEED_RESOURCES, SEED_USERS
from database.directory import DatabaseDirectory
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory
from fsm.states import SystemState

TUESDAY_10AM = datetime(2026, 9, 22, 10, 0)   # inside business hours
TUESDAY_10PM = datetime(2026, 9, 22, 22, 0)   # after hours
CLOCKS = (TUESDAY_10AM, TUESDAY_10PM)

DATASETS = {
    "seed": (SEED_USERS, SEED_RESOURCES),
    "demo": (DEMO_USERS, DEMO_RESOURCES),
}


@pytest.fixture(scope="module")
def factories():
    built = {}
    for name, (users, resources) in DATASETS.items():
        engine = make_engine("sqlite://")
        init_db(engine)
        factory = make_session_factory(engine)
        with factory() as session:
            seed_database(session, users=users, resources=resources)
            session.commit()
        built[name] = factory
    return built


def _pair(dataset, factories, clock):
    users, resources = DATASETS[dataset]
    seed_pipeline = RequestPipeline(directory=SeedDirectory(users, resources))
    db_pipeline = RequestPipeline(directory=DatabaseDirectory(factories[dataset]))
    for pipeline in (seed_pipeline, db_pipeline):
        pipeline.validation_agent._now_fn = lambda: clock
    return seed_pipeline, db_pipeline


def _norm(result):
    audit = asdict(result.audit_record) if result.audit_record is not None else None
    if audit is not None:
        audit.pop("timestamp")
    notification = asdict(result.notification) if result.notification is not None else None
    if notification is not None:
        notification.pop("sent_at")
    return {
        "status": result.status,
        "fsm_state": result.fsm_state,
        "candidate_resource_ids": result.candidate_resource_ids,
        "errors": result.errors,
        "audit": audit,
        "notification": notification,
    }


def _both(seed_pipeline, db_pipeline, method, *args, **kwargs):
    seed_result = getattr(seed_pipeline, method)(*args, **kwargs)
    db_result = getattr(db_pipeline, method)(*args, **kwargs)
    assert _norm(seed_result) == _norm(db_result), f"{method}{args}{kwargs}"
    return seed_result


USER_PARAMS = [("seed", u.id) for u in SEED_USERS] + [("demo", u.id) for u in DEMO_USERS]


class TestFullMatrixParity:

    @pytest.mark.parametrize("dataset,user_id", USER_PARAMS, ids=[f"{d}-{u}" for d, u in USER_PARAMS])
    def test_every_resource_for_this_user(self, factories, dataset, user_id):
        _users, resources = DATASETS[dataset]

        for resource in resources:
            for clock in CLOCKS:
                request = {"user_id": user_id, "resource_id": resource.id, "session_token": "abc"}

                seed_pipeline, db_pipeline = _pair(dataset, factories, clock)
                first = _both(seed_pipeline, db_pipeline, "submit_request", {**request, "request_id": "m-1"})
                if first.status == "pending_approval":
                    _both(seed_pipeline, db_pipeline, "resolve_escalation", "m-1", human_decision="approved")

                    seed_pipeline, db_pipeline = _pair(dataset, factories, clock)
                    _both(seed_pipeline, db_pipeline, "submit_request", {**request, "request_id": "m-2"})
                    _both(seed_pipeline, db_pipeline, "resolve_escalation", "m-2", human_decision="rejected")


class TestInputVariantsParity:

    @pytest.mark.parametrize("dataset", ["seed", "demo"])
    def test_fuzzy_names_unknown_ids_missing_fields_and_urgency(self, factories, dataset):
        users, _resources = DATASETS[dataset]
        requests = []
        for name in ("employee", "report", "database", "e", "", "zzz", "LOG", "system", "documents"):
            requests.append({"user_id": "user-002", "resource_name": name})
        requests += [
            {"user_id": "user-999", "resource_id": "resource-001"},
            {"user_id": "user-001", "resource_id": "resource-999"},
            {"user_id": "user-001"},
            {"resource_id": "resource-001"},
            {"user_id": "user-001", "resource_id": "resource-001", "urgency": "high"},
            {"user_id": "user-001", "resource_id": "resource-001", "urgency": "urgent"},
            {"user_id": users[0].id, "resource_id": "resource-003", "role": "CISO"},
            {"user_id": "user-001", "resource_id": "resource-001", "session_expired": True},
            {"user_id": "user-001", "resource_id": "resource-001", "location": "Atlantis"},
        ]

        for index, request in enumerate(requests):
            for clock in CLOCKS:
                seed_pipeline, db_pipeline = _pair(dataset, factories, clock)
                _both(seed_pipeline, db_pipeline, "submit_request",
                      {**request, "request_id": f"v-{index}", "session_token": request.get("session_token", "abc")})


class TestStatefulSequenceParity:

    @pytest.mark.parametrize("dataset", ["seed", "demo"])
    def test_repeated_requests_on_one_pipeline_accumulate_identically(self, factories, dataset):
        seed_pipeline, db_pipeline = _pair(dataset, factories, TUESDAY_10AM)
        base = {"user_id": "user-001", "session_token": "abc"}

        for index in range(8):  # enough to trip rapid_succession on both
            resource = ("resource-001", "resource-004", "resource-003")[index % 3]
            _both(seed_pipeline, db_pipeline, "submit_request", {**base, "resource_id": resource, "request_id": f"s-{index}"})

        seed_log = [(e.entry_type, e.from_state, e.to_state, e.request_id) for e in seed_pipeline.decision_logger.all_entries()]
        db_log = [(e.entry_type, e.from_state, e.to_state, e.request_id) for e in db_pipeline.decision_logger.all_entries()]
        assert seed_log == db_log
        assert [_norm_record(r) for r in seed_pipeline.audit_records] == [_norm_record(r) for r in db_pipeline.audit_records]

    @pytest.mark.parametrize("dataset", ["seed", "demo"])
    def test_safe_mode_blocks_identically(self, factories, dataset):
        seed_pipeline, db_pipeline = _pair(dataset, factories, TUESDAY_10AM)
        request = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc", "request_id": "sm-1"}
        _both(seed_pipeline, db_pipeline, "submit_request", request)
        for pipeline in (seed_pipeline, db_pipeline):
            pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE

        blocked = _both(seed_pipeline, db_pipeline, "resolve_escalation", "sm-1", human_decision="approved")

        assert blocked.status == "blocked_safe_mode"
        _both(seed_pipeline, db_pipeline, "submit_request",
              {"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc", "request_id": "sm-2"})


def _norm_record(record):
    data = asdict(record)
    data.pop("timestamp")
    return data

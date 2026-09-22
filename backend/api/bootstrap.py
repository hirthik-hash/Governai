# backend/api/bootstrap.py

"""
Composition root (Day 76): builds the real application from Settings.

Kept out of main.py so tests can call build_app() with their own Settings
(an in-memory database, production mode, ...) without importing main.py,
which would create the development database file as a side effect.

For now the pipeline is database-backed for users and resources and
and Day 78-79 persist the audit ledger, decision log and pending
escalations to that same database.
"""

import redis
from fastapi import FastAPI

from api.app import create_app
from auth.passwords import PasswordService
from auth.seeding import seed_demo_credentials, seed_demo_roles
from auth.session_validator import JwtSessionValidator
from auth.service import AuthService
from auth.tokens import TokenService
from core.config import DEFAULT_JWT_SECRET, Settings, settings
from core.distributed_lock import DistributedLock
from core.orchestrator import RequestPipeline
from core.redis_client import make_redis_client
from data.demo_data import DEMO_ADMIN_USER_IDS, DEMO_RESOURCES, DEMO_USERS
from database.credentials import DatabaseCredentialStore
from database.audit_store import DatabaseAuditStore, PersistentDecisionLogger
from database.pending_store import DatabasePendingEscalationStore
from database.directory import DatabaseDirectory
from database.roles import DatabaseRoleStore
from database.seeding import seed_database
from database.session import check_schema, init_db, make_engine, make_session_factory, session_scope


def build_app(
    app_settings: Settings = settings,
    passwords: PasswordService = None,
    redis_client: redis.Redis = None,
) -> FastAPI:
    if app_settings.is_production() and app_settings.jwt_secret_key == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET_KEY is still the public development default. "
            "Set a private value (at least 32 characters) when APP_ENV=production."
        )

    # Day 80: fail loud and immediately if Redis is unreachable, rather
    # than starting up successfully and only discovering it on the first
    # real request. redis_client is injectable (like `passwords` above)
    # so tests can pass a fakeredis client instead of depending on a real
    # local Redis server, per this project's established testing convention.
    redis_client = redis_client or make_redis_client(app_settings.redis_url)
    try:
        redis_client.ping()
    except redis.RedisError as error:
        raise RuntimeError(
            f"Cannot reach Redis at {app_settings.redis_url}: {error}. "
            "GovernAI requires Redis for cross-worker request locking - "
            "start it (e.g. `docker compose up redis`) before running the app."
        ) from error

    engine = make_engine(app_settings.database_url)
    init_db(engine)
    check_schema(engine)
    session_factory = make_session_factory(engine)

    development = not app_settings.is_production()
    if development:
        with session_scope(session_factory) as session:
            seed_database(session, users=DEMO_USERS, resources=DEMO_RESOURCES)

    directory = DatabaseDirectory(session_factory)
    credentials = DatabaseCredentialStore(session_factory)
    tokens = TokenService(
        app_settings.jwt_secret_key,
        app_settings.jwt_algorithm,
        app_settings.jwt_expiry_minutes,
    )
    auth_service = AuthService(
        credentials=credentials,
        directory=directory,
        passwords=passwords or PasswordService(),
        tokens=tokens,
        roles=DatabaseRoleStore(session_factory),
    )
    if development:
        seed_demo_credentials(
            auth_service, credentials, [u.id for u in DEMO_USERS], app_settings.demo_user_password
        )
        seed_demo_roles(auth_service, DEMO_ADMIN_USER_IDS)

    # Day 78: the audit ledger and decision log persist to the same
    # database, and the pipeline refuses a request_id that already has a
    # final decision - even from a previous run.
    audit_store = DatabaseAuditStore(session_factory)
    # Day 80: a lock per request_id, held for the duration of
    # submit_request()/resolve_escalation(), so multiple uvicorn workers
    # (or multiple app instances behind a load balancer) sharing this
    # same Redis and database cannot both decide the same request_id at
    # once. A failure to reach Redis here is NOT caught - if Redis is
    # unreachable, the app should fail to start rather than silently run
    # without the multi-worker safety it was configured to have.
    distributed_lock = DistributedLock(redis_client)
    pipeline = RequestPipeline(
        directory=directory,
        session_validator=JwtSessionValidator(tokens),
        decision_logger=PersistentDecisionLogger(session_factory),
        audit_store=audit_store,
        pending_store=DatabasePendingEscalationStore(session_factory),
        distributed_lock=distributed_lock,
    )
    pipeline.audit_records = audit_store.all()  # restore the ledger's read path after a restart

    return create_app(pipeline=pipeline, auth_service=auth_service)

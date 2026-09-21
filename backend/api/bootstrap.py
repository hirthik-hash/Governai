# backend/api/bootstrap.py

"""
Composition root (Day 76): builds the real application from Settings.

Kept out of main.py so tests can call build_app() with their own Settings
(an in-memory database, production mode, ...) without importing main.py,
which would create the development database file as a side effect.

For now the pipeline is database-backed for users and resources and
in-memory for pending escalations, audit records and the decision log;
Days 78-79 move those onto the database too.
"""

from fastapi import FastAPI

from api.app import create_app
from auth.passwords import PasswordService
from auth.seeding import seed_demo_credentials
from auth.service import AuthService
from auth.tokens import TokenService
from core.config import DEFAULT_JWT_SECRET, Settings, settings
from core.orchestrator import RequestPipeline
from data.demo_data import DEMO_RESOURCES, DEMO_USERS
from database.credentials import DatabaseCredentialStore
from database.directory import DatabaseDirectory
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory, session_scope


def build_app(app_settings: Settings = settings, passwords: PasswordService = None) -> FastAPI:
    if app_settings.is_production() and app_settings.jwt_secret_key == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET_KEY is still the public development default. "
            "Set a private value (at least 32 characters) when APP_ENV=production."
        )

    engine = make_engine(app_settings.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)

    development = not app_settings.is_production()
    if development:
        with session_scope(session_factory) as session:
            seed_database(session, users=DEMO_USERS, resources=DEMO_RESOURCES)

    directory = DatabaseDirectory(session_factory)
    credentials = DatabaseCredentialStore(session_factory)
    auth_service = AuthService(
        credentials=credentials,
        directory=directory,
        passwords=passwords or PasswordService(),
        tokens=TokenService(
            app_settings.jwt_secret_key,
            app_settings.jwt_algorithm,
            app_settings.jwt_expiry_minutes,
        ),
    )
    if development:
        seed_demo_credentials(
            auth_service, credentials, [u.id for u in DEMO_USERS], app_settings.demo_user_password
        )

    return create_app(pipeline=RequestPipeline(directory=directory), auth_service=auth_service)

# backend/tests/test_database_schema_check.py

"""Day 77: an out-of-date development database fails loudly at startup."""

import pytest
from sqlalchemy import text

from api.bootstrap import build_app
from auth.passwords import PasswordService
from core.config import Settings
from database.session import SchemaOutOfDateError, check_schema, init_db, make_engine

FAST = PasswordService(time_cost=1, memory_cost=8, parallelism=1)


def _old_users_table(engine):
    """The users table as it was before api_role existed."""
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE users (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, department VARCHAR NOT NULL, "
            "role VARCHAR NOT NULL, clearance_level INTEGER NOT NULL, is_blacklisted BOOLEAN NOT NULL, "
            "reports_to VARCHAR, password_hash VARCHAR)"
        ))


class TestCheckSchema:

    def test_a_freshly_created_schema_passes(self):
        engine = make_engine("sqlite://")
        init_db(engine)

        check_schema(engine)

    def test_a_missing_column_is_reported_with_the_fix(self):
        engine = make_engine("sqlite://")
        _old_users_table(engine)
        init_db(engine)  # creates the other tables, leaves the old users table alone

        with pytest.raises(SchemaOutOfDateError) as error:
            check_schema(engine)

        message = str(error.value)
        assert "users.api_role" in message
        assert "delete the file" in message

    def test_a_missing_table_is_reported(self):
        engine = make_engine("sqlite://")

        with pytest.raises(SchemaOutOfDateError, match="table 'users' is missing"):
            check_schema(engine)


class TestStartupUsesIt:

    def test_build_app_refuses_an_old_database_file(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'old.db'}"
        _old_users_table(make_engine(url))
        settings = Settings(_env_file=None, database_url=url, jwt_secret_key="k" * 40)

        with pytest.raises(SchemaOutOfDateError):
            build_app(settings, passwords=FAST)

    def test_build_app_accepts_a_database_file_it_created_itself(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'fresh.db'}"
        settings = Settings(_env_file=None, database_url=url, jwt_secret_key="k" * 40)

        build_app(settings, passwords=FAST)
        build_app(settings, passwords=FAST)  # a restart against the same file

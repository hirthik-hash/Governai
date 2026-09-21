# backend/tests/test_jwt_session_validator.py

"""Day 77: JwtSessionValidator, alone and inside a real RequestPipeline."""

import pytest

from auth.session_validator import JwtSessionValidator
from auth.tokens import TokenService
from core.orchestrator import RequestPipeline
from tests.api_harness import FakeClock, SECRET


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def tokens(clock):
    return TokenService(SECRET, expiry_minutes=60, now_fn=clock.now)


@pytest.fixture
def validator(tokens):
    return JwtSessionValidator(tokens)


class TestValidator:

    def test_a_valid_token_for_the_requesting_user(self, validator, tokens):
        token = tokens.issue("user-001").access_token

        assert validator.is_valid({"session_token": token, "user_id": "user-001"}) == (True, "Session valid")

    @pytest.mark.parametrize("token", [None, "", 5, ["x"]])
    def test_missing_or_non_string_token(self, validator, token):
        assert validator.is_valid({"session_token": token, "user_id": "user-001"}) == (False, "No session token provided")

    def test_garbage_token(self, validator):
        assert validator.is_valid({"session_token": "abc", "user_id": "user-001"}) == (False, "Session token is invalid")

    def test_another_users_token(self, validator, tokens):
        token = tokens.issue("user-002").access_token

        valid, reason = validator.is_valid({"session_token": token, "user_id": "user-001"})

        assert valid is False and "does not belong" in reason

    def test_no_user_id_to_match_against(self, validator, tokens):
        assert validator.is_valid({"session_token": tokens.issue("user-001").access_token})[0] is False

    def test_expired_token(self, validator, tokens, clock):
        token = tokens.issue("user-001").access_token
        clock.advance(3600)

        assert validator.is_valid({"session_token": token, "user_id": "user-001"}) == (False, "Session token has expired")

    def test_the_client_controlled_expired_flag_cannot_rescue_or_condemn_a_session(self, validator, tokens, clock):
        good = tokens.issue("user-001").access_token
        assert validator.is_valid({"session_token": good, "user_id": "user-001", "session_expired": True})[0] is True

        clock.advance(3600)
        assert validator.is_valid({"session_token": good, "user_id": "user-001", "session_expired": False})[0] is False


class TestInsideThePipeline:

    def _pipeline(self, tokens):
        return RequestPipeline(session_validator=JwtSessionValidator(tokens))

    def test_a_genuine_session_is_granted(self, tokens):
        result = self._pipeline(tokens).submit_request(
            {"user_id": "user-007", "resource_id": "resource-001", "session_token": tokens.issue("user-007").access_token}
        )

        assert result.status == "granted"

    @pytest.mark.parametrize("token", [None, "abc"])
    def test_the_old_placeholder_token_no_longer_works(self, tokens, token):
        result = self._pipeline(tokens).submit_request(
            {"user_id": "user-007", "resource_id": "resource-001", "session_token": token}
        )

        assert result.status == "error"

    def test_someone_elses_token_is_an_error_not_a_denial(self, tokens):
        result = self._pipeline(tokens).submit_request(
            {"user_id": "user-007", "resource_id": "resource-001", "session_token": tokens.issue("user-001").access_token}
        )

        assert result.status == "error" and result.audit_record is None

    def test_an_expired_token_is_an_error(self, tokens, clock):
        token = tokens.issue("user-007").access_token
        clock.advance(3600)

        result = self._pipeline(tokens).submit_request(
            {"user_id": "user-007", "resource_id": "resource-001", "session_token": token}
        )

        assert result.status == "error"

    def test_a_pipeline_without_a_validator_keeps_the_default_check(self):
        result = RequestPipeline().submit_request({"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"})

        assert result.status == "granted"

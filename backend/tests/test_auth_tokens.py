# backend/tests/test_auth_tokens.py

"""Day 76: JWT issuing and verification, including the attacks it must refuse."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from auth.errors import AuthenticationError, TokenExpiredError, TokenInvalidError
from auth.tokens import TokenService

SECRET = "s" * 40
NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.current = NOW

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service(clock):
    return TokenService(SECRET, expiry_minutes=60, now_fn=clock.now)


def _claims(clock, **overrides):
    issued = int(clock.now().timestamp())
    claims = {"sub": "user-001", "iss": "governai", "iat": issued, "exp": issued + 3600}
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


class TestIssueAndVerify:

    def test_round_trip_returns_the_user_id(self, service):
        assert service.verify(service.issue("user-004").access_token) == "user-004"

    def test_reports_the_lifetime_in_seconds(self, service):
        assert service.issue("user-001").expires_in_seconds == 3600

    def test_token_is_a_standard_hs256_jwt_with_the_expected_claims(self, service, clock):
        token = service.issue("user-001").access_token

        assert jwt.get_unverified_header(token)["alg"] == "HS256"
        claims = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False, "verify_iat": False})
        assert claims == {"sub": "user-001", "iss": "governai", "iat": int(NOW.timestamp()), "exp": int(NOW.timestamp()) + 3600}

    def test_token_carries_no_role_or_personal_data(self, service):
        claims = jwt.decode(service.issue("user-001").access_token, SECRET, algorithms=["HS256"],
                            options={"verify_exp": False, "verify_iat": False})

        assert set(claims) == {"sub", "iss", "iat", "exp"}


class TestExpiry:

    def test_valid_until_the_last_second(self, service, clock):
        token = service.issue("user-001").access_token
        clock.advance(3599)

        assert service.verify(token) == "user-001"

    def test_expired_exactly_at_the_expiry_instant(self, service, clock):
        token = service.issue("user-001").access_token
        clock.advance(3600)

        with pytest.raises(TokenExpiredError):
            service.verify(token)

    def test_expired_and_invalid_are_both_authentication_errors(self):
        assert issubclass(TokenExpiredError, AuthenticationError)
        assert issubclass(TokenInvalidError, AuthenticationError)


class TestRefusedTokens:

    def test_tampered_signature(self, service):
        token = service.issue("user-001").access_token
        header, payload, signature = token.split(".")
        forged = ".".join([header, payload, ("A" if signature[0] != "A" else "B") + signature[1:]])

        with pytest.raises(TokenInvalidError):
            service.verify(forged)

    def test_payload_swapped_to_another_user_keeps_the_old_signature(self, service, clock):
        token = service.issue("user-001").access_token
        other = jwt.encode(_claims(clock, sub="user-007"), SECRET, algorithm="HS256")
        forged = ".".join([token.split(".")[0], other.split(".")[1], token.split(".")[2]])

        with pytest.raises(TokenInvalidError):
            service.verify(forged)

    def test_signed_with_a_different_secret(self, service, clock):
        token = jwt.encode(_claims(clock), "x" * 40, algorithm="HS256")

        with pytest.raises(TokenInvalidError):
            service.verify(token)

    def test_alg_none_is_refused(self, service, clock):
        token = jwt.encode(_claims(clock), key=None, algorithm="none")

        with pytest.raises(TokenInvalidError):
            service.verify(token)

    def test_a_different_hmac_algorithm_is_refused_even_with_the_right_secret(self, service, clock):
        token = jwt.encode(_claims(clock), SECRET, algorithm="HS512")

        with pytest.raises(TokenInvalidError):
            service.verify(token)

    @pytest.mark.parametrize("missing", ["sub", "exp", "iat", "iss"])
    def test_every_required_claim_is_required(self, service, clock, missing):
        token = jwt.encode(_claims(clock, **{missing: None}), SECRET, algorithm="HS256")

        with pytest.raises(TokenInvalidError):
            service.verify(token)

    def test_wrong_issuer(self, service, clock):
        token = jwt.encode(_claims(clock, iss="someone-else"), SECRET, algorithm="HS256")

        with pytest.raises(TokenInvalidError):
            service.verify(token)

    @pytest.mark.parametrize("bad_subject", ["", 5, ["user-001"]])
    def test_subject_must_be_a_non_empty_string(self, service, clock, bad_subject):
        token = jwt.encode(_claims(clock, sub=bad_subject), SECRET, algorithm="HS256")

        with pytest.raises(TokenInvalidError):
            service.verify(token)

    @pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x", "....", " "])
    def test_garbage(self, service, garbage):
        with pytest.raises(TokenInvalidError):
            service.verify(garbage)


class TestConfigurationIsValidated:

    def test_short_secret_is_refused(self):
        with pytest.raises(ValueError, match="at least 32"):
            TokenService("too-short")

    @pytest.mark.parametrize("algorithm", ["none", "RS256", "hs256", ""])
    def test_only_hmac_algorithms_are_allowed(self, algorithm):
        with pytest.raises(ValueError):
            TokenService(SECRET, algorithm=algorithm)

    @pytest.mark.parametrize("minutes", [0, -5])
    def test_expiry_must_be_positive(self, minutes):
        with pytest.raises(ValueError):
            TokenService(SECRET, expiry_minutes=minutes)

    def test_tokens_from_another_issuer_setting_are_refused(self, clock):
        ours = TokenService(SECRET, issuer="governai", now_fn=clock.now)
        theirs = TokenService(SECRET, issuer="other-app", now_fn=clock.now)

        with pytest.raises(TokenInvalidError):
            ours.verify(theirs.issue("user-001").access_token)

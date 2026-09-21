# backend/tests/test_auth_passwords.py

"""Day 76: Argon2 password hashing and the password policy."""

import pytest

from auth.errors import WeakPasswordError
from auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, PasswordService

GOOD = "correct-horse-battery"


@pytest.fixture
def service():
    # Minimum Argon2 costs: the hash is just as real, but ~instant in tests.
    return PasswordService(time_cost=1, memory_cost=8, parallelism=1)


class TestHashing:

    def test_hash_is_argon2id_and_verifies(self, service):
        hashed = service.hash(GOOD)

        assert hashed.startswith("$argon2id$")
        assert service.verify(hashed, GOOD) is True

    def test_wrong_password_does_not_verify(self, service):
        assert service.verify(service.hash(GOOD), GOOD + "x") is False

    def test_the_same_password_hashes_differently_each_time(self, service):
        assert service.hash(GOOD) != service.hash(GOOD)

    def test_hash_does_not_contain_the_password(self, service):
        assert GOOD not in service.hash(GOOD)

    def test_garbage_hash_verifies_false_instead_of_raising(self, service):
        assert service.verify("not-a-hash", GOOD) is False
        assert service.verify("", GOOD) is False

    def test_absurdly_long_login_attempt_is_rejected_without_hashing(self, service):
        assert service.verify(service.hash(GOOD), "x" * 5000) is False


class TestPolicy:

    def test_minimum_length_boundary(self, service):
        service.hash("a" * MIN_PASSWORD_LENGTH)
        with pytest.raises(WeakPasswordError):
            service.hash("a" * (MIN_PASSWORD_LENGTH - 1))

    def test_maximum_length_boundary(self, service):
        service.hash("a" * MAX_PASSWORD_LENGTH)
        with pytest.raises(WeakPasswordError):
            service.hash("a" * (MAX_PASSWORD_LENGTH + 1))

    def test_policy_can_be_skipped_for_rehashing_a_legacy_password(self, service):
        assert service.verify(service.hash("short", enforce_policy=False), "short") is True

    def test_weak_password_error_is_a_value_error(self):
        assert issubclass(WeakPasswordError, ValueError)


class TestRehash:

    def test_hash_made_with_weaker_costs_needs_rehash(self):
        weak = PasswordService(time_cost=1, memory_cost=8, parallelism=1)
        stronger = PasswordService(time_cost=2, memory_cost=16, parallelism=1)

        assert stronger.needs_rehash(weak.hash(GOOD)) is True

    def test_hash_made_with_current_costs_does_not(self, service):
        assert service.needs_rehash(service.hash(GOOD)) is False


class TestDummyVerification:

    def test_verify_against_dummy_returns_nothing_and_never_raises(self, service):
        assert service.verify_against_dummy("anything at all") is None

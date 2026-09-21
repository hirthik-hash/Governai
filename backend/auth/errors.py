# backend/auth/errors.py

"""
Authentication errors (Day 76).

AuthenticationError is deliberately uninformative to the caller: the API
answers every failure the same way, so a response never reveals whether
a user id exists, whether the password or the token was the problem, or
why. The specific reason is logged server-side instead.
"""


class AuthenticationError(Exception):
    pass


class TokenInvalidError(AuthenticationError):
    """Malformed, wrongly signed, wrong issuer, wrong algorithm, or missing claims."""


class TokenExpiredError(AuthenticationError):
    pass


class WeakPasswordError(ValueError):
    """A password that does not meet the policy. Raised when SETTING one, never on login."""

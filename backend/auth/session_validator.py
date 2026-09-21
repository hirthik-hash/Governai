# backend/auth/session_validator.py

"""
JwtSessionValidator (Day 77): replaces the placeholder session check.

AccessValidationAgent has taken an injectable SessionValidator since
Day 33 precisely so this could be swapped in without changing the agent.
It accepts a request only if session_token is a genuine, unexpired token
whose subject is the user making the request. The old client-supplied
session_expired flag is deliberately ignored: a flag the client controls
must never be able to rescue (or condemn) a session.
"""

from agents.validation_agent import SessionValidator
from auth.errors import AuthenticationError, TokenExpiredError
from auth.tokens import TokenService


class JwtSessionValidator(SessionValidator):

    def __init__(self, tokens: TokenService):
        self._tokens = tokens

    def is_valid(self, input_data: dict) -> tuple[bool, str]:
        token = input_data.get("session_token")
        if not token or not isinstance(token, str):
            return False, "No session token provided"

        try:
            subject = self._tokens.verify(token)
        except TokenExpiredError:
            return False, "Session token has expired"
        except AuthenticationError:
            return False, "Session token is invalid"

        if subject != input_data.get("user_id"):
            return False, "Session token does not belong to the requesting user"

        return True, "Session valid"

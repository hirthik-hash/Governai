# backend/ai/fake_ollama_client.py

"""
Test doubles for OllamaClient (Day 84) - never call a real Ollama server
in the automated test suite, matching this project's convention for
every other external dependency (fakeredis for Redis, an in-memory
SQLite database, etc.). These implement the same generate()/is_available()
surface as OllamaClient so an agent built against that surface (the
Policy Intelligence Agent, Days 89+) can be tested without Ollama
installed at all.

Two flavors, for two different testing needs:
  FakeOllamaClient - one fixed response (or a canned mapping of prompt
    substrings to responses) for tests that don't care about call order.
  FakeOllamaClientSequence - a scripted list of responses returned one
    at a time, for tests that need to control exactly what each
    successive call returns (e.g. a multi-step agent workflow).
"""

from typing import Optional

from ai.ollama_client import OllamaError


class FakeOllamaClient:

    def __init__(self, default_response: str = "", responses_by_prompt_substring: Optional[dict] = None):
        self.default_response = default_response
        self._responses_by_substring = responses_by_prompt_substring or {}
        self.calls: list[dict] = []  # every generate() call, for tests to assert on

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        for substring, response in self._responses_by_substring.items():
            if substring in prompt:
                return response
        return self.default_response

    def is_available(self) -> bool:
        return True

    def list_models(self):
        return []

    def ensure_model_available(self) -> None:
        return None

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeOllamaClient":
        return self

    def __exit__(self, *exc_info) -> None:
        pass


class FakeOllamaClientSequence:
    """
    Returns each entry in `responses` in order, one per generate() call.
    An entry that is an Exception instance is raised instead of returned,
    so a test can script a transient failure mid-sequence. Raises
    OllamaError if generate() is called more times than there are
    scripted responses - a test relying on more calls than it planned
    for is a bug in the test, not something to paper over with a
    default.
    """

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._index = 0
        self.calls: list[dict] = []

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        if self._index >= len(self._responses):
            raise OllamaError(
                f"FakeOllamaClientSequence exhausted: {len(self._responses)} response(s) scripted, "
                f"call {self._index + 1} was made"
            )
        item = self._responses[self._index]
        self._index += 1
        if isinstance(item, Exception):
            raise item
        return item

    def is_available(self) -> bool:
        return True

    def list_models(self):
        return []

    def ensure_model_available(self) -> None:
        return None

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeOllamaClientSequence":
        return self

    def __exit__(self, *exc_info) -> None:
        pass

# backend/ai/ollama_client.py

"""
Ollama client (Day 84): the HTTP wrapper the Policy Intelligence Agent
(Days 89+) will use to talk to a local Ollama server. Nothing in this
file decides anything about governance - it is pure infrastructure,
matching the project's design philosophy that the AI layer is advisory
only and never touches the FSM directly (see core/orchestrator.py).

Uses Ollama's REST API directly (POST /api/generate, GET /api/tags) via
httpx - no ollama Python package dependency, so the only new requirement
is Ollama running somewhere reachable.

OllamaClient is deliberately thin: one call in, one string out. Anything
resembling a decision - conflict detection, citation verification,
grounding checks - belongs in the agent that USES this client (Days
89-97), not here.
"""

from dataclasses import dataclass
from typing import Optional

import httpx

DEFAULT_TIMEOUT_SECONDS = 60.0


class OllamaError(Exception):
    """Base class for every error this client raises."""


class OllamaUnavailableError(OllamaError):
    """Could not reach the Ollama server at all (connection refused, DNS failure, timeout)."""


class OllamaResponseError(OllamaError):
    """Ollama responded, but with an error status or a body this client cannot parse."""


class OllamaModelNotFoundError(OllamaError):
    """The configured model is not pulled on this Ollama server."""


@dataclass(frozen=True)
class OllamaModel:
    name: str
    size_bytes: int


class OllamaClient:

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        """
        transport is exposed only so tests can inject httpx.MockTransport
        and exercise this class's real request-building and response-
        parsing code without any network access or a hand-rolled fake
        standing in for it (see FakeOllamaClient in
        ai/fake_ollama_client.py for that - a different, simpler kind of
        test double used by code that CALLS an Ollama client, not by
        tests of this class itself).
        """
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout_seconds, transport=transport)

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        """
        One prompt in, the model's full text response out (non-streaming
        - stream:false - since every current and planned caller wants
        the complete answer, not a token stream).
        """
        payload = {"model": self._model, "prompt": prompt, "stream": False}
        if system is not None:
            payload["system"] = system

        response = self._post("/api/generate", payload)
        try:
            text = response["response"]
        except (KeyError, TypeError) as error:
            raise OllamaResponseError(f"Ollama's response had no 'response' field: {response!r}") from error
        if not isinstance(text, str):
            raise OllamaResponseError(f"Ollama's 'response' field was not text: {text!r}")
        return text

    def list_models(self) -> list[OllamaModel]:
        try:
            http_response = self._client.get("/api/tags")
        except httpx.RequestError as error:
            raise OllamaUnavailableError(f"Cannot reach Ollama at {self._base_url}: {error}") from error

        if http_response.status_code != 200:
            raise OllamaResponseError(f"Ollama returned HTTP {http_response.status_code} for /api/tags")

        try:
            body = http_response.json()
            models = body["models"]
        except (ValueError, KeyError, TypeError) as error:
            raise OllamaResponseError(f"Could not parse /api/tags response: {error}") from error

        return [OllamaModel(name=m["name"], size_bytes=m.get("size", 0)) for m in models]

    def is_available(self) -> bool:
        try:
            self.list_models()
            return True
        except OllamaError:
            return False

    def ensure_model_available(self) -> None:
        """Raises OllamaModelNotFoundError if the configured model has not been pulled."""
        available = [m.name for m in self.list_models()]
        # Ollama model names carry a tag (e.g. "llama3:latest"); a bare
        # "llama3" configured value should match "llama3:latest".
        if self._model in available or any(name.split(":")[0] == self._model for name in available):
            return
        raise OllamaModelNotFoundError(
            f"Model '{self._model}' is not available on this Ollama server. "
            f"Available: {available or '(none)'}. Pull it with: ollama pull {self._model}"
        )

    def _post(self, path: str, payload: dict) -> dict:
        try:
            http_response = self._client.post(path, json=payload)
        except httpx.RequestError as error:
            raise OllamaUnavailableError(f"Cannot reach Ollama at {self._base_url}: {error}") from error

        if http_response.status_code == 404:
            raise OllamaModelNotFoundError(f"Ollama returned 404 for {path} - is model '{self._model}' pulled?")
        if http_response.status_code != 200:
            raise OllamaResponseError(f"Ollama returned HTTP {http_response.status_code} for {path}: {http_response.text[:200]}")

        try:
            return http_response.json()
        except ValueError as error:
            raise OllamaResponseError(f"Ollama's response to {path} was not valid JSON") from error

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

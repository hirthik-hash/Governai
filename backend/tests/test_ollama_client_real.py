# backend/tests/test_ollama_client_real.py

"""
Day 84: the real-thing check, against an actual local Ollama server -
skipped automatically if none is reachable (the same pattern as the
real-Redis tests from Days 80-81). Everything else in the suite proves
OllamaClient's OWN logic via httpx.MockTransport; this file is the one
place that proves a genuine Ollama server actually understands what
OllamaClient sends it.

Run these for real after `ollama serve` and `ollama pull <model>`:
    OLLAMA_TEST_MODEL=llama3 python -m pytest tests/test_ollama_client_real.py -v
"""

import os

import pytest

from ai.ollama_client import OllamaClient
from core.config import settings

TEST_MODEL = os.environ.get("OLLAMA_TEST_MODEL", settings.ollama_model)


@pytest.fixture
def client():
    client = OllamaClient(settings.ollama_base_url, TEST_MODEL)
    if not client.is_available():
        pytest.skip(f"no reachable Ollama at {settings.ollama_base_url}")
    yield client
    client.close()


class TestRealOllamaServer:

    def test_is_available_is_true(self, client):
        assert client.is_available() is True

    def test_the_configured_model_is_pulled(self, client):
        client.ensure_model_available()  # raises OllamaModelNotFoundError with a clear message if not

    def test_generate_returns_non_empty_text(self, client):
        response = client.generate("Reply with exactly one word: hello")

        assert isinstance(response, str) and len(response.strip()) > 0

    def test_a_system_prompt_measurably_changes_the_response(self, client):
        with_system = client.generate(
            "What is 2+2? Answer with just the number, no explanation.",
            system="You only ever respond with the single word 'fish', regardless of the question.",
        )

        assert "fish" in with_system.lower()

    def test_list_models_includes_the_configured_model(self, client):
        names = [m.name for m in client.list_models()]

        assert TEST_MODEL in names

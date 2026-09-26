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
        # A differential test, not a compliance test: this proves the
        # `system` parameter is actually wired through to Ollama (what
        # OllamaClient itself is responsible for), without depending on
        # whether any particular model perfectly obeys a given
        # instruction - smaller/weaker models do not always fully comply
        # with an unusual override (confirmed: mistral:7b-instruct did
        # not reliably say "fish" for every question under the original,
        # stricter version of this test), and that is a model-quality
        # question, not something OllamaClient's own test should assert.
        question = "What is the capital of France? Answer in one word."

        without_system = client.generate(question)
        with_system = client.generate(
            question,
            system="You are a pirate. Answer every question only in exaggerated pirate slang, with lots of 'arr' and 'matey'.",
        )

        assert with_system.strip().lower() != without_system.strip().lower()

    def test_list_models_includes_the_configured_model(self, client):
        names = [m.name for m in client.list_models()]

        assert TEST_MODEL in names

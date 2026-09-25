# backend/tests/test_ollama_client.py

"""
Day 84: OllamaClient. Every test here exercises the REAL request-building
and response-parsing code via httpx.MockTransport - not a hand-rolled
fake standing in for the whole class - so a bug in how OllamaClient talks
to Ollama's actual REST API would be caught here, not just in the fakes.
"""

import httpx
import pytest

from ai.ollama_client import (
    OllamaClient, OllamaModelNotFoundError, OllamaResponseError, OllamaUnavailableError,
)

BASE_URL = "http://localhost:11434"


def _client(handler) -> OllamaClient:
    return OllamaClient(BASE_URL, "llama3", transport=httpx.MockTransport(handler))


class TestGenerate:

    def test_returns_the_response_text(self):
        def handler(request):
            assert request.url.path == "/api/generate"
            body = httpx.Request(request.method, request.url, content=request.content)
            return httpx.Response(200, json={"response": "42", "done": True})

        assert _client(handler).generate("What is the answer?") == "42"

    def test_sends_the_model_prompt_and_stream_false(self):
        captured = {}

        def handler(request):
            import json
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"response": "ok"})

        _client(handler).generate("hello")

        assert captured == {"model": "llama3", "prompt": "hello", "stream": False}

    def test_an_optional_system_prompt_is_included_when_given(self):
        captured = {}

        def handler(request):
            import json
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"response": "ok"})

        _client(handler).generate("hello", system="Be terse.")

        assert captured["system"] == "Be terse."

    def test_system_is_omitted_entirely_when_not_given(self):
        captured = {}

        def handler(request):
            import json
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"response": "ok"})

        _client(handler).generate("hello")

        assert "system" not in captured

    def test_missing_response_field_is_a_response_error(self):
        def handler(request):
            return httpx.Response(200, json={"done": True})

        with pytest.raises(OllamaResponseError):
            _client(handler).generate("hello")

    def test_non_string_response_field_is_a_response_error(self):
        def handler(request):
            return httpx.Response(200, json={"response": 12345})

        with pytest.raises(OllamaResponseError):
            _client(handler).generate("hello")

    def test_invalid_json_body_is_a_response_error(self):
        def handler(request):
            return httpx.Response(200, content=b"not json at all")

        with pytest.raises(OllamaResponseError):
            _client(handler).generate("hello")

    def test_a_non_200_status_is_a_response_error(self):
        def handler(request):
            return httpx.Response(500, text="internal error")

        with pytest.raises(OllamaResponseError):
            _client(handler).generate("hello")

    def test_a_404_is_reported_as_model_not_found(self):
        def handler(request):
            return httpx.Response(404, text="model not found")

        with pytest.raises(OllamaModelNotFoundError):
            _client(handler).generate("hello")

    def test_a_connection_failure_is_unavailable_not_a_generic_error(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        with pytest.raises(OllamaUnavailableError):
            _client(handler).generate("hello")

    def test_a_timeout_is_also_unavailable(self):
        def handler(request):
            raise httpx.TimeoutException("timed out")

        with pytest.raises(OllamaUnavailableError):
            _client(handler).generate("hello")


class TestListModels:

    def test_parses_the_real_tags_response_shape(self):
        def handler(request):
            assert request.url.path == "/api/tags"
            return httpx.Response(200, json={"models": [
                {"name": "llama3:latest", "size": 4700000000},
                {"name": "mistral:latest", "size": 4100000000},
            ]})

        models = _client(handler).list_models()

        assert [m.name for m in models] == ["llama3:latest", "mistral:latest"]
        assert models[0].size_bytes == 4700000000

    def test_empty_model_list(self):
        def handler(request):
            return httpx.Response(200, json={"models": []})

        assert _client(handler).list_models() == []

    def test_malformed_tags_response_is_a_response_error(self):
        def handler(request):
            return httpx.Response(200, json={"unexpected": "shape"})

        with pytest.raises(OllamaResponseError):
            _client(handler).list_models()

    def test_unreachable_server_is_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        with pytest.raises(OllamaUnavailableError):
            _client(handler).list_models()


class TestIsAvailable:

    def test_true_when_the_server_responds(self):
        def handler(request):
            return httpx.Response(200, json={"models": []})

        assert _client(handler).is_available() is True

    def test_false_when_unreachable(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        assert _client(handler).is_available() is False

    def test_false_on_a_malformed_response_too(self):
        def handler(request):
            return httpx.Response(200, json={"garbage": True})

        assert _client(handler).is_available() is False


class TestEnsureModelAvailable:

    def test_passes_when_the_exact_name_is_pulled(self):
        def handler(request):
            return httpx.Response(200, json={"models": [{"name": "llama3", "size": 1}]})

        _client(handler).ensure_model_available()  # must not raise

    def test_passes_when_a_tagged_variant_matches_the_bare_name(self):
        def handler(request):
            return httpx.Response(200, json={"models": [{"name": "llama3:latest", "size": 1}]})

        _client(handler).ensure_model_available()  # must not raise

    def test_raises_with_a_helpful_message_when_not_pulled(self):
        def handler(request):
            return httpx.Response(200, json={"models": [{"name": "mistral:latest", "size": 1}]})

        with pytest.raises(OllamaModelNotFoundError, match="ollama pull llama3"):
            _client(handler).ensure_model_available()


class TestContextManager:

    def test_can_be_used_as_a_context_manager(self):
        def handler(request):
            return httpx.Response(200, json={"response": "ok"})

        with _client(handler) as client:
            assert client.generate("hi") == "ok"

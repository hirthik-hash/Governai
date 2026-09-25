# backend/tests/test_fake_ollama_client.py

"""Day 84: the FakeOllamaClient / FakeOllamaClientSequence test doubles themselves."""

import pytest

from ai.fake_ollama_client import FakeOllamaClient, FakeOllamaClientSequence
from ai.ollama_client import OllamaError


class TestFakeOllamaClient:

    def test_returns_the_default_response(self):
        client = FakeOllamaClient(default_response="hello")

        assert client.generate("anything") == "hello"

    def test_matches_by_prompt_substring_before_falling_back_to_default(self):
        client = FakeOllamaClient(
            default_response="fallback",
            responses_by_prompt_substring={"weather": "sunny", "time": "noon"},
        )

        assert client.generate("what's the weather today?") == "sunny"
        assert client.generate("what time is it?") == "noon"
        assert client.generate("tell me a joke") == "fallback"

    def test_records_every_call_including_the_system_prompt(self):
        client = FakeOllamaClient(default_response="x")

        client.generate("prompt one")
        client.generate("prompt two", system="be terse")

        assert client.calls == [
            {"prompt": "prompt one", "system": None},
            {"prompt": "prompt two", "system": "be terse"},
        ]

    def test_is_available_is_always_true(self):
        assert FakeOllamaClient().is_available() is True

    def test_works_as_a_context_manager(self):
        with FakeOllamaClient(default_response="ok") as client:
            assert client.generate("x") == "ok"

    def test_ensure_model_available_never_raises(self):
        FakeOllamaClient().ensure_model_available()  # must not raise


class TestFakeOllamaClientSequence:

    def test_returns_each_scripted_response_in_order(self):
        client = FakeOllamaClientSequence(["first", "second", "third"])

        assert [client.generate("p") for _ in range(3)] == ["first", "second", "third"]

    def test_raises_when_exhausted_rather_than_silently_repeating_or_defaulting(self):
        client = FakeOllamaClientSequence(["only one"])
        client.generate("p")

        with pytest.raises(OllamaError, match="exhausted"):
            client.generate("p")

    def test_a_scripted_exception_is_raised_at_its_turn(self):
        boom = OllamaError("simulated failure")
        client = FakeOllamaClientSequence(["first", boom, "third"])

        assert client.generate("p") == "first"
        with pytest.raises(OllamaError, match="simulated failure"):
            client.generate("p")
        assert client.generate("p") == "third"

    def test_records_calls_the_same_way_as_the_plain_fake(self):
        client = FakeOllamaClientSequence(["a"])

        client.generate("hello", system="sys")

        assert client.calls == [{"prompt": "hello", "system": "sys"}]

    def test_empty_sequence_raises_on_the_first_call(self):
        with pytest.raises(OllamaError, match="exhausted"):
            FakeOllamaClientSequence([]).generate("p")

"""Unit tests for the LiteLLM facade.

The actual `litellm.completion` call is mocked — these tests pin our adapter
behaviour (arg shaping, response unpacking, latency measurement) without
hitting the network or instantiating provider clients.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import llm_service


def _fake_response(content: str, tin: int | None, tout: int | None) -> SimpleNamespace:
    """Mimic LiteLLM's ModelResponse — just the attrs we read."""
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    usage = SimpleNamespace(prompt_tokens=tin, completion_tokens=tout)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_passes_model_and_messages_through_to_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return _fake_response("hi", 4, 2)

    monkeypatch.setattr(llm_service.litellm, "completion", fake)

    llm_service.chat_completion(
        model="groq/llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
        max_tokens=300,
        timeout=10.0,
    )

    assert seen["model"] == "groq/llama-3.3-70b-versatile"
    assert seen["messages"] == [{"role": "user", "content": "hi"}]
    assert seen["temperature"] == 0.2
    assert seen["max_tokens"] == 300
    assert seen["timeout"] == 10.0


def test_extracts_text_and_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_service.litellm, "completion",
        lambda **_: _fake_response("answer text", 12, 5),
    )

    result = llm_service.chat_completion(model="x/y", messages=[])

    assert result.text == "answer text"
    assert result.tokens_in == 12
    assert result.tokens_out == 5
    assert result.model == "x/y"


def test_missing_usage_falls_back_to_zero_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some providers (or streaming responses without `include_usage`) return
    a ModelResponse with `usage=None`. Must not crash; defaults to 0."""
    choice = SimpleNamespace(message=SimpleNamespace(content="x"))
    no_usage = SimpleNamespace(choices=[choice], usage=None)
    monkeypatch.setattr(llm_service.litellm, "completion", lambda **_: no_usage)

    result = llm_service.chat_completion(model="x/y", messages=[])

    assert result.tokens_in == 0
    assert result.tokens_out == 0


def test_null_content_becomes_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """`choices[0].message.content` is `None` for tool-call-only responses.
    Adapter coerces to empty string so callers never see a None text."""
    choice = SimpleNamespace(message=SimpleNamespace(content=None))
    resp = SimpleNamespace(choices=[choice], usage=None)
    monkeypatch.setattr(llm_service.litellm, "completion", lambda **_: resp)

    result = llm_service.chat_completion(model="x/y", messages=[])

    assert result.text == ""


def test_records_call_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    import time as _time

    def slow_call(**_: Any) -> Any:
        _time.sleep(0.02)  # 20 ms minimum
        return _fake_response("x", 1, 1)

    monkeypatch.setattr(llm_service.litellm, "completion", slow_call)

    result = llm_service.chat_completion(model="x/y", messages=[])

    assert result.latency_ms >= 20.0


def test_ensure_provider_env_mirrors_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pristine env for this assertion — drop the keys we're about to inject.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    monkeypatch.setattr(llm_service.settings, "groq_api_key", "gsk_from_settings")
    monkeypatch.setattr(llm_service.settings, "openai_api_key", "sk_from_settings")

    llm_service._ensure_provider_env()

    assert os.environ["GROQ_API_KEY"] == "gsk_from_settings"
    assert os.environ["OPENAI_API_KEY"] == "sk_from_settings"


def test_ensure_provider_env_does_not_overwrite_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    # A host-exported env var must win over our mirrored settings — that's the
    # contract the docker-compose pass-through depends on.
    monkeypatch.setenv("GROQ_API_KEY", "host_set_value")
    monkeypatch.setattr(llm_service.settings, "groq_api_key", "settings_value")

    llm_service._ensure_provider_env()

    assert os.environ["GROQ_API_KEY"] == "host_set_value"

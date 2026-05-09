"""Unit tests for token-cost computation."""

from __future__ import annotations

import pytest

from app.services.cost import compute_cost_usd, has_known_rate


def test_known_model_cost() -> None:
    # gpt-4o-mini: $0.15 / 1M input, $0.60 / 1M output
    cost = compute_cost_usd("gpt-4o-mini", tokens_in=1_000_000, tokens_out=1_000_000)
    assert cost == pytest.approx(0.75, rel=1e-9)


def test_unknown_model_returns_zero() -> None:
    cost = compute_cost_usd("unknown-llm-9000", tokens_in=1000, tokens_out=1000)
    assert cost == 0.0


def test_zero_tokens_returns_zero_cost() -> None:
    assert compute_cost_usd("gpt-4o", tokens_in=0, tokens_out=0) == 0.0


def test_groq_models_are_zero_during_sprint() -> None:
    cost = compute_cost_usd("groq/llama-3.1-70b-versatile", tokens_in=10_000, tokens_out=10_000)
    assert cost == 0.0


def test_has_known_rate() -> None:
    assert has_known_rate("gpt-4o-mini") is True
    assert has_known_rate("unknown-model") is False
    # Groq models are in the catalog but mapped to ZERO_RATE → not "known" for billing
    assert has_known_rate("groq/llama-3.1-8b-instant") is False

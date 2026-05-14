"""Token-count → USD cost conversion.

Rates current as of 2026-Q2; published per-model. Update when providers change pricing.
USD only at the storage layer; the dashboard converts to ₹ at display time using a fresh FX rate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRate:
    """Per-million-token pricing in USD."""

    input_per_mtok: float
    output_per_mtok: float


# Default catalog. Unknown models fall back to ZERO_RATE (cost not computable).
RATES: dict[str, ModelRate] = {
    # OpenAI
    "gpt-4o": ModelRate(input_per_mtok=2.50, output_per_mtok=10.00),
    "gpt-4o-mini": ModelRate(input_per_mtok=0.15, output_per_mtok=0.60),
    "gpt-4-turbo": ModelRate(input_per_mtok=10.00, output_per_mtok=30.00),
    # Anthropic
    "claude-opus-4-7": ModelRate(input_per_mtok=15.00, output_per_mtok=75.00),
    "claude-sonnet-4-6": ModelRate(input_per_mtok=3.00, output_per_mtok=15.00),
    "claude-haiku-4-5": ModelRate(input_per_mtok=1.00, output_per_mtok=5.00),
    # Groq (free tier during sprint — modeled as zero so it doesn't pollute cost charts).
    # Groq decommissions models aggressively; verify against `/v1/models` at the
    # start of each milestone (last verified 2026-05-09).
    "groq/llama-3.3-70b-versatile": ModelRate(input_per_mtok=0.0, output_per_mtok=0.0),
    "groq/llama-3.1-8b-instant": ModelRate(input_per_mtok=0.0, output_per_mtok=0.0),
}

ZERO_RATE = ModelRate(input_per_mtok=0.0, output_per_mtok=0.0)


def compute_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return cost in USD for a given (model, tokens_in, tokens_out) tuple.

    Unknown models return 0.0 (caller should log a warning when this happens to surface
    pricing-table gaps in the dashboard).
    """
    rate = RATES.get(model, ZERO_RATE)
    return (
        tokens_in * rate.input_per_mtok / 1_000_000.0
        + tokens_out * rate.output_per_mtok / 1_000_000.0
    )


def has_known_rate(model: str) -> bool:
    """True if `model` has a non-zero published rate in our catalog."""
    rate = RATES.get(model)
    if rate is None:
        return False
    return rate.input_per_mtok > 0 or rate.output_per_mtok > 0

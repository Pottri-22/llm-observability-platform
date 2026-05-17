"""Provider-agnostic chat completion via LiteLLM.

One entry point (`chat_completion`) the whole backend uses to talk to *any* LLM
provider — Groq, OpenAI, Anthropic, Ollama, etc. The Judge calls this; future
RAGAS / regression / drift evaluators will call it; LiteLLM picks the provider
from the model-string prefix (`groq/llama-3.3-70b-versatile`, `anthropic/...`).

Swapping the judge from Groq to Anthropic is one config line — no code change.
That's the whole point of putting this layer in: the eval engine isn't
hard-coded to a vendor.

LiteLLM is chatty by default (per-call DEBUG logs, an import-time phone-home).
Both are silenced at module load — server libraries shouldn't make noise.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import litellm

from app.config import settings

log = logging.getLogger(__name__)

# Quiet the library defaults. Three layers, each silences something different:
#   * `suppress_debug_info` — drops the stdout banner LiteLLM prints with the
#     request body / response preview.
#   * `telemetry = False` — stops the hosted-endpoint usage ping.
#   * Logger level WARNING — silences the per-call INFO line that goes through
#     Python's logging ("Wrapper: Completed Call, calling success_handler").
litellm.suppress_debug_info = True
litellm.telemetry = False
logging.getLogger("LiteLLM").setLevel(logging.WARNING)


@dataclass(frozen=True)
class CompletionResult:
    """One chat completion response, distilled to the four things any caller
    needs. Provider-shape details (LiteLLM's `ModelResponse` object) stay inside
    this module."""

    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: float


def _ensure_provider_env() -> None:
    """LiteLLM looks up API keys in `os.environ`. Mirror our `settings` values
    so a worker process picks them up even though it never ran FastAPI's
    lifespan. We don't overwrite — if the host already exported the key
    (docker-compose pass-through does), that wins."""
    for env_key, value in (
        ("GROQ_API_KEY", settings.groq_api_key),
        ("OPENAI_API_KEY", settings.openai_api_key),
        ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
    ):
        if value and not os.environ.get(env_key):
            os.environ[env_key] = value


def chat_completion(
    *,
    model: str,
    messages: Iterable[dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: float = 15.0,
) -> CompletionResult:
    """One LLM call. Raises `litellm` exceptions on failure — callers decide
    whether to retry.

    `model` is a LiteLLM provider-prefixed string. The list of supported
    prefixes is documented at https://docs.litellm.ai/docs/providers; the
    common ones for Aegis are `groq/`, `openai/`, `anthropic/`, `ollama/`.
    """
    _ensure_provider_env()
    t0 = time.perf_counter()
    response = litellm.completion(
        model=model,
        messages=list(messages),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    text = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)
    return CompletionResult(
        text=text,
        model=model,
        tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
        tokens_out=getattr(usage, "completion_tokens", 0) or 0,
        latency_ms=elapsed_ms,
    )

# Week 0 · Block 2 — LiteLLM unified gateway probe

**As of:** 2026-05-09 (Sat) · pre-Week 1 · Block 2 complete
**Pairs with:** [litellm_sandbox.py](litellm_sandbox.py), [hello_llm.py](hello_llm.py), [docs/v0.1-walkthrough.md §7](../docs/v0.1-walkthrough.md)
**Reading goal:** so v0.2's LiteLLM gateway build doesn't re-learn any of these.

---

## 1. What ran

`litellm.completion(...)` against the same prompt across 5 probes, three runs to surface cold-vs-warm:

| Provider · Model | Run 1 | Run 2 | Run 3 | Tokens (in→out) |
|---|---:|---:|---:|---:|
| Groq · `llama-3.3-70b-versatile` | 319 ms | 386 ms | 401 ms | 48 → 57–65 |
| Groq · `llama-3.1-8b-instant`    | 207 ms | 166 ms | 247 ms | 48 → 51–55 |
| Ollama · `llama3.2:3b` (local)   | **16 652 ms** | 10 635 ms | 10 298 ms | 38 → 39–54 |
| OpenAI · `gpt-4o-mini`           | SKIPPED — `OPENAI_API_KEY` unset | | | |
| Anthropic · `claude-haiku-4-5-20251001` | SKIPPED — `ANTHROPIC_API_KEY` unset | | | |

3/5 reachable; abstraction proven across one OpenAI-compat hosted provider (Groq) and one local provider (Ollama). OpenAI/Anthropic stay deferred until paid keys exist; the script handles them as `SKIPPED`, not crash.

---

## 2. Surprises (the actual learning)

### 2.1 Local models are 25-80× slower than Groq, even after warmup
- Cold start: 16 652 ms (model loading into RAM is ~6 s of that)
- Warm steady state: ~10 300 ms — still 25-50× slower than Groq's 200-400 ms
- **Why this matters:** Aegis cannot ship one global `timeout=30s` and call it done. v0.2 needs per-provider timeouts. Suggested defaults: Groq 15 s, OpenAI 30 s, Anthropic 60 s, Ollama 120 s.
- **Defend:** "Why not just use a generous global timeout?" → because user-facing requests should fail fast on a wedged Groq, but a slow local model is *expected* to take a minute. One number can't serve both.

### 2.2 Tokenizers disagree across providers — same prompt, different counts
- Groq counts the prompt as **48 tokens**; Ollama counts the *same string* as **38 tokens**.
- Llama 3.1/3.3 (Groq) and Llama 3.2 (Ollama) both use BPE-family tokenizers, but the chat-template framing the server applies before counting differs.
- **Why this matters:** never re-tokenize a prompt with a different model to estimate cost. The source provider's `usage.prompt_tokens` is the only correct value. If Aegis ever adds a "predict cost before sending" feature, it must use a tokenizer that matches the *target* model.
- **Defend:** "Why does the same trace show different token counts across providers?" → tokenizers are per-model, and the chat template is part of what gets tokenized. This is normal; never reconcile.

### 2.3 LLM responses are non-deterministic by default
- Groq 70B in 3 runs: 60, 57, 65 output tokens. Latency 319, 386, 401 ms.
- That's ±15% on tokens and ±25% on latency at default temperature.
- **Why this matters:** v0.2's eval engine cannot compare a single trace to a single baseline trace and call drift. We need either `temperature=0` for baseline runs, or N-sample averaging.
- **Defend:** "How does drift detection avoid false positives from sampling noise?" → eval baselines run with `temperature=0` and `seed` pinned; production traces get score-averaged over a rolling window before comparison.

### 2.4 The `ollama_chat/` vs `ollama/` prefix is a real trap
- LiteLLM's `ollama/<model>` routes to Ollama's `/api/generate`, which returns `prompt_eval_count=0` for chat-shaped messages — token counts are wrong.
- LiteLLM's `ollama_chat/<model>` routes to `/api/chat`, which returns proper `prompt_eval_count` + `eval_count`.
- The Aegis sandbox uses `ollama_chat/` for that reason. **Lock this in for v0.2's catalog**: any Ollama model entry must be keyed `ollama_chat/...`, never `ollama/...`.
- **Defend:** "Why two prefixes for Ollama?" → it predates Ollama's `/api/chat` endpoint; LiteLLM kept the old prefix for back-compat. New code should always use `ollama_chat/`.

### 2.5 `has_known_rate` can't tell "intentionally free" from "unknown"
- `cost.py::has_known_rate("groq/llama-3.3-70b-versatile")` → `False` (rate is 0/0)
- `cost.py::has_known_rate("ollama/llama3.2:3b")` → `False` (model not in catalog at all)
- Both look identical to the dashboard, but the first is "this model is genuinely free, $0 is correct" and the second is "we don't have pricing for this, $0 is a lie."
- **v0.2 work item:** add a third state. `ModelRate(0.0, 0.0, free_tier=True)` or a separate `KNOWN_FREE` set. The cost-charts UI should annotate free-tier traces differently from unknown-rate traces.

### 2.6 Anthropic catalog-key mismatch
- LiteLLM expects the dated id: `anthropic/claude-haiku-4-5-20251001`
- `cost.py` stores the short id: `claude-haiku-4-5`
- The sandbox's `Probe.catalog_key` field bridges this manually. v0.2's gateway needs a normalization helper: strip provider prefix, strip date suffix, look up. Centralize so we don't sprinkle `if "anthropic" in model: ...` across the codebase.

### 2.7 Groq `/v1/models` 403s via raw urllib but works via OpenAI SDK
- `urllib.request.urlopen()` with `Authorization: Bearer ...` → HTTP 403 Forbidden
- Same key + same endpoint via `OpenAI(api_key=...).models.list()` → 200 OK
- Likely a Groq WAF rule that blocks the default `User-Agent: Python-urllib/3.12`.
- **For the milestone-start preflight (per Groq-deprecation memory):** always use the OpenAI SDK's `client.models.list()`, never raw urllib. [_preflight.py](_preflight.py) currently does it the wrong way and shows 403; fix during v0.2.

---

## 3. v0.2 work items that fell out of this block

Cross-reference these against [README §12](../README.md#12-roadmap-versioned-shipping) when v0.2 starts:

1. **Per-provider timeout config** — add `LLMProviderTimeouts` to `app/config.py` (Groq=15, OpenAI=30, Anthropic=60, Ollama=120).
2. **Provider-aware cost-key normalizer** — `app/services/cost.py::normalize_model_key("anthropic/claude-haiku-4-5-20251001")` → `"claude-haiku-4-5"`. Strip provider prefix; strip ISO-date suffix.
3. **`KNOWN_FREE_TIER` set in `cost.py`** — separate "free" from "unknown" so dashboard cost charts read honestly.
4. **Lock `ollama_chat/` in catalog** — when a user adds an Ollama model, the form must rewrite `ollama/foo` → `ollama_chat/foo` before persisting.
5. **Fix `_preflight.py` to use the OpenAI SDK** — replace `urllib.request` with `OpenAI(...).models.list()` so the 403 goes away.
6. **Per-trace tokenizer note in dashboard** — when displaying cross-provider cost comparisons, surface a tooltip: "Token counts come from the source provider; not directly comparable."
7. **Eval determinism contract** — the eval runner sets `temperature=0` and (where supported) `seed=<fixed>`; document in `app/services/evals/README.md` when that lands.
8. **Catalog candidate to add:** Groq's `meta-llama/llama-4-scout-17b-16e-instruct` showed up in `/v1/models`. Worth benchmarking in v0.3.

---

## 4. What I should be able to defend

If interview-prep on this block were five minutes at a whiteboard, these are the questions I should answer without rereading code:

1. **"Why does Aegis use LiteLLM and not call each provider's SDK directly?"** → one signature, graceful fallback when a provider is missing, automatic model-name routing. The cost is one extra dependency and a learning curve on prefix conventions.
2. **"How does the gateway handle a wedged Ollama daemon?"** → per-provider timeout (120 s for Ollama), the call raises `APIConnectionError`, the SDK catches it, marks the trace status as `error`, still ingests it (so the dashboard shows the failure). Ingestion never crashes because of a wedged upstream.
3. **"Why not normalize tokens across providers?"** → tokenizers are per-model; re-tokenizing produces a *different* count and quietly breaks cost. Source provider's count is the only truth.
4. **"What's the difference between `ollama/` and `ollama_chat/` and which does Aegis use?"** → see §2.4. Chat. Always.

---

## 5. What I'm not doing in this block

- **Not adding LiteLLM to backend deps yet.** That happens in v0.2 when the gateway lands in `app/services/`. Today it lives only in `week0/.venv/`.
- **Not wiring the sandbox into the Aegis ingest path.** The sandbox imports `cost.py` to dogfood the catalog, but it does *not* POST traces to `/v1/traces`. Wiring is a v0.2 item.
- **Not testing OpenAI or Anthropic.** Deferred until paid keys exist. The probe table is structured so that the day a key arrives, only `.env` changes — no code does.

---

**Last verified:** 2026-05-09 · 3 of 5 probes responding · script + notes committed to `week0/`.

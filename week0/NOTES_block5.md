# Week 0 · Block 5 — embedding centroid drift (all-MiniLM-L6-v2)

**As of:** 2026-05-14 (Wed) · pre-Week 1 · Block 5 complete
**Pairs with:** [embedding_drift_sandbox.py](embedding_drift_sandbox.py)
**Reading goal:** so v0.3's Drift Detector (README §6.6) doesn't re-derive the
centroid-cosine math or re-discover that you need a control batch to set a
threshold.

---

## 1. What was built

[embedding_drift_sandbox.py](embedding_drift_sandbox.py) — embeds three
100-prompt batches with `all-MiniLM-L6-v2` and shows that **centroid cosine
distance** separates "same prompt mix" from "topic spike":

```
sample 3 batches → load model → embed → per-batch centroid →
cosine_distance(new_centroid, baseline_centroid) → compare to threshold
```

The three batches mirror the README's "RuPay-on-UPI Diwali drift" narrative
(line 438):

| Batch | Topic mix | Purpose |
|---|---|---|
| BASELINE | normal fintech-support spread, `rupay_upi` = 8% | the frozen reference |
| CONTROL | **same** weights as baseline, different random draw | measures the noise floor |
| DRIFTED | `rupay_upi` spiked 8% → 55% | the event the detector must catch |

**Measured result (seed=42, CPU dev box):**

| Metric | Result |
|---|---|
| Model load, first run (download + init) | ~23 s |
| Embed 300 prompts | 944 ms → **~318 prompts/sec, CPU** |
| Embedding dim | 384 |
| drift(CONTROL vs BASELINE) — noise floor | **0.0347** |
| drift(DRIFTED vs BASELINE) — real shift | **0.2367** |
| ratio | **6.8×** above the noise floor |

A 55% topic spike moved the centroid 0.24 cosine. README's narrative quotes
+0.34 — same order of magnitude; the exact number depends on how concentrated
the spike is and how distinct the topic's embeddings are.

---

## 2. The math, and why it works

### 2.1 Centroid = mean vector of the batch

Embed every prompt (each row a 384-d unit vector with
`normalize_embeddings=True`), then take the column-wise mean. One batch → one
vector. Drift detection is then a single `O(1)` comparison of two centroids,
after `O(n)` embedding. Cheap enough to run nightly on a Celery Beat job.

### 2.2 Cosine distance, not Euclidean

`cosine_distance = 1 - cos(θ)`. Cosine is **magnitude-invariant** — it only
looks at direction. That is exactly why comparing centroids works even though
**the centroid of unit vectors is itself NOT a unit vector**:

```
‖centroid(BASELINE)‖ = 0.4572
‖centroid(DRIFTED )‖ = 0.5901   ← longer
```

The centroid's *length* shrinks as the batch spreads out and grows as it
tightens. DRIFTED's centroid is longer because one topic dominates → vectors
point more the same way → their mean is less self-cancelling. Cosine distance
ignores this length difference and measures only the *direction* shift, which
is the thing we actually mean by "drift." (If you ever used Euclidean distance
on raw centroids instead, the length difference would contaminate the score.)

The centroid norm is a *free second signal* — batch tightness — that v0.3
could surface alongside drift_score.

### 2.3 You cannot pick a threshold without a control batch

This is the real lesson. Two different random samples of the *same*
distribution do not produce identical centroids — there's a noise floor
(here, 0.0347). A threshold has to sit **above the noise floor and below a
real shift**. Without the CONTROL batch you'd be guessing; with it you can say
"normal night-to-night variation is ~0.03, so alert at ~0.10–0.13." v0.3
should calibrate on *several* control batches, not one — the midpoint pick in
the script is deliberately naive.

---

## 3. Gotchas hit live during this block

### 3.1 First model load is slow (~23 s); cached loads are fast

First run downloads ~80 MB from HuggingFace into
`%USERPROFILE%\.cache\huggingface\hub` **and** pays torch's import/init cost.
Subsequent runs skip the download (cache hit) and are far quicker. v0.3's
worker container should **bake the model into the image** so the first job
isn't a 23 s cold start — don't download at runtime.

### 3.2 HF symlink warning on Windows (cosmetic, not a bug)

```
UserWarning: huggingface_hub cache-system uses symlinks ... your machine
does not support them ...
```

Windows without Developer Mode / admin can't make symlinks, so the HF cache
falls back to copies — uses a bit more disk, works fine. Silence with
`HF_HUB_DISABLE_SYMLINKS_WARNING=1` if it's noisy; not worth enabling
Developer Mode for a sandbox. Related Windows quirks: the cp1252 console fix
is already baked into the script (see [[windows-console-cp1252-encoding]]).

### 3.3 `get_sentence_embedding_dimension` was renamed in sentence-transformers 5.x

5.x emits `FutureWarning: ... renamed to get_embedding_dimension`. The script
now does `getattr(model, "get_embedding_dimension", None) or
model.get_sentence_embedding_dimension` so it works on both. Same lesson as the
Groq deprecation cadence — pinned ML deps move fast; check method names at the
start of v0.3.

### 3.4 `normalize_embeddings=True` matters

Without it, `model.encode` returns raw (non-unit) vectors and per-prompt
magnitudes vary. Normalizing first keeps the per-prompt vectors comparable and
makes the centroid a clean "average direction." Cosine distance would still be
*magnitude-invariant* either way, but normalized inputs make the centroid norm
(§2.2) interpretable as a tightness signal.

---

## 4. v0.3 Drift Detector work items that fell out of this block

Cross-reference [README §6.6](../README.md) when v0.3 build starts
(`backend/app/evaluators/drift.py`):

1. **Bake the model into the worker image** — no runtime download; §3.1.
2. **Calibrate the threshold on multiple control batches**, not one. Store the
   noise-floor stats so the threshold is data-driven, not a magic constant.
3. **Freeze + version the baseline centroid.** The baseline is "what we tested
   against" — it must be a stored artifact (ClickHouse `drift snapshots`,
   README §6.6/line 218), not recomputed each night, or drift can never be
   detected (the baseline would chase the drift).
4. **Emit `drift_score` as a metric** + Discord webhook on threshold cross —
   that's the README §6.6 contract.
5. **Pin sentence-transformers and re-check the API** at v0.3 start; §3.3.
6. **Decide the batch window.** README says "last 24 h of prompts." If a day
   has very few prompts the centroid is noisy — v0.3 needs a minimum-N guard
   before it trusts a drift score.
7. **Centroid norm as a secondary signal** — surface batch tightness alongside
   drift_score; a tightening batch (§2.2) is itself interesting.

---

## 5. What I should be able to defend

1. **"What is embedding drift and how do you measure it?"** → embed each
   prompt with a sentence model, average to a centroid, compare today's
   centroid to a frozen baseline centroid via cosine distance. One number per
   night; alert when it exceeds a calibrated threshold.

2. **"Why cosine distance and not Euclidean?"** → cosine is magnitude-
   invariant. The centroid of unit vectors is not unit-length — its length
   encodes batch *tightness*, which we don't want polluting the *direction*
   shift we call drift. Euclidean would mix the two.

3. **"Why do you need a control batch?"** → to find the noise floor. Two
   random samples of the same distribution still differ (~0.03 here). The
   threshold must sit above that floor; without a control batch you're
   guessing.

4. **"Why `all-MiniLM-L6-v2` and not a bigger model?"** → 384-d, ~80 MB, runs
   on CPU at ~300 prompts/sec — free and fast enough for a nightly job. Drift
   detection needs *relative* distances, not state-of-the-art absolute
   semantic quality, so a small model is the right trade.

5. **"What breaks if the baseline isn't frozen?"** → if you recompute the
   baseline from recent data every night, it chases the drift and the distance
   stays near the noise floor forever — you'd never detect anything. The
   baseline must be a stored, versioned artifact.

---

## 6. What this block intentionally does NOT do

- **Does not wire to ClickHouse or Celery.** No `drift snapshots` table, no
  Beat schedule. Batches are synthetic and in-memory. That's v0.3.
- **Does not use real prompts.** Prompts are template-sampled from a fixed
  topic distribution with a seed. Real drift detection runs on the actual
  last-24h prompt stream.
- **Does not calibrate the threshold properly.** Uses a single control batch
  and a midpoint guess. v0.3 needs multiple control batches and stored
  noise-floor stats (§4 item 2).
- **Does not handle per-topic / cluster drift.** A single centroid can miss a
  case where two topics shift in opposite directions and cancel out. v0.3
  could cluster first, then track per-cluster centroids — out of scope here.
- **Does not detect *quality* drift** — only *distribution* drift of the
  prompts. Whether answers got worse is the Regression Detector (§6.7) and the
  eval engine, not this.

---

**Last verified:** 2026-05-14 · `embedding_drift_sandbox.py` runs green, no
warnings · CONTROL drift 0.035 vs DRIFTED drift 0.237 (6.8× separation) ·
script + notes to be committed to `week0/`.

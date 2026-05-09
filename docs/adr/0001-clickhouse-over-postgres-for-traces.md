# ADR-0001: ClickHouse over Postgres for trace storage

**Date:** 2026-05-09
**Status:** Accepted
**Decision-maker:** Pottri Selvan R (solo)

## Context

Aegis ingests LLM traces — every prompt, completion, token count, cost, and latency for every LLM call across customer applications. Production volume is projected at 100 traces/sec sustained, with bursts to 500/sec. Each trace is ~2-5 KB. Over a year, a single tenant could generate billions of rows.

The dashboard requires fast analytic queries:

- "p99 latency by model over the last 24 h"
- "cost breakdown by project for last 30 days"
- "filter traces by `metadata.user_tier` where `tokens_in > 1000`"

We need a store that handles 100+ writes/sec sustained AND p99 < 200 ms on aggregations over 100M+ rows.

## Decision

Use **ClickHouse** as the primary trace store. Postgres remains the metadata DB for orgs, projects, API keys, prompts, and audit log.

ClickHouse table:

```sql
CREATE TABLE traces (
    trace_id String,
    org_id String,
    project_id String,
    ts DateTime64(3),
    model String,
    prompt String,
    completion String,
    tokens_in UInt32,
    tokens_out UInt32,
    cost_usd Float64,
    latency_ms UInt32,
    metadata String,
    inserted_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(ts)
ORDER BY (project_id, ts, trace_id);
```

## Alternatives considered

1. **Postgres + JSONB** — initial prototype hit ~50 traces/sec ingest before WAL contention; aggregation queries on `metadata->>'flow'` over 1 M rows took 2-3 s. Would not scale to year-1 volume.
2. **TimescaleDB** — Postgres extension; better than vanilla but still row-based. Hypothesis: ~150 traces/sec. Acceptable for v0.1, not for year-1.
3. **OpenSearch / Elasticsearch** — search-shaped, not analytics-shaped. p99 on aggregations comparable to ClickHouse but write-amplification + JVM overhead made the operational story worse.
4. **DuckDB** — embedded; great for batch but not concurrent writes. Out.

## Consequences

**Positive:**

- Sustains 500+ traces/sec on 2 vCPU (target verified by k6 in v0.3)
- Sub-200 ms p99 on filtered aggregations over 100 M rows
- Columnar compression — ~10× storage savings vs Postgres for trace text

**Negative:**

- Adds operational complexity — second DB to back up, monitor, version-upgrade
- Eventual consistency for some reads (acceptable for our workload)
- Joins between Postgres metadata and ClickHouse traces happen in app code, not in a single SQL statement

**Mitigation:** Postgres holds the source-of-truth metadata; ClickHouse holds append-only traces. No bi-directional consistency required.

## References

- ClickHouse `MergeTree` docs: https://clickhouse.com/docs/en/engines/table-engines/mergetree-family/mergetree
- TimescaleDB benchmark: `tests/load/k6_ingest.js` (added in v0.3)

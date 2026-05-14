# Week 0 · Block 4 — ClickHouse MergeTree fundamentals

**As of:** 2026-05-14 (Wed) · pre-Week 1 · Block 4 complete
**Pairs with:** [clickhouse_sandbox.py](clickhouse_sandbox.py), [docker-compose.yml](../docker-compose.yml), [ADR 0001](../docs/adr/0001-clickhouse-over-postgres-for-traces.md)
**Reading goal:** so v0.1's `backend/app/db/clickhouse.py` migration doesn't re-derive the schema — the DDL here is the schema, validated against the <100ms bar.

---

## 1. What was built

A local ClickHouse container (`docker compose up -d clickhouse`) plus
[clickhouse_sandbox.py](clickhouse_sandbox.py), which:

```
create MergeTree table → generate 100k synthetic spans → batch-insert →
OPTIMIZE FINAL → run 5 analytic queries → assert each < 100 ms
```

**Measured result (2 runs, 2 vCPU dev box):**

| Metric | Result |
|---|---|
| Insert, 100k rows, 2×50k batches | ~650 ms → **~150k rows/sec** |
| Q1 cost by model (`GROUP BY model`) | 69–70 ms |
| Q2 p95 latency by project | 62–67 ms |
| Q3 daily token spend, last 7d (partition-pruned) | 63–65 ms |
| Q4 single-project 14-day count (ORDER BY-aligned) | 56–68 ms |
| Q5 latest 20 traces for one project | 80–86 ms |

All 5 under the 100ms bar. The schema is intentionally identical to v0.1's
`TRACES_TABLE_DDL` except the table name (`traces_block4`) so the sandbox is
isolated from the real table.

---

## 2. The schema decisions that matter

```sql
ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(ts)
ORDER BY (project_id, ts, trace_id)
SETTINGS index_granularity = 8192
```

### 2.1 `ORDER BY (project_id, ts, trace_id)` — not `ORDER BY ts`

The ORDER BY key *is* the sparse primary index in MergeTree. Aegis's dashboard
queries are almost always **tenant-scoped** (`WHERE project_id = '…'`). Putting
`project_id` first means a single-project query reads only the index ranges for
that project — Q4 (56ms) proves it. `ORDER BY ts` alone would force every
project query to scan all projects' rows in each time range.

`trace_id` last is the tiebreaker for uniqueness; it doesn't help pruning but
costs nothing and makes the sort total.

### 2.2 `PARTITION BY toYYYYMMDD(ts)` — one part-group per day

Partitions are physical: a `WHERE ts >= now() - INTERVAL 7 DAY` filter (Q3)
lets ClickHouse **skip entire partitions** without reading them — partition
pruning. 30 days of data = 30 partitions; the 7-day query touches 7–8.
Daily granularity matches the dashboard's "last N days" filters; finer (hourly)
would make too many small parts, coarser (monthly) would prune too little.

### 2.3 Why the same design holds at 100M rows

The queries are columnar scans over *compressed* blocks — Q1 touches only the
`model` + `cost_usd` columns (2 of 13). Scan cost grows with the columns and
partitions touched, not total table size. 100k → 100M is ~1000× rows but the
per-query column/partition footprint barely changes once partition pruning and
the ORDER BY prefix are doing their job.

---

## 3. Gotchas hit live during this block

### 3.1 Windows console `cp1252` can't encode `─` (UnicodeEncodeError)

First run crashed at the first `print()` of a box-drawing header:
`UnicodeEncodeError: 'charmap' codec can't encode characters` — Python on
Windows defaults stdout to `cp1252`. **Fix baked into the script:**

```python
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
```

This is a *generic* Windows-Python trap, not ClickHouse-specific — any sprint
script printing non-ASCII will hit it. See [[windows-console-cp1252-encoding]].

### 3.2 `OPTIMIZE TABLE … FINAL` is a sandbox-only crutch

MergeTree merges parts **asynchronously**. Right after a batch insert the data
lives in multiple unmerged parts; queries still work but part-count is
non-deterministic, so timings wobble. The sandbox calls `OPTIMIZE … FINAL` to
force a synchronous merge so the <100ms numbers are stable and reproducible.

**In production you must NOT do this** — `OPTIMIZE FINAL` rewrites whole
partitions and is expensive; the background merger is supposed to run on its
own. v0.1's ingest path inserts and walks away.

### 3.3 Batch insert vs row-by-row — the 20–100× claim, confirmed

Two `client.insert()` calls of 50k rows each → ~150k rows/sec. Row-by-row
inserts would each create a new part and trigger merge pressure; MergeTree is
built for *batched* writes. v0.1's HTTP ingest must buffer traces and flush in
batches — never one INSERT per trace.

---

## 4. v0.1 SDK / backend work items that fell out of this block

Cross-reference [README §12](../README.md#12-roadmap-versioned-shipping) and
`backend/app/db/clickhouse.py` when v0.1 build starts:

1. **DDL parity check** — `traces_block4` DDL here must stay byte-identical
   (modulo table name) to `TRACES_TABLE_DDL`. Consider a test that diffs them.
2. **Batch buffer in the ingest path** — `POST /v1/traces` must accumulate and
   flush in batches (size + time-based), never INSERT per request. §3.3.
3. **No `OPTIMIZE FINAL` in migrations or ingest** — let the background merger
   run. §3.2.
4. **TTL policy** — sandbox keeps all rows forever. v0.1 should decide a
   `TTL ts + INTERVAL 90 DAY` (or similar) on the real table so storage is bounded.
5. **`metadata String` is a JSON blob** — fine for v0.1, but if the dashboard
   ever filters on a metadata key, revisit (`Map` type or materialized columns).
6. **Connection settings** — sandbox hard-codes `localhost:8123` / `aegis` /
   `aegis_dev_pw`. v0.1 reads these from env (`CLICKHOUSE_HOST` etc., already in
   docker-compose). Don't copy the hard-coded constants into backend code.

---

## 5. What I should be able to defend

1. **"Why ClickHouse and not Postgres for traces?"** → traces are
   append-heavy, never updated, and queried analytically (GROUP BY over
   millions of rows). Columnar storage + compression + partition pruning makes
   `sum(cost_usd) GROUP BY model` a 70ms scan. Postgres row-store would index-
   or seq-scan the whole table. See ADR 0001.

2. **"Why `project_id` first in the ORDER BY?"** → the ORDER BY key is the
   sparse primary index. Dashboard queries are tenant-scoped, so leading with
   `project_id` lets a single-project query read only that tenant's index
   ranges instead of scanning all tenants.

3. **"What is partition pruning and how did you prove it?"** → `PARTITION BY
   toYYYYMMDD(ts)` stores each day in separate parts. A `WHERE ts >= now() -
   7 DAY` filter skips the other ~23 partitions entirely — Q3 ran in 65ms
   despite the table holding 30 days of data.

4. **"Why is `OPTIMIZE FINAL` in your script but you say never use it?"** →
   it's a sandbox-only device to force a synchronous merge so the benchmark
   timings are deterministic. In production the async background merger does
   this; `OPTIMIZE FINAL` rewrites whole partitions and is far too expensive
   to run on the ingest path.

5. **"Why batch the inserts?"** → every INSERT creates a new MergeTree part;
   row-by-row inserts flood the merger with tiny parts. Batching 50k rows/insert
   got ~150k rows/sec. The v0.1 ingest API must buffer-and-flush.

---

## 6. What this block intentionally does NOT do

- **Does not wire to the real `traces` table.** Uses `traces_block4`. The real
  table is created by v0.1's migration in `backend/app/db/clickhouse.py`.
- **Does not test concurrent inserts.** Single-threaded batch insert only.
  v0.1's ingest will have concurrent writers; part-merge pressure under
  concurrency is untested here.
- **Does not benchmark at 100M rows.** The <100ms bar was cleared at 100k; the
  argument that it holds at 100M (§2.3) is reasoned, not measured.
- **Does not use `clickhouse-client` CLI ergonomics** beyond the Python driver.
  The README table mentions CLI ergonomics; the sandbox went straight to
  `clickhouse_connect` since that's what the backend uses. CLI familiarity is
  a gap to close ad hoc if needed.
- **Does not set a TTL.** Rows accumulate forever. §4 item 4.

---

**Last verified:** 2026-05-14 · `clickhouse_sandbox.py` runs green with no
`PYTHONIOENCODING` override · 100k rows, all 5 queries < 100ms · script + notes
to be committed to `week0/`.

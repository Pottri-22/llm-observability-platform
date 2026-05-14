"""Week 0 · Block 4 — ClickHouse MergeTree probe.

What this teaches:
  1. MergeTree fundamentals — partition key, order key, sparse primary index.
  2. Why ORDER BY (project_id, ts, trace_id) is better than ORDER BY ts alone
     for tenant-scoped analytics (the query pattern Aegis's dashboard uses).
  3. Batch insert vs row-by-row — a 20-100× throughput difference.
  4. Partition pruning — a date-range filter skips entire partitions; ClickHouse
     never touches rows outside the matching date blocks.
  5. The sub-100ms analytic query bar on 100k rows, and why the same design
     holds at 100M rows (columnar scan + compressed blocks).

Artifact requirement (from README §12 week0 table):
  Local ClickHouse container; insert 100k synthetic spans; analytic
  SELECT … GROUP BY returns in < 100 ms.

Schema is intentionally identical to backend/app/db/clickhouse.py::TRACES_TABLE_DDL
so v0.1's migration is validated here first.

Run:
    week0\\.venv\\Scripts\\python.exe week0\\clickhouse_sandbox.py
Prerequisites:
    docker compose up -d clickhouse   (see docker-compose.yml at repo root)
"""

from __future__ import annotations

import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import clickhouse_connect

# Windows console defaults to cp1252, which can't encode the box-drawing chars
# (─) used in section headers. Force UTF-8 so the script runs without a
# PYTHONIOENCODING override. No-op on platforms that already use UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# Connection — mirrors backend/app/db/clickhouse.py settings in dev mode.
# ---------------------------------------------------------------------------

CH_HOST = "localhost"
CH_PORT = 8123
CH_USER = "aegis"
CH_PASSWORD = "aegis_dev_pw"
CH_DATABASE = "aegis"

SANDBOX_TABLE = "traces_block4"   # dedicated name; does NOT touch the real traces table

# ---------------------------------------------------------------------------
# DDL — identical structure to backend/app/db/clickhouse.py::TRACES_TABLE_DDL
#        except the table name, to keep sandbox isolated.
# ---------------------------------------------------------------------------

DDL = f"""
CREATE TABLE IF NOT EXISTS {SANDBOX_TABLE}
(
    trace_id   String,
    org_id     String,
    project_id String,
    ts         DateTime64(3),
    model      String,
    prompt     String,
    completion String,
    tokens_in  UInt32,
    tokens_out UInt32,
    cost_usd   Float64,
    latency_ms UInt32,
    metadata   String,
    inserted_at DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(ts)
ORDER BY (project_id, ts, trace_id)
SETTINGS index_granularity = 8192
""".strip()

# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------

MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "groq/llama-3.3-70b-versatile",
]

# Rough cost rates (USD per token) for synthetic cost generation.
_COST_PER_TOKEN: dict[str, float] = {
    "gpt-4o": 6.25e-6,
    "gpt-4o-mini": 3.75e-7,
    "claude-sonnet-4-6": 4.5e-6,
    "claude-haiku-4-5": 1.5e-6,
    "groq/llama-3.3-70b-versatile": 0.0,
}

PROJECTS = [f"proj_{i:03d}" for i in range(10)]
ORGS = [f"org_{i:02d}" for i in range(3)]

_PROMPTS = [
    "What is the capital of France?",
    "Summarise this document in three bullet points.",
    "Write a Python function to reverse a linked list.",
    "Explain the difference between TCP and UDP.",
    "What are the risks of using LLMs in production?",
]

_COMPLETIONS = [
    "Paris is the capital of France.",
    "• Point one. • Point two. • Point three.",
    "def reverse(head): ...",
    "TCP is reliable; UDP is fast but lossy.",
    "Hallucination, cost bleed, and prompt injection are the main risks.",
]


def _make_row(ts: datetime) -> tuple:
    model = random.choice(MODELS)
    tokens_in = random.randint(20, 200)
    tokens_out = random.randint(20, 300)
    cost = (tokens_in + tokens_out) * _COST_PER_TOKEN[model]
    return (
        str(uuid.uuid4()),                           # trace_id
        random.choice(ORGS),                         # org_id
        random.choice(PROJECTS),                     # project_id
        ts,                                          # ts
        model,                                       # model
        random.choice(_PROMPTS),                     # prompt
        random.choice(_COMPLETIONS),                 # completion
        tokens_in,                                   # tokens_in
        tokens_out,                                  # tokens_out
        cost,                                        # cost_usd
        random.randint(100, 3000),                   # latency_ms
        "{}",                                        # metadata
    )


def generate_rows(n: int, days_back: int = 30) -> list[tuple]:
    """Generate n synthetic trace rows spread over the last `days_back` days."""
    now = datetime.now(tz=timezone.utc)
    rows = []
    for _ in range(n):
        offset_secs = random.uniform(0, days_back * 86400)
        ts = now - timedelta(seconds=offset_secs)
        rows.append(_make_row(ts))
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hdr(text: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {text}")
    print(f"{'─' * 60}")


def _time_query(client: clickhouse_connect.driver.Client, sql: str, label: str) -> float:
    t0 = time.perf_counter()
    result = client.query(sql)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    rows = result.result_rows
    print(f"  [{label}]  {elapsed_ms:6.1f} ms  →  {len(rows)} row(s)")
    for row in rows[:5]:
        print(f"    {row}")
    if len(rows) > 5:
        print(f"    … {len(rows) - 5} more")
    return elapsed_ms


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

COLUMN_NAMES = [
    "trace_id", "org_id", "project_id", "ts", "model",
    "prompt", "completion", "tokens_in", "tokens_out",
    "cost_usd", "latency_ms", "metadata",
]

N_ROWS = 100_000
BATCH_SIZE = 50_000   # two batches of 50k


def main() -> int:
    # ---- connect ----
    print(f"Connecting to ClickHouse at {CH_HOST}:{CH_PORT} …")
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT,
            username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE,
            compress=True,
        )
        client.query("SELECT 1")
    except Exception as exc:
        print(f"\nERROR: cannot connect — {exc}")
        print("Make sure ClickHouse is running:  docker compose up -d clickhouse")
        return 1
    print("  connected.")

    # ---- create table ----
    _hdr("1. DDL — create table (idempotent IF NOT EXISTS)")
    client.command(f"DROP TABLE IF EXISTS {SANDBOX_TABLE}")
    client.command(DDL)
    count_after_create = client.query(f"SELECT count() FROM {SANDBOX_TABLE}").result_rows[0][0]
    print(f"  table '{SANDBOX_TABLE}' created, row count = {count_after_create}")

    # ---- generate data ----
    _hdr(f"2. Generate {N_ROWS:,} synthetic rows (in memory)")
    t0 = time.perf_counter()
    rows = generate_rows(N_ROWS, days_back=30)
    gen_ms = (time.perf_counter() - t0) * 1000.0
    print(f"  generated in {gen_ms:.0f} ms")

    # ---- batch insert ----
    _hdr(f"3. Batch insert — {N_ROWS:,} rows in {N_ROWS // BATCH_SIZE} batches of {BATCH_SIZE:,}")
    t_insert_start = time.perf_counter()
    for i in range(0, N_ROWS, BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        client.insert(SANDBOX_TABLE, batch, column_names=COLUMN_NAMES)
        print(f"  batch {i // BATCH_SIZE + 1}: inserted {len(batch):,} rows")
    insert_ms = (time.perf_counter() - t_insert_start) * 1000.0
    print(f"  total insert time: {insert_ms:.0f} ms  ({N_ROWS / (insert_ms / 1000):.0f} rows/sec)")

    # ClickHouse merges parts asynchronously; OPTIMIZE forces a synchronous merge
    # so the analytic queries hit merged data and reflect the final part layout.
    # In production you would NOT do this — let the background merger run.
    print("  forcing merge for query accuracy (OPTIMIZE TABLE — sandbox only)…")
    client.command(f"OPTIMIZE TABLE {SANDBOX_TABLE} FINAL")

    # Confirm row count
    total = client.query(f"SELECT count() FROM {SANDBOX_TABLE}").result_rows[0][0]
    print(f"  row count after insert: {total:,}")

    # ---- analytic queries ----
    _hdr("4. Analytic queries — target < 100 ms each")
    latencies: list[float] = []

    # Q1: Cost by model — scans only `cost_usd` and `model` columns (2 of 13)
    latencies.append(_time_query(client, f"""
        SELECT model, round(sum(cost_usd), 6) AS total_cost, count() AS n
        FROM {SANDBOX_TABLE}
        GROUP BY model
        ORDER BY total_cost DESC
    """, "cost by model"))

    # Q2: p95 latency by project — scans `latency_ms`, `project_id` only
    latencies.append(_time_query(client, f"""
        SELECT project_id,
               quantile(0.95)(latency_ms) AS p95_ms,
               round(avg(latency_ms), 1)  AS avg_ms,
               count()                    AS n
        FROM {SANDBOX_TABLE}
        GROUP BY project_id
        ORDER BY p95_ms DESC
        LIMIT 5
    """, "p95 latency by project (top 5)"))

    # Q3: Daily token spend — partition-aligned, partition pruning kicks in
    latencies.append(_time_query(client, f"""
        SELECT toDate(ts) AS day,
               sum(tokens_in)  AS total_in,
               sum(tokens_out) AS total_out,
               round(sum(cost_usd), 4) AS cost
        FROM {SANDBOX_TABLE}
        WHERE ts >= now() - INTERVAL 7 DAY
        GROUP BY day
        ORDER BY day
    """, "daily token spend (last 7 days, partition-pruned)"))

    # Q4: Trace count by project + date (ORDER BY-aligned — uses sparse index)
    latencies.append(_time_query(client, f"""
        SELECT project_id, toDate(ts) AS day, count() AS n
        FROM {SANDBOX_TABLE}
        WHERE project_id = 'proj_003'
          AND ts >= now() - INTERVAL 14 DAY
        GROUP BY project_id, day
        ORDER BY day
    """, "single-project 14-day count (ORDER BY-aligned)"))

    # Q5: Raw trace fetch — simulates dashboard trace-detail page
    latencies.append(_time_query(client, f"""
        SELECT trace_id, model, tokens_in, tokens_out, cost_usd, latency_ms
        FROM {SANDBOX_TABLE}
        WHERE project_id = 'proj_005'
        ORDER BY ts DESC
        LIMIT 20
    """, "latest 20 traces for one project"))

    # ---- summary ----
    _hdr("5. Summary")
    print(f"  rows inserted : {total:,}")
    print(f"  insert speed  : {N_ROWS / (insert_ms / 1000):.0f} rows/sec")
    for i, ms in enumerate(latencies, 1):
        flag = "OK" if ms < 100 else "SLOW"
        print(f"  Q{i} latency    : {ms:6.1f} ms  [{flag}]")

    slow = [ms for ms in latencies if ms >= 100]
    if slow:
        print(f"\n  WARNING: {len(slow)} query/queries exceeded 100 ms target.")
        return 1

    print(f"\n  All {len(latencies)} queries under 100 ms — block 4 target cleared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

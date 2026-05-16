"""Celery application — the async eval engine's process boundary.

Why decouple at all: evaluator calls are slow (LLM-as-Judge is another LLM call,
seconds of latency) and best-effort (one judge timing out must never fail the
trace POST). Moving them off the request path lets ingest stay sub-100ms and
isolates eval flakiness from ingest reliability.

Broker = Redis (already in the stack — no new infra). Result backend is also
Redis but unused in code; we set it so `.delay()` returns a usable AsyncResult
in case a future call site wants to await an eval (e.g. CI eval gate in v0.3).
"""

from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery(
    "aegis",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Tasks are auto-discovered from `app.workers.tasks` on worker startup.
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_default_queue="aegis",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Eval jobs make blocking LLM calls that can run for seconds. Greedy prefetch
    # would let one worker hoard the queue while others sit idle — set to 1 so
    # the broker hands a job out only when a worker is actually free.
    worker_prefetch_multiplier=1,
    # ACK after the task completes, so a crashed worker's in-flight job is
    # redelivered to another worker rather than silently lost.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Result rows are mostly written to ClickHouse by the task itself; the Redis
    # result backend is just there to satisfy AsyncResult. Expire its entries
    # quickly so Redis doesn't grow unboundedly.
    result_expires=3600,
)

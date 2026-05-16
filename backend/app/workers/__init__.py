"""Async eval engine — Celery app + task definitions.

The FastAPI ingest path produces eval jobs (`evaluate_trace.delay(...)`); a
separate worker process consumes them. See `celery_app.py` for the Celery
instance and `tasks.py` for the registered tasks.
"""

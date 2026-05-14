"""Unit tests for trace_reader's filter-building logic.

`_build_filters` is the security-critical core of the read path: it must always scope
to a project and must never inline a filter value into the SQL text. These tests pin
both invariants. The query execution itself (clickhouse-connect round-trip) is covered
by the integration suite, not here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.trace_reader import _build_filters


def test_project_id_is_always_present() -> None:
    where, params = _build_filters(project_id="proj-1")
    assert "project_id = {project_id:String}" in where
    assert params == {"project_id": "proj-1"}


def test_no_optional_filters_yields_only_project_scope() -> None:
    where, params = _build_filters(project_id="p")
    assert where == "project_id = {project_id:String}"
    assert list(params) == ["project_id"]


def test_model_filter_adds_clause_and_param() -> None:
    where, params = _build_filters(project_id="p", model="gpt-4o-mini")
    assert "model = {model:String}" in where
    assert params["model"] == "gpt-4o-mini"


def test_time_window_filters_add_clauses() -> None:
    since = datetime(2026, 5, 1, tzinfo=UTC)
    until = datetime(2026, 5, 14, tzinfo=UTC)
    where, params = _build_filters(project_id="p", since=since, until=until)
    assert "ts >= {since:DateTime64(3)}" in where
    assert "ts < {until:DateTime64(3)}" in where
    assert params["since"] == since
    assert params["until"] == until


def test_filter_values_never_inlined_into_sql() -> None:
    """The whole point of server-side parameter binding: a hostile filter value can
    never reach the SQL text — it travels in the params dict instead."""
    hostile = "x'; DROP TABLE traces; --"
    where, params = _build_filters(project_id="p", model=hostile)
    assert hostile not in where
    assert params["model"] == hostile


def test_clauses_joined_with_and() -> None:
    where, _ = _build_filters(
        project_id="p", model="m", since=datetime(2026, 1, 1, tzinfo=UTC)
    )
    # project_id + model + since → two " AND " joins between three clauses.
    assert where.count(" AND ") == 2

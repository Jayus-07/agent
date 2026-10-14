"""Trace 服务端分页、筛选与授权统计回归。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import observability


def _trace(trace_id: str, *, tenant: str, user: str, status: str,
           timestamp: str, cost_usd: float = 0) -> dict:
    return {
        "id": trace_id,
        "request_id": f"req-{trace_id}",
        "timestamp": timestamp,
        "session_id": f"session-{trace_id}",
        "duration_ms": 125 if status != "running" else 0,
        "status": status,
        "workflow_name": "agent",
        "cost_usd": cost_usd,
        "metadata": {},
        "error": {},
        "tags": {"tenant_id": tenant, "user_id": user},
        "spans": [],
    }


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(observability.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client() -> TestClient:
    return _client()


def _headers() -> dict[str, str]:
    return {
        "X-Auth-Type": "jwt",
        "X-User-Id": "u1",
        "X-Tenant-Id": "tenant-a",
        "X-User-Roles": "admin",
    }


def _viewer_headers() -> dict[str, str]:
    return {
        "X-Auth-Type": "jwt",
        "X-User-Id": "u1",
        "X-Tenant-Id": "tenant-a",
        "X-User-Roles": "viewer",
    }


@pytest.fixture
def traces(monkeypatch):
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=10)).isoformat()
    stale = (now - timedelta(days=40)).isoformat()
    rows = [
        _trace("foreign", tenant="tenant-b", user="u2", status="success",
               timestamp=recent, cost_usd=100),
        _trace("mine-new", tenant="tenant-a", user="u1", status="success",
               timestamp=recent, cost_usd=1),
        _trace("mine-error", tenant="tenant-a", user="u1", status="error",
               timestamp=recent, cost_usd=2),
        _trace("mine-old", tenant="tenant-a", user="u1", status="success",
               timestamp=stale, cost_usd=3),
    ]
    monkeypatch.setattr(observability.trace_collector, "list", lambda *_a, **_kw: rows)
    monkeypatch.setattr(observability, "_stored_dict_to_dto", lambda row: row)
    return rows


def test_list_filters_authorized_records_before_pagination(client, traces):
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    response = client.get(
        "/observability/traces",
        params={"limit": 1, "offset": 1, "since": since, "status": "success"},
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [trace["id"] for trace in body["traces"]] == []
    assert body["has_more"] is False
    assert "foreign" not in response.text


def test_list_paginates_all_authorized_matches_across_retained_rows(client, traces):
    response = client.get(
        "/observability/traces",
        params={"limit": 1, "offset": 1, "status": "success"},
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [trace["id"] for trace in body["traces"]] == ["mine-old"]
    assert body["retention_limit"] == observability.TRACE_RETENTION_LIMIT


def test_status_filter_matches_unknown_without_inferring_from_duration(client, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        _trace("missing-status", tenant="tenant-a", user="u1", status="", timestamp=now),
        _trace("explicit-error", tenant="tenant-a", user="u1", status="", timestamp=now),
    ]
    rows[1]["error"] = {"code": "RUN_FAILED"}
    monkeypatch.setattr(observability.trace_collector, "list", lambda *_a, **_kw: rows)
    monkeypatch.setattr(observability, "_stored_dict_to_dto", lambda row: row)

    unknown = client.get("/observability/traces?status=unknown", headers=_headers())
    error = client.get("/observability/traces?status=error", headers=_headers())

    assert unknown.status_code == 200
    assert [trace["id"] for trace in unknown.json()["traces"]] == ["missing-status"]
    assert error.status_code == 200
    assert [trace["id"] for trace in error.json()["traces"]] == ["explicit-error"]


def test_stats_are_tenant_scoped_and_report_retention_bound(client, traces):
    response = client.get("/observability/traces/stats?hours=24", headers=_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["records_considered"] == 2
    assert body["counts"]["success"] == 1
    assert body["counts"]["error"] == 1
    assert body["total_cost_usd"] == 3
    assert body["retention_limit"] == observability.TRACE_RETENTION_LIMIT


def test_stats_reject_unauthenticated_requests(client, traces):
    response = client.get("/observability/traces/stats?hours=24")
    assert response.status_code in (401, 403, 503)
    assert "mine-new" not in response.text


def test_viewer_list_hides_other_owners_in_same_tenant(client, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        _trace("mine", tenant="tenant-a", user="u1", status="success", timestamp=now),
        _trace("same-tenant-other-owner", tenant="tenant-a", user="u2", status="success", timestamp=now),
        _trace("foreign-tenant", tenant="tenant-b", user="u1", status="success", timestamp=now),
    ]
    monkeypatch.setattr(observability.trace_collector, "list", lambda *_a, **_kw: rows)
    monkeypatch.setattr(observability, "_stored_dict_to_dto", lambda row: row)

    response = client.get("/observability/traces?limit=20", headers=_viewer_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [trace["id"] for trace in body["traces"]] == ["mine"]
    assert "same-tenant-other-owner" not in response.text
    assert "foreign-tenant" not in response.text


def test_viewer_stats_hide_other_owners_and_their_cost(client, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        _trace("mine", tenant="tenant-a", user="u1", status="success", timestamp=now, cost_usd=1),
        _trace("same-tenant-other-owner", tenant="tenant-a", user="u2", status="error", timestamp=now, cost_usd=25),
        _trace("foreign-tenant", tenant="tenant-b", user="u1", status="success", timestamp=now, cost_usd=100),
    ]
    monkeypatch.setattr(observability.trace_collector, "list", lambda *_a, **_kw: rows)

    response = client.get("/observability/traces/stats?hours=24", headers=_viewer_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["records_considered"] == 1
    assert body["counts"]["success"] == 1
    assert body["counts"]["error"] == 0
    assert body["total_cost_usd"] == 1

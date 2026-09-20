"""竞品 REST 写入口的身份与幂等门禁契约。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api.identity import Identity
from backend.app.api.routes import competitor as competitor_route


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/competitor/watchlist",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "query_string": b"",
    })


def test_competitor_write_rejects_missing_trusted_identity_before_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        competitor_route,
        "resolve_identity",
        lambda _request: Identity(source="guest"),
    )

    with pytest.raises(HTTPException) as exc_info:
        competitor_route._run_idempotent_write(
            _request(),
            "competitor.api.watchlist.add",
            {"url": "https://example.com"},
            lambda: calls.append("write") or {"ok": True},
            approval_action="watchlist_add",
        )

    assert exc_info.value.status_code == 401
    assert calls == []


def test_competitor_write_rejects_missing_idempotency_key_before_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        competitor_route,
        "resolve_identity",
        lambda _request: Identity(
            user_id="user-1", tenant_id="tenant-1", source="header",
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        competitor_route._run_idempotent_write(
            _request(),
            "competitor.api.watchlist.add",
            {"url": "https://example.com"},
            lambda: calls.append("write") or {"ok": True},
            approval_action="watchlist_add",
        )

    assert exc_info.value.status_code == 400
    assert calls == []

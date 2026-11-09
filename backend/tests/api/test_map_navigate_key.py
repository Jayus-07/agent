"""审计 #44：/map/navigate 不得把含后端 Key 的 URL 返回给调用方。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import maps as maps_route


def _client(monkeypatch, *, key_kind: str) -> TestClient:
    monkeypatch.setattr(maps_route, "_ensure_configured", lambda: None)
    monkeypatch.setattr(
        maps_route.api, "navigation_uri",
        lambda **_kw: {"url": "https://x/nav?key=SECRET", "key_kind": key_kind,
                       "mode": "drive", "policy": ""},
    )
    app = FastAPI()
    app.include_router(maps_route.router)
    return TestClient(app, raise_server_exceptions=False)


def test_backend_key_url_is_rejected(monkeypatch):
    client = _client(monkeypatch, key_kind="backend")
    resp = client.get("/map/navigate", params={"to": "30.1,120.1"})
    assert resp.status_code == 409
    body = resp.json()
    detail = body.get("detail") or {}
    assert detail.get("error") == "MapFrontendKeyNotConfigured"
    assert "SECRET" not in resp.text


def test_frontend_key_url_is_returned(monkeypatch):
    client = _client(monkeypatch, key_kind="frontend")
    resp = client.get("/map/navigate", params={"to": "30.1,120.1"})
    assert resp.status_code == 200
    assert resp.json()["key_kind"] == "frontend"

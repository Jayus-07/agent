"""请求体上限中间件（2026-11-08 安全审计 T6）。

锁定：按路径上限在读取 body 之前用 Content-Length 拒绝 413；
webhook / 选品导入 / RAG 上传各有独立上限，普通 JSON API 用默认 8MB。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.api.middleware.body_limit import (
    DEFAULT_BODY_LIMIT,
    body_size_limit_middleware,
    request_body_limit,
)
from backend.config.rag import RAG_MAX_FILE_SIZE


def _client() -> TestClient:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        return {"length": len(await request.body())}

    @app.post("/webhooks/automations/t/a")
    async def webhook(request: Request):
        return {"ok": True}

    @app.get("/echo")
    async def echo_get():
        return {"ok": True}

    app.middleware("http")(body_size_limit_middleware)
    return TestClient(app)


def test_request_body_limit_rules() -> None:
    assert request_body_limit("/webhooks/automations/t/a") == 1024 * 1024
    assert request_body_limit("/selection-funnel/import/file") == 32 * 1024 * 1024
    assert request_body_limit("/rag/upload") == RAG_MAX_FILE_SIZE * 1024 * 1024
    assert request_body_limit("/chat/stream") == DEFAULT_BODY_LIMIT


def test_webhook_oversize_rejected_with_413() -> None:
    client = _client()
    resp = client.post(
        "/webhooks/automations/t/a", content=b"x" * (1024 * 1024 + 1)
    )
    assert resp.status_code == 413
    assert resp.json()["error"] == "PayloadTooLarge"


def test_default_oversize_rejected_with_413() -> None:
    client = _client()
    resp = client.post("/echo", content=b"x" * (DEFAULT_BODY_LIMIT + 1))
    assert resp.status_code == 413


def test_within_limit_passes() -> None:
    client = _client()
    resp = client.post("/echo", content=b"x" * 1024)
    assert resp.status_code == 200
    assert resp.json() == {"length": 1024}


def test_get_is_not_limited() -> None:
    client = _client()
    assert client.get("/echo").status_code == 200


@pytest.mark.parametrize("size", [0, 1, 1024])
def test_small_bodies_pass(size: int) -> None:
    client = _client()
    assert client.post("/echo", content=b"x" * size).status_code == 200

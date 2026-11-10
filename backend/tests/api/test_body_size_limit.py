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
from backend.config.rag import RAG_MAX_FILE_SIZE, RAG_UPLOAD_MULTIPART_OVERHEAD


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
    # /rag/upload 带 multipart 开销余量（F10），与路由层预检同源；
    # 贴上限文件（cl = 文件体 + 封装开销）不被中间件 413 误拒
    assert request_body_limit("/rag/upload") == (
        RAG_MAX_FILE_SIZE * 1024 * 1024 + RAG_UPLOAD_MULTIPART_OVERHEAD
    )
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


def test_rag_upload_boundary_body_within_margin_passes(monkeypatch) -> None:
    """F10 回归（缩比复现）：文件体 + multipart 开销的请求体不被中间件误拒。

    此前 /rag/upload 规则用精确 RAG_MAX_FILE_SIZE 判 Content-Length，
    贴上限文件在中间件层 413，路由层特意留的余量形同虚设
    （2026-11-10 评审发现）。真实常量由 test_request_body_limit_rules 钉死，
    本用例缩比验证中间件「总包 ≤ 上限」的判定逻辑本身。
    """
    from backend.app.api.middleware import body_limit as body_limit_mod

    monkeypatch.setattr(
        body_limit_mod, "_BODY_LIMIT_RULES", (("/rag/upload", 1024 + 16),)
    )
    client = _client()

    @client.app.post("/rag/upload")  # type: ignore[attr-defined]
    async def rag_upload_stub(request: Request):  # noqa: F811 — 测试内注册
        return {"length": len(await request.body())}

    # 文件体 1024 + 封装开销 16 = 总包 1040，恰在上限内 → 放行
    resp = client.post("/rag/upload", content=b"x" * 1040)
    assert resp.status_code == 200
    assert resp.json() == {"length": 1040}


def test_rag_upload_over_total_limit_rejected(monkeypatch) -> None:
    """总包超过「文件上限 + 余量」仍 413；文件体精确上限由路由流式计数兜底。"""
    from backend.app.api.middleware import body_limit as body_limit_mod

    monkeypatch.setattr(
        body_limit_mod, "_BODY_LIMIT_RULES", (("/rag/upload", 1024 + 16),)
    )
    client = _client()

    @client.app.post("/rag/upload")  # type: ignore[attr-defined]
    async def rag_upload_stub(request: Request):  # noqa: F811 — 测试内注册
        return {"length": len(await request.body())}

    resp = client.post("/rag/upload", content=b"x" * 1041)
    assert resp.status_code == 413


@pytest.mark.parametrize("size", [0, 1, 1024])
def test_small_bodies_pass(size: int) -> None:
    client = _client()
    assert client.post("/echo", content=b"x" * size).status_code == 200

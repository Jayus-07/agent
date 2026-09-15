"""test_internal_token_failclosed.py — 内部服务凭据（X-Internal-Token）fail-closed 契约

S0-4（2026-09-15）背景：`require_internal_token` 原实现在 `AI_INTERNAL_TOKEN` 未配置时
直接 return 放行 —— 等于「服务间网关无凭据即开放」。本文件锁定改造后的语义，防止回退。

被测：`backend.app.api.deps.require_internal_token`

测试口径说明（沿用 test_rag_server_internal_token.py 的既有经验）：
  config 的 ENVIRONMENT / AI_INTERNAL_TOKEN 是进程内缓存常量，`setenv` 改不动，
  且不应 `importlib.reload`（会污染其它测试）。因此改为**打桩读取点**：
    - `backend.config.messaging.AI_INTERNAL_TOKEN`（函数内惰性 import，逐次读取）
    - `backend.app.api.deps.ENVIRONMENT` / `deps.ALLOW_UNAUTHENTICATED`（模块级已绑定）
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.app.api import deps


def _make_client() -> TestClient:
    """最小 app：仅挂一个受该依赖保护的路由，避免触达业务端点。"""
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(deps.require_internal_token)])
    async def probe() -> dict:
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def _patched(monkeypatch):
    """统一的打桩入口：返回一个可设置 (token, environment, allow_unauth) 的函数。"""

    def _apply(*, token: str, environment: str = "development", allow_unauth: bool = False):
        monkeypatch.setattr("backend.config.messaging.AI_INTERNAL_TOKEN", token, raising=False)
        monkeypatch.setattr(deps, "ENVIRONMENT", environment)
        monkeypatch.setattr(deps, "ALLOW_UNAUTHENTICATED", allow_unauth)

    return _apply


# ── 1. 令牌已配置：正常校验 ──────────────────────────────

def test_configured_token_correct_header_passes(_patched):
    _patched(token="sekrit", environment="production")
    res = _make_client().get("/probe", headers={"X-Internal-Token": "sekrit"})
    assert res.status_code == 200, res.text


def test_configured_token_missing_header_401(_patched):
    _patched(token="sekrit", environment="production")
    res = _make_client().get("/probe")
    assert res.status_code == 401, res.text


def test_configured_token_wrong_header_401(_patched):
    _patched(token="sekrit", environment="production")
    res = _make_client().get("/probe", headers={"X-Internal-Token": "nope"})
    assert res.status_code == 401, res.text


# ── 2. 令牌未配置：fail-closed（本次改造的核心）────────────

def test_no_token_production_denied(_patched):
    """生产未配令牌 —— 即使开了豁免开关也必须拒绝（defense-in-depth）。"""
    _patched(token="", environment="production", allow_unauth=True)
    res = _make_client().get("/probe")
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["error"] == "InternalTokenNotConfigured"


def test_no_token_nonprod_without_exemption_denied(_patched):
    """非生产但未显式豁免 —— 同样拒绝（不再静默放行）。"""
    _patched(token="", environment="development", allow_unauth=False)
    res = _make_client().get("/probe")
    assert res.status_code == 503, res.text


def test_no_token_nonprod_with_exemption_passes(_patched):
    """显式 ALLOW_UNAUTHENTICATED=true —— 本地开发豁免，放行。"""
    _patched(token="", environment="development", allow_unauth=True)
    res = _make_client().get("/probe")
    assert res.status_code == 200, res.text


# ── 3. 防回退 ────────────────────────────────────────────

def test_regression_no_silent_pass_when_unconfigured(_patched):
    """回归锁：默认环境（非生产、未豁免）下，空令牌必须被拒。

    对应改造前行为 —— 空令牌直接 return 放行。若有人改回，本用例红。
    """
    _patched(token="", environment="", allow_unauth=False)
    res = _make_client().get("/probe")
    assert res.status_code == 503, "空令牌 + 未显式豁免时必须 fail-closed"

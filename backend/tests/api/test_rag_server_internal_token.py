"""test_rag_server_internal_token.py — rag-server 内部令牌防护契约（P3 就绪清单第 5 条）

  - require_internal_boot 纯函数：production + 无 token → RuntimeError（拒启动）
  - 有 token：业务端点缺头/错头 401，对头放行，/healthz /readyz 豁免
  - 开发模式（token 空）：全放行（对齐 /internal/ai/* 行为）
  不用 importlib.reload——config 的 ENVIRONMENT/AI_INTERNAL_TOKEN 是进程内
  缓存常量，setenv 改不动；改注入点（纯函数参数 / _internal_token 打桩）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.services import rag_server as m
from backend.services.rag_server import require_internal_boot


# ── 启动守卫（纯函数，参数注入）──

def test_production_without_token_rejects_boot():
    with pytest.raises(RuntimeError, match="AI_INTERNAL_TOKEN"):
        require_internal_boot(token="", environment="production")


def test_development_without_token_allowed():
    require_internal_boot(token="", environment="development")  # 不抛
    require_internal_boot(token="", environment="staging")


def test_with_token_allowed_even_in_production():
    require_internal_boot(token="sekrit", environment="production")


def test_real_boot_guard_runs_on_import():
    # 模块导入时守卫已真实执行过一次（本环境非 production，此处验证可重复调用）
    require_internal_boot()


# ── HTTP 中间件（打桩 _internal_token，不 reload）──
# ⚠ 不要用 /ask 验证放行：防护层一旦放行会真进业务端点并触发 RAG pipeline
# 初始化（重资源，曾把测试进程硬杀）。这里挂 /__probe 探针路由测中间件层。

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(m, "_internal_token", lambda: "sekrit")
    m.app.add_api_route("/__probe", lambda: {"ok": True}, methods=["GET", "POST"])
    return TestClient(m.app, raise_server_exceptions=False)


def test_missing_token_401(client):
    assert client.post("/__probe").status_code == 401


def test_wrong_token_401(client):
    r = client.post("/__probe", headers={"X-Internal-Token": "wrong"})
    assert r.status_code == 401


def test_correct_token_passes_guard(client):
    r = client.post("/__probe", headers={"X-Internal-Token": "sekrit"})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_health_paths_exempt(client):
    assert client.get("/healthz").status_code == 200


def test_ask_reports_service_admission_and_pipeline_timing(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr("backend.prompts.hot_reload.ensure_prompt_snapshot_fresh_async", AsyncMock())
    monkeypatch.setattr(m, "_get_pipeline", lambda: SimpleNamespace(
        ask=lambda *a, **k: "测试答案", last_answer_meta={},
    ))
    response = client.post("/ask", json={"question": "测试问题"},
                           headers={"X-Internal-Token": "sekrit"})
    assert response.status_code == 200
    assert response.json()["answer"] == "测试答案"
    for header in ("X-RAG-Service-Ms", "X-RAG-Prehandler-Ms", "X-RAG-Pipeline-Init-Ms"):
        assert float(response.headers[header]) >= 0
    assert client.get("/readyz").status_code != 401


def test_dev_mode_without_token_allows_all(monkeypatch):
    monkeypatch.setattr(m, "_internal_token", lambda: "")
    m.app.add_api_route("/__probe2", lambda: {"ok": True}, methods=["GET"])
    client = TestClient(m.app, raise_server_exceptions=False)
    assert client.get("/__probe2").status_code == 200


@pytest.mark.parametrize("failed", [False, True])
def test_ask_binds_usage_identity_and_restores_it(monkeypatch, failed):
    from types import SimpleNamespace

    from backend.core.request_context import (
        get_tool_tenant_id, get_tool_user_id, scoped_tool_identity,
    )
    from backend.infra.llm import budget
    from backend.infra.llm.proxy import (
        _thread_local_user_id, scoped_current_user_id,
    )

    observed = []

    def ask(**kwargs):
        observed.append((get_tool_user_id(), get_tool_tenant_id(),
                         _thread_local_user_id()))
        if failed:
            raise RuntimeError("生成失败")
        return "答案"

    monkeypatch.setattr(m, "_get_pipeline", lambda: SimpleNamespace(
        ask=ask, last_answer_meta={},
    ))
    monkeypatch.setattr(budget, "bind_request_budget", lambda *a, **kw: None)
    monkeypatch.setattr(budget, "clear_request_budget", lambda: None)
    with scoped_tool_identity(user_id="outer", tenant_id="outer-tenant"), \
            scoped_current_user_id("outer"):
        request = m.AskRequest(question="测试", user_id="42", tenant_id="t1")
        if failed:
            with pytest.raises(RuntimeError, match="生成失败"):
                m.ask(request)
        else:
            assert m.ask(request)["answer"] == "答案"
        assert (get_tool_user_id(), get_tool_tenant_id(),
                _thread_local_user_id()) == ("outer", "outer-tenant", "outer")
    assert observed == [("42", "t1", "42")]

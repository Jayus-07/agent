"""test_orchestration_debt_fixes.py — 2026-09-17 编排层五项债务修复的守护测试

锁定以下行为，防回退：
  R2  orchestration/tool_registry → capability_registry 改名（旧路径仅剩 shim）
  R3  builder._make_sync 线程本地事件循环复用
  R4  路由缓存键含上下文位（department/user_id 不互串）
  R5  plan 依赖深度上限（critique 机器守门，拒绝超限计划进 supervisor）
  R1  GET /agents/tools — Tool 表的运行时消费方
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

import backend

# ═══════════════════════════════════════════════════
# R2: capability_registry 改名
# ═══════════════════════════════════════════════════


def test_no_stray_old_import_paths():
    """全仓 .py 不得再引用旧模块路径（shim 本体除外）。

    判据字符串动态拼接，避免本文件被扫描时自匹配。
    """
    needle = "backend.orchestration" + ".tool_registry"
    repo = Path(backend.__file__).resolve().parent.parent
    allowed = {
        repo / "backend/orchestration/tool_registry.py",  # shim 本体
        Path(__file__).resolve(),  # 本测试
    }
    hits: list[str] = []
    for root in ("backend", "scripts", "tests", "mcp_servers"):
        base = repo / root
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if py in allowed or "__pycache__" in py.parts:
                continue
            if needle in py.read_text(encoding="utf-8"):
                hits.append(str(py.relative_to(repo)))
    assert not hits, f"以下文件仍引用已改名的 orchestration.tool_registry: {hits}"


def test_shim_exports_same_singleton():
    """旧 import 路径仍可用，且拿到的是新模块的同一单例。"""
    import warnings

    from backend.orchestration.capability_registry import (
        tool_registry as new_registry,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        shim = importlib.reload(
            importlib.import_module("backend.orchestration.tool_registry")
        )
    assert shim.tool_registry is new_registry
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


# ═══════════════════════════════════════════════════
# R3: _make_sync 线程本地事件循环复用
# ═══════════════════════════════════════════════════


def test_make_sync_reuses_thread_local_loop():
    """同线程多次调用共享同一事件循环（旧 asyncio.run 每次新建）。"""
    from backend.orchestration.graph.builder import _make_sync

    seen: list[int] = []

    async def probe(state: dict) -> dict:
        seen.append(id(asyncio.get_running_loop()))
        return {**state, "ok": True}

    wrapper = _make_sync(probe)
    assert wrapper({})["ok"] is True
    assert wrapper({})["ok"] is True
    assert len(seen) == 2
    assert seen[0] == seen[1], "两次调用应复用同一线程本地事件循环"


# ═══════════════════════════════════════════════════
# R4: 路由缓存键上下文位
# ═══════════════════════════════════════════════════


def test_route_cache_key_context_positions():
    from backend.orchestration.router.router import (
        _ROUTE_CACHE_CONTEXT_VERSION,
        _route_cache_key,
    )

    base = _route_cache_key("查库存")
    assert base.startswith(_ROUTE_CACHE_CONTEXT_VERSION)
    assert _route_cache_key("  查库存 ") == base  # strip/lower 归一保持
    ctx1 = {"user_id": "u1", "department": "d1"}
    assert _route_cache_key("查库存", ctx1) != base  # 上下文参与键构造
    assert (
        _route_cache_key("查库存", {"user_id": "u2", "department": "d1"})
        != _route_cache_key("查库存", ctx1)
    )
    assert (
        _route_cache_key("查库存", {"b": 2, "a": 1})
        == _route_cache_key("查库存", {"a": 1, "b": 2})  # 键序无关
    )


def test_router_cache_scoped_by_context(monkeypatch):
    """行为级：同上下文命中缓存，不同上下文不互串。"""
    import backend.orchestration.router.router as rmod
    from backend.orchestration.router.types import (
        CapabilityScore,
        ExecutionMode,
        RouteDecision,
    )

    calls = {"n": 0}

    class FakeRule:
        def route(self, q: str):
            calls["n"] += 1
            return RouteDecision(
                execution_mode=ExecutionMode.DIRECT,
                candidates=[CapabilityScore(name="sql.query", score=0.9)],
                confidence=0.9,
                reason="fake-strong-rule",
            )

    class StubCache:
        def __init__(self):
            self.store: dict = {}

        def get_json(self, k):
            return self.store.get(k)

        def set_json(self, k, v):
            self.store[k] = v

    monkeypatch.setattr(rmod, "_router_cache", StubCache())
    router = rmod.Router.__new__(rmod.Router)  # 跳过三层初始化（不碰向量索引）
    router.rule = FakeRule()
    router.vector = None
    router.llm = None

    router.route("同文问题", context={"user_id": "u1", "department": "d"})
    router.route("同文问题", context={"user_id": "u1", "department": "d"})
    assert calls["n"] == 1, "同上下文第二次调用应命中缓存"

    router.route("同文问题", context={"user_id": "u2", "department": "d"})
    assert calls["n"] == 2, "不同 user_id 的同文问题不得互串缓存"


# ═══════════════════════════════════════════════════
# R5: plan 深度守门
# ═══════════════════════════════════════════════════


def _plan(edges: dict) -> dict:
    """由 edges 反推最小合法 plan（节点自动补齐）。"""
    nodes: dict[str, dict] = {}
    for sid, deps in edges.items():
        nodes[str(sid)] = {"step_id": str(sid), "capability": "sql.query",
                           "description": "", "params": {}}
        for d in deps:
            nodes[str(d)] = {"step_id": str(d), "capability": "sql.query",
                             "description": "", "params": {}}
    return {"nodes": nodes, "edges": edges}


def test_plan_depth_pure_function():
    from backend.agents.planner.plan_utils import MAX_PLAN_DEPTH, plan_depth

    assert plan_depth({"nodes": {}, "edges": {}}) == 0
    assert plan_depth({"nodes": {"1": {}}, "edges": {}}) == 1
    assert plan_depth(_plan({"4": ["3"], "3": ["2"], "2": ["1"]})) == 4  # 串行链
    assert plan_depth(_plan({"4": ["1", "2"], "3": ["1"]})) == 2  # 菱形
    assert plan_depth(_plan({"1": ["2"], "2": ["1"]})) == MAX_PLAN_DEPTH + 1  # 环
    assert plan_depth(_plan({f"{i}": [str(i - 1)] for i in range(2, 12)})) == 11


def test_critique_rejects_overdeep_plan(monkeypatch):
    """超限计划不得进入 supervisor：LLM 修正失败时兜底拒绝（空 plan）。"""
    import backend.skills  # noqa: F401  # 注册 capability，规则引擎查表用

    import backend.agents.planner.critique as critique_mod

    deep_plan = _plan({str(i): [str(i - 1)] for i in range(2, 12)})  # 深度 11

    class BoomLLM:
        def invoke(self, *a, **k):
            raise RuntimeError("unit-test: LLM 不可用")

    monkeypatch.setattr(critique_mod, "ENABLE_PLAN_CRITIQUE", True)
    monkeypatch.setattr(critique_mod, "llm", BoomLLM())

    result = critique_mod.critique_node({"plan": deep_plan, "question": "查询数据"})
    assert result["plan"]["nodes"] == {}, "超深计划必须被拒绝为空 plan"
    assert result["_plan_critiqued"] is True


def test_critique_passes_healthy_plan_through():
    """正常浅层计划原样放行（不被深度守门误伤）。"""
    import backend.skills  # noqa: F401

    from backend.agents.planner.critique import critique_node

    plan = _plan({"2": ["1"]})
    result = critique_node({"plan": plan, "question": "查询本月各渠道销售额"})
    assert result["plan"] == plan
    assert result["_plan_critiqued"] is True
    assert result["_plan_changed"] is False


# ═══════════════════════════════════════════════════
# R1: GET /agents/tools — Tool 表运行时消费方
# ═══════════════════════════════════════════════════


def test_agents_tools_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.routes.agents import router as agents_router

    app = FastAPI()
    app.include_router(agents_router)
    resp = TestClient(app).get("/agents/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 30, "Tool 表应加载全部 @tool（当前 34 个）"
    assert body["summary"]["phantom"] == 0, "不应出现幽灵注册"
    assert body["summary"]["duplicates"] == 0, "不应出现重复定义"
    assert {t["name"] for t in body["tools"]}  # 清单非空且带 name

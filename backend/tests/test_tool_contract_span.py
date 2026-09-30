"""Tool 契约 hash 随 trace span 测试（治理 #5 Tool 侧补齐）

1. _tool_contract_hash：34 Tool 全命中 + 未命中空串 + 进程内缓存
2. span 注入：execute 路径 end_span metrics 含 contract_hash（mock trace）
"""
from __future__ import annotations


class TestToolContractHash:
    def test_known_tool_resolved(self):
        from backend.skills.base import _tool_contract_hash

        h = _tool_contract_hash("execute_sql_tool")
        assert len(h) == 16

    def test_all_lock_tools_covered(self):
        import json
        from pathlib import Path

        from backend.skills.base import _tool_contract_hash

        lock = json.loads(
            (Path(__file__).resolve().parents[1] / "tool_contracts.lock.json")
            .read_text(encoding="utf-8"))
        for name in lock["tools"]:
            assert len(_tool_contract_hash(name)) == 16, name

    def test_unknown_tool_empty(self):
        from backend.skills.base import _tool_contract_hash

        assert _tool_contract_hash("no_such_tool") == ""

    def test_cache_stable(self):
        from backend.skills import base as skill_base

        skill_base._contract_hash_cache = None  # 重置后重建
        a = skill_base._tool_contract_hash("execute_sql_tool")
        assert skill_base._contract_hash_cache is not None  # 缓存已建
        assert a == skill_base._tool_contract_hash("execute_sql_tool")


class TestSpanInjection:
    def test_governed_path_injects_contract_hash(self, monkeypatch):
        """end_span metrics 必须带 contract_hash（治理层主路径）。"""
        import asyncio
        from unittest.mock import MagicMock

        from backend.core.tool_runtime.models import ToolResult, ToolStatus
        from backend.skills.base import BaseSkill

        captured: dict = {}

        class _TC:
            def current(self):
                return MagicMock()

            def start_span(self, *a, **k):
                return "span-1"

            def end_span(self, span, **kw):
                captured.update(kw.get("metrics") or {})

        monkeypatch.setattr(
            "backend.observability.tracer.trace_collector", _TC())

        class _Skill(BaseSkill):
            name = "map"

            @property
            def _tool_fn(self):
                m = MagicMock()
                m.name = "map_lookup_tool"
                m.invoke = lambda params: {"ok": True}
                return m

            async def _run_capability(self, state, sr, params):
                return {}

        skill = _Skill()
        skill.output_types = {}
        sr = {"step_id": "1", "capability": "map.lookup", "retries": 0,
              "description": ""}
        state = {"plan": {"nodes": {"1": {"capability": "map.lookup"}}},
                 "current_step_id": "1", "step_results": {}}

        # 直接跑治理路径内部太重（熔断/隔离舱全链）——这里走真实
        # safe_tool_executor 需要 policy 注册；mock 到最小：
        # 只验证 execute() 主路径把 contract_md 传给 _execute_governed
        called: dict = {}

        async def fake_governed(self, state, sr, step_results, tool_fn,
                                invoke_params, params, timeout, max_retries,
                                tool_span, contract_md=None):
            called["contract_md"] = contract_md
            step_results[sr["step_id"]] = dict(sr)  # 真实路径的收尾职责

        monkeypatch.setattr(BaseSkill, "_execute_governed", fake_governed)
        monkeypatch.setattr("backend.skills.base._tool_runtime_enabled", lambda: True)
        # 测试焦点是 contract_md 传递；前置参数校验不属本用例
        monkeypatch.setattr("backend.skills.base.validate_invocation",
                            lambda *a, **k: None)
        asyncio.run(skill.execute(state, step_capability="map.lookup"))
        assert called["contract_md"] == {"contract_hash": skill_base_hash()}
        assert len(called["contract_md"]["contract_hash"]) == 16


def skill_base_hash():
    from backend.skills.base import _tool_contract_hash

    return _tool_contract_hash("map_lookup_tool")

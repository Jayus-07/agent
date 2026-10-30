"""Prompt 版本记录覆盖缺口测试（2026-10-30 obs-collection-handoff）。

根因：`get_template_sync` 不落版本记录，daily_report 的 InventoryAnalyzer
用其取系统提示词 → workflow trace prompt_versions 恒空。修复后调用方显式
record_template_usage_sync（record_prompt_version 按 key 幂等）。

口径说明（不在本测试范围、记录于任务 PROGRESS）：
- rag/chain.py 已有 _record_runtime_prompt_usage（rag.qa/rag.document/
  rag.contextualize），不重复埋点；
- inventory_alert 全链无 LLM 调用，prompt_versions=0 属如实空值。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.observability import tracer as tracer_mod


@pytest.fixture
def _fresh_tracer(monkeypatch) -> tracer_mod.TraceCollector:
    collector = tracer_mod.TraceCollector()
    monkeypatch.setattr(tracer_mod, "trace_collector", collector)
    tracer_mod._current_trace_var.set(None)
    return collector


class TestInventoryAnalyzerPromptVersion:
    def test_run_records_template_version(self, _fresh_tracer):
        from backend.agents.capability.inventory_analyzer import InventoryAnalyzer

        trace = _fresh_tracer.start("[Workflow] daily_report",
                                    workflow_name="daily_report")
        analyzer = InventoryAnalyzer()

        async def _fake_call_llm(*args, **kwargs) -> str:
            # 合法 JSON 输出，走正常解析路径
            return json.dumps({
                "anomalies": [], "advice": [],
                "confidence": 0.9, "reasoning": "ok",
            }, ensure_ascii=False)

        analyzer._call_llm = _fake_call_llm  # 只测 prompt 记账，不触达真 LLM
        result = asyncio.run(analyzer.run({
            "sales_data": [], "inventory_data": [],
            "rules": "", "alert_level": "warning",
        }))
        assert result["confidence"] == 0.9
        versions = trace.metadata.get("prompt_versions") or []
        matched = [v for v in versions
                   if v["key"] == "capability.inventory_analyzer"]
        assert len(matched) == 1
        assert matched[0]["source"] in ("default", "snapshot", "pinned", "db")

    def test_same_key_not_duplicated(self, _fresh_tracer):
        """record_prompt_version 按 key 幂等：链路里二次渲染不产生重复条目。"""
        from backend.observability.prompt_trace import record_prompt_version

        trace = _fresh_tracer.start("q", workflow_name="daily_report")
        record_prompt_version("capability.inventory_analyzer", None, "default")
        record_prompt_version("capability.inventory_analyzer", 2, "db")
        versions = trace.metadata["prompt_versions"]
        assert len(versions) == 1
        assert versions[0]["version"] == 2

    def test_runner_map_snapshot_updated_in_place(self, _fresh_tracer):
        """runner 的 {key: version} map 快照在场时原地更新，不抛错不覆盖丢数据。

        回归根因：旧实现按 list 迭代 map（str.get 抛 AttributeError 被
        静默吞掉），workflow trace 上 render_sync 的精确记录从未生效。
        """
        from backend.observability.prompt_trace import record_prompt_version

        trace = _fresh_tracer.start("q", workflow_name="daily_report")
        trace.metadata["prompt_versions"] = {"rag.qa": 17, "general_chat.system": 3}
        record_prompt_version("capability.inventory_analyzer", None, "default")
        record_prompt_version("general_chat.system", 4, "db")
        snapshot = trace.metadata["prompt_versions"]
        assert snapshot["capability.inventory_analyzer"] is None
        assert snapshot["general_chat.system"] == 4
        assert snapshot["rag.qa"] == 17

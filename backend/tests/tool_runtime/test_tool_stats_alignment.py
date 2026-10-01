# -*- coding: utf-8 -*-
"""Tool 治理指标键对齐回归（2026-10-01 /tools 页行全 0 缺陷）

三段：
1. 指标键：record_tool_result 用 tool_name（@tool 函数名 = lock 键），
   空回退 capability；probe./test. 前缀（非生产键）在唯一入口丢弃。
2. 跨源合并：stats 键（指标 label）与 lock 键（@tool 函数名）相交时
   inventory 行 runtime 必须非零——本次缺陷的假一致场景（两份 mock 键
   天然一致，真键域结构性不相交），专补此合并路径。
3. 中文名：labels.py 键集与 tool_registry 完全一致（多键/少键 fail-fast），
   lock 生成器派生 display_name 且不影响 content_hash。
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.tool_runtime.models import ToolResult, ToolStatus
from backend.core.tool_runtime import metrics as tool_metrics


def _result(tool_key: str = "rag.search", status: ToolStatus = ToolStatus.SUCCESS) -> ToolResult:
    return ToolResult(status=status, tool_name=tool_key, latency_ms=5)


def _tool_samples():
    """读 agent_tool_calls_total 当前进程累计 sample，返回 {tool: value}。"""
    from backend.observability.metrics import agent_tool_calls_total

    out = {}
    for metric in agent_tool_calls_total.collect():
        for s in metric.samples:
            if s.name == "agent_tool_calls_total":
                out[s.labels["tool"]] = out.get(s.labels["tool"], 0.0) + s.value
    return out


class TestMetricToolLabel:
    def test_tool_name_used_as_label(self):
        """传入 tool_name（契约名）时，指标 label 必须用它而非 capability。"""
        tool_metrics.record_tool_result(_result("rag.search"), domain="rag",
                                        tool_name="search_knowledge_tool")
        samples = _tool_samples()
        assert samples.get("search_knowledge_tool", 0) >= 1

    def test_empty_tool_name_falls_back_to_capability(self):
        """未传 tool_name 回退 capability（历史口径兼容），不抛错。"""
        tool_metrics.record_tool_result(_result("rag.search"), domain="rag")
        samples = _tool_samples()
        assert samples.get("rag.search", 0) >= 1

    def test_probe_and_test_keys_dropped(self):
        """probe./test. 前缀键在唯一入口丢弃，不产生任何指标样本。"""
        import prometheus_client

        before = len(list(prometheus_client.REGISTRY.collect()))
        tool_metrics.record_tool_result(_result(f"probe.{'a' * 32}"), domain="eval",
                                        tool_name="search_knowledge_tool")
        tool_metrics.record_tool_result(_result("test.heavy"), domain="eval")
        after = len(list(prometheus_client.REGISTRY.collect()))
        assert before == after  # 指标族数量不变 = 没有新样本序列


class TestCrossSourceMerge:
    async def test_inventory_runtime_matches_by_lock_key(self):
        """跨源合并回归：stats 按契约键记账后，/api/admin/tools 行 runtime 非零。

        走真实端点合并路径：读仓库 lock + monkeypatch _aggregate_tool_stats
        返回同键域统计 + 免鉴权。2026-10-01 缺陷=stats 键域（capability 名）
        与 lock 键域（@tool 函数名）结构性不相交，行全 0。
        """
        from backend.app.api.routes import admin_tools

        lock = json.loads(
            (Path(admin_tools.__file__).resolve().parents[3]
             / "tool_contracts.lock.json").read_text(encoding="utf-8"))
        target = next(n for n, e in lock["tools"].items() if e.get("capabilities"))

        fake_stats = {"scope": "process", "totals": {}, "top_failed_tools": [],
                      "top_error_classes": [], "skill_failures": [],
                      "tools": [{
                          "tool": target, "domain": "rag", "calls": 3.0,
                          "success": 3.0, "failures": 0.0, "success_rate": 1.0,
                          "error_classes": {},
                      }]}

        async def _noop_auth(_request):
            return None

        with patch.object(admin_tools, "_aggregate_tool_stats", return_value=fake_stats), \
             patch.object(admin_tools, "require_admin_user", _noop_auth):
            resp = await admin_tools.tool_inventory(request=None)

        row = next(t for t in resp["tools"] if t["name"] == target)
        assert row["runtime"]["calls"] == 3.0, (
            f"lock 键 {target!r} 在 stats 键域中查不到 → 行全 0 缺陷复发"
        )
        assert row["display_name"], "inventory 行必须带中文名（display_name）"

    async def test_recorded_tool_name_is_lock_key(self):
        """端到端键口径：治理层记账的键 = lock 键域成员。

        用真实 @tool（calculate_tool）走 safe_tool_executor，断言指标
        sample 落在契约键 calculate_tool 下而非 capability 名下。
        """
        from backend.core.tool_runtime.executor import safe_tool_executor
        from backend.tools.calculator import calculate_tool

        result = await safe_tool_executor.run(
            tool_key="calc.cap",
            call=lambda: calculate_tool.invoke({"expression": "1+1"}),
            tool_name="calculate_tool",
        )
        assert result.status is ToolStatus.SUCCESS
        samples = _tool_samples()
        assert samples.get("calculate_tool", 0) >= 1
        assert "calc.cap" not in samples, "capability 键出现在指标中 = 键域不相交缺陷复发"


class TestDisplayNames:
    def test_labels_keys_match_registry(self):
        """labels.py 键集与 tool_registry 完全一致：多键（Tool 已删未清）
        与少键（新 Tool 忘登中文名）都 fail-fast。"""
        import backend.skills  # noqa: F401 触发 Skill 自注册 → 连带加载全部 Tool 模块
        import backend.tools  # noqa: F401
        from backend.tools.labels import TOOL_DISPLAY_NAMES
        from backend.tools.tool_registry import tool_registry

        registered = set(tool_registry.tool_names)
        labeled = set(TOOL_DISPLAY_NAMES)
        assert labeled - registered == set(), f"labels 多出未注册 Tool: {labeled - registered}"
        assert registered - labeled == set(), f"新 Tool 未登记中文名: {registered - labeled}"

    def test_display_name_not_in_content_hash(self):
        """display_name 只进 lock 展示，不改 content_hash（改中文名≠契约变更）。"""
        from backend.scripts.gen_tool_contract_lock import _content_hash

        base = {"args_schema": {"q": {"type": "string"}}, "capabilities": ["x.y"],
                "output_types": {}}
        entry_a = {"module": "m", "display_name": "", **base}
        entry_b = {"module": "m", "display_name": "改名后", **base}
        assert _content_hash(entry_a) == _content_hash(entry_b)

    def test_lock_file_carries_display_name(self):
        """仓库 lock 全部条目带非空 display_name（生成器收口验证）。"""
        lock_path = Path(__file__).resolve().parents[2] / "tool_contracts.lock.json"
        tools = json.loads(lock_path.read_text(encoding="utf-8"))["tools"]
        missing = [n for n, e in tools.items() if not e.get("display_name")]
        assert missing == [], f"lock 缺中文名: {missing}"

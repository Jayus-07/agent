"""L3 previous_outputs 微压缩测试（Context Budget 基础版，2026-09-22）

覆盖：
- ≤ 预算不处理（原对象返回）
- > 预算 → compact、最新结果优先完整保留、旧结果降级为摘要结构
- 总 token ≤ budget
- L1 已 truncated（context_compacted）的条目不重新拼回完整结果
"""
import pytest

import backend.config as config
from backend.context_budget.micro_compactor import compact_previous_outputs
from backend.context_budget.tool_guard import (
    guard_tool_result,
    is_compacted_preview,
)
from backend.memory.token_budget import count_tokens


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)
    monkeypatch.setattr(config, "TOOL_INLINE_MAX_TOKENS", 200)
    monkeypatch.setattr(config, "TOOL_PREVIEW_MAX_TOKENS", 64)


class TestCompact:
    def test_within_budget_untouched(self):
        po = {"1": "短输出", "2": {"k": "v"}}
        result = compact_previous_outputs(po)
        assert result is po  # 原样返回（零拷贝）

    def test_over_budget_compacted_within_limit(self):
        # 每条约 1200+ token，两条就超 1024 → 只有最新一条能完整保留
        po = {
            "1": "旧结果一" * 600,
            "2": "旧结果二" * 600,
            "3": "最新结果" * 50,
        }
        result = compact_previous_outputs(po)
        total = sum(count_tokens(str(v)) for v in result.values())
        assert total <= 1024
        # 最新（"3"）优先完整保留
        assert result["3"] == po["3"]
        # 旧的降级为摘要结构，不无声丢失
        for dep_id in ("1", "2"):
            entry = result[dep_id]
            assert isinstance(entry, dict)
            assert entry["compacted"] is True
            assert entry["step_id"] == dep_id
            assert entry["preview"]

    def test_meta_preserved_in_degraded_entries(self):
        # 2026-09-23 计数口径切换（calibrated CJK ≈0.77 token/字）：
        # 单条 ~616 token（≤1024 可完整保留），两条总量 >1024 → 旧的降级
        po = {"1": "数据" * 400, "2": "数据" * 400}
        meta = {
            "1": {"step_id": "1", "tool": "sql.query", "status": "success"},
            "2": {"step_id": "2", "tool": "rag.search", "status": "success"},
        }
        result = compact_previous_outputs(po, meta=meta)
        # "2" 是最新且单独 ≤1024 → 完整保留；"1" 降级并保留元数据
        assert result["2"] == po["2"]
        assert result["1"]["tool"] == "sql.query"
        assert result["1"]["step_id"] == "1"

    def test_l1_preview_not_reexpanded(self):
        # L1 先把超大输出压成 preview
        big = "超大工具结果" * 500
        preview = guard_tool_result(big, capability="x.y", step_id="9")
        assert is_compacted_preview(preview)
        # L3 再压缩：preview 只会保持或收缩，绝不变回完整内容
        po = {"9": preview, "8": "填充" * 1500}
        result = compact_previous_outputs(po)
        entry = result["9"]
        assert not isinstance(entry, str) or len(entry) < len(big)
        assert big not in str(entry)
        if isinstance(entry, dict):
            # 无论被 L3 再降级还是原样保留，都仍是 preview 形态
            assert entry.get("preview") is not None or is_compacted_preview(entry)

    def test_empty_and_disabled(self, monkeypatch):
        assert compact_previous_outputs({}) == {}
        monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", False)
        po = {"1": "数据" * 500}
        assert compact_previous_outputs(po) is po

    def test_newest_kept_when_single_entry_fits(self):
        # 只有最新一条时（≤1024）即使旧条目很大也保最新
        po = {"1": "旧" * 3000, "2": "新" * 100}
        result = compact_previous_outputs(po)
        assert result["2"] == po["2"]

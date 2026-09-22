"""L1 工具结果预算 Guard 测试（Context Budget 基础版，2026-09-22）

覆盖：
- 小结果不压缩 / 大结果生成 preview / preview ≤ TOOL_PREVIEW_MAX_TOKENS /
  original_tokens 正确
- 旧 Markdown Tool（str 返回）与新 JSON Tool（dict 返回）走同一 Guard
- Guard 异常 / 开关关闭时原样放行
"""
import json

import pytest

import backend.config as config
from backend.context_budget.tool_guard import (
    guard_tool_result,
    is_compacted_preview,
    serialize_for_count,
    truncate_text_to_tokens,
)
from backend.memory.token_budget import count_tokens


@pytest.fixture(autouse=True)
def _small_budget(monkeypatch):
    """收紧阈值便于测试：inline 100 / preview 30。"""
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "TOOL_INLINE_MAX_TOKENS", 100)
    monkeypatch.setattr(config, "TOOL_PREVIEW_MAX_TOKENS", 30)


def _big_text() -> str:
    return "这是一段用于测试的超长工具输出。" * 200  # 远超 100 token


class TestGuardBasics:
    def test_small_result_unchanged(self):
        out = "短结果"
        assert guard_tool_result(out, capability="x.y") is out

    def test_none_and_disabled_passthrough(self):
        assert guard_tool_result(None) is None
        monkey_none = pytest.MonkeyPatch()
        try:
            monkey_none.setattr(config, "CONTEXT_BUDGET_ENABLED", False)
            big = _big_text()
            assert guard_tool_result(big) is big
        finally:
            monkey_none.undo()

    def test_big_str_result_becomes_preview(self):
        big = _big_text()
        out = guard_tool_result(big, capability="rag.search", step_id="s1")

        assert isinstance(out, dict)
        assert is_compacted_preview(out)
        assert out["context_compacted"] is True
        assert out["type"] == "tool_result_preview"
        # original_tokens 正确（按真实 token 计数）
        assert out["original_tokens"] == count_tokens(big)
        # preview 按 token 截取，≤ TOOL_PREVIEW_MAX_TOKENS
        assert 0 < count_tokens(out["preview"]) <= 30

    def test_big_dict_result_becomes_preview(self):
        big = {"rows": [{"a": i, "b": "数据" * 20} for i in range(100)]}
        out = guard_tool_result(big, capability="sql.query", step_id="s2")
        assert is_compacted_preview(out)
        assert out["original_tokens"] == count_tokens(json.dumps(big, ensure_ascii=False))
        assert count_tokens(out["preview"]) <= 30

    def test_preview_is_token_truncated_not_char_sliced(self):
        text = "字" * 500  # 每字约 1 token
        out = guard_tool_result(text)
        # 若按 2 字符/token 截会得 60 字符；token 截取 30 个 → 接近 30 字
        assert len(out["preview"]) <= 70


class TestTruncate:
    def test_truncate_within_budget_noop(self):
        text = "短文本"
        assert truncate_text_to_tokens(text, 100) is text

    def test_truncate_respects_max_tokens(self):
        text = "上下文预算测试" * 100
        cut = truncate_text_to_tokens(text, 25)
        assert count_tokens(cut) <= 25


class TestSerialize:
    def test_serialize_variants(self):
        assert serialize_for_count(None) == ""
        assert serialize_for_count("abc") == "abc"
        assert json.loads(serialize_for_count({"k": 1})) == {"k": 1}


class TestUnifiedGuardAcrossToolStyles:
    """旧 Markdown Tool（str）与新 JSON Tool（dict）走同一 Guard —— 两条
    Skill 执行路径（governed / legacy）的成功分支都接了 _apply_tool_result_budget。"""

    @staticmethod
    def _make_state():
        return {
            "current_step_id": "s1",
            "plan": {"nodes": {"s1": {
                "capability": "test.heavy", "description": "重结果",
                "params": {}}}},
            "step_results": {},
        }

    @staticmethod
    def _make_skill(tool_return):
        from backend.skills.base import BaseSkill

        class _FakeTool:
            def invoke(self, params):
                return tool_return

        class _HeavySkill(BaseSkill):
            name = "heavy"
            capabilities = ["test.heavy"]
            description = "测试用"
            examples = [{"question": "q", "plan": {"nodes": {}, "edges": {}}}]
            params_schema = {}

            @property
            def _tool_fn(self):
                return _FakeTool()

        return _HeavySkill()

    @pytest.mark.asyncio
    async def test_markdown_tool_legacy_path_guarded(self, monkeypatch):
        import backend.skills.base as base_mod
        monkeypatch.setattr(base_mod, "_tool_runtime_enabled", lambda: False)
        skill = self._make_skill(_big_text())  # 旧式 Markdown str
        result = await skill.execute(self._make_state(), "test.heavy")
        sr = result["step_results"]["s1"]
        assert sr["status"] == "success"
        assert is_compacted_preview(sr["output"])
        assert sr["context_compacted"] is True

    @pytest.mark.asyncio
    async def test_json_tool_governed_path_guarded(self, monkeypatch):
        import backend.skills.base as base_mod
        monkeypatch.setattr(base_mod, "_tool_runtime_enabled", lambda: True)
        big_dict = {"data": ["条目" * 50 for _ in range(200)]}  # 新式 JSON dict
        skill = self._make_skill(big_dict)
        result = await skill.execute(self._make_state(), "test.heavy")
        sr = result["step_results"]["s1"]
        assert sr["status"] == "success"
        assert is_compacted_preview(sr["output"])
        assert sr["original_output_tokens"] == count_tokens(
            json.dumps(big_dict, ensure_ascii=False))

    @pytest.mark.asyncio
    async def test_small_result_untouched_through_skill(self, monkeypatch):
        import backend.skills.base as base_mod
        monkeypatch.setattr(base_mod, "_tool_runtime_enabled", lambda: False)
        skill = self._make_skill("正常小结果")
        result = await skill.execute(self._make_state(), "test.heavy")
        assert result["step_results"]["s1"]["output"] == "正常小结果"

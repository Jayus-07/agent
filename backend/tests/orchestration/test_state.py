"""P0.3 — backend/orchestration/state.py 单元测试

覆盖 _merge_step_results reducer 逻辑 + TypedDict 字段完整性。

CLAUDE.md 硬性要求：
- Planner 只输出 capability DAG，禁止调 Tool/Skill/DB
- 状态合并规则：right 覆盖 left 同 key
"""
import pytest

from backend.orchestration.state import (
    AgentState,
    StepResult,
    _merge_step_results,
)


# ==========================================================
# 1. Reducer — _merge_step_results
# ==========================================================

class TestMergeStepResults:
    def test_empty_left_returns_right_copy(self):
        """left 为空 → 直接返回 right 副本"""
        right = {"step1": {"status": "success"}}
        result = _merge_step_results({}, right)
        assert result == {"step1": {"status": "success"}}

    def test_empty_right_returns_left_copy(self):
        """right 为空 → 直接返回 left 副本"""
        left = {"step1": {"status": "success"}}
        result = _merge_step_results(left, {})
        assert result == {"step1": {"status": "success"}}

    def test_both_empty_returns_empty(self):
        result = _merge_step_results({}, {})
        assert result == {}

    def test_right_overrides_left_on_key_conflict(self):
        """right 中同 key 覆盖 left"""
        left = {"step1": {"status": "pending", "output": None}}
        right = {"step1": {"status": "success", "output": "ok"}}
        result = _merge_step_results(left, right)
        assert result["step1"]["status"] == "success"
        assert result["step1"]["output"] == "ok"

    def test_merge_preserves_unique_keys(self):
        """left 和 right 各自独有的 key 都保留"""
        left = {"step1": {"status": "success"}}
        right = {"step2": {"status": "success"}}
        result = _merge_step_results(left, right)
        assert "step1" in result
        assert "step2" in result
        assert len(result) == 2

    def test_returned_dict_is_new_instance(self):
        """返回值是新 dict — 修改不应影响 left/right"""
        left = {"a": 1}
        right = {"b": 2}
        result = _merge_step_results(left, right)
        result["c"] = 3
        assert "c" not in left
        assert "c" not in right

    def test_returned_dict_independent_of_right(self):
        """修改返回值不应影响 right"""
        left = {"a": 1}
        right = {"b": 2}
        result = _merge_step_results(left, right)
        result["b"] = 999
        assert right["b"] == 2  # right 未变

    def test_concurrent_merge_simulation(self):
        """模拟 LangGraph 并行合并两个 Worker 的结果"""
        worker_a = {"step1": {"status": "success", "output": "A"}}
        worker_b = {"step2": {"status": "success", "output": "B"}}
        merged1 = _merge_step_results(worker_a, worker_b)
        assert len(merged1) == 2
        # 第二次合并：新增 step3
        worker_c = {"step3": {"status": "success", "output": "C"}}
        merged2 = _merge_step_results(merged1, worker_c)
        assert len(merged2) == 3
        # 原值不变
        assert merged2["step1"]["output"] == "A"
        assert merged2["step2"]["output"] == "B"
        assert merged2["step3"]["output"] == "C"

    def test_merge_with_none_values(self):
        """right 含 None value 也能正确合并（覆盖 left）"""
        left = {"step1": {"status": "running", "output": "prev"}}
        right = {"step1": {"status": "failed", "output": None}}
        result = _merge_step_results(left, right)
        assert result["step1"]["output"] is None
        assert result["step1"]["status"] == "failed"


# ==========================================================
# 2. TypedDict 类型完整性 — 防止字段被误删
# ==========================================================

class TestStepResultFields:
    def test_required_fields_present(self):
        """StepResult 必须包含这些字段（防止重构误删）"""
        annotations = StepResult.__annotations__
        required = ["step_id", "capability", "description", "status",
                    "output", "error", "retries", "started_at", "finished_at",
                    "row_count", "is_empty", "error_type"]
        for field in required:
            assert field in annotations, f"StepResult missing field: {field}"

    def test_status_literal_values(self):
        """status 必须是 5 个枚举之一"""
        from typing import get_args
        ann = StepResult.__annotations__["status"]
        literal_values = get_args(ann)
        valid = {"pending", "running", "success", "failed", "skipped"}
        assert set(literal_values) == valid


class TestAgentStateFields:
    def test_required_fields_present(self):
        annotations = AgentState.__annotations__
        required = ["question", "kb_id", "plan", "step_results",
                    "current_step_id", "messages", "final_answer",
                    "alerts", "_supervisor_loop_count",
                    "_plan_critiqued", "_plan_changed", "_degraded_steps"]
        for field in required:
            assert field in annotations, f"AgentState missing field: {field}"

    def test_step_results_uses_merge_reducer(self):
        """step_results 字段必须用 _merge_step_results 作为 reducer"""
        from typing import get_type_hints
        hints = get_type_hints(AgentState, include_extras=True)
        step_results_type = hints["step_results"]
        metadata = step_results_type.__metadata__
        assert _merge_step_results in metadata

    def test_messages_uses_langgraph_add_messages(self):
        """messages 字段必须用 langgraph 的 add_messages reducer"""
        from typing import get_type_hints
        from langgraph.graph.message import add_messages
        hints = get_type_hints(AgentState, include_extras=True)
        messages_type = hints["messages"]
        assert add_messages in messages_type.__metadata__

    def test_degraded_steps_uses_or_reducer(self):
        """_degraded_steps 必须用 operator.or_ 作为 reducer（set union）"""
        import operator
        from typing import get_type_hints
        hints = get_type_hints(AgentState, include_extras=True)
        ds_type = hints["_degraded_steps"]
        assert operator.or_ in ds_type.__metadata__
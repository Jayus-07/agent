"""
state.py 测试 — 状态定义与 reducer

覆盖:
  - _merge_step_results(): 并发状态合并
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent.state import _merge_step_results


class TestMergeStepResults:
    """并发 Worker 状态合并 reducer"""

    def test_merge_two_workers(self):
        """两个 Worker 并行执行 → 合并各自的 step_results"""
        left = {
            "1": {"step_id": "1", "status": "success", "output": "data1"},
        }
        right = {
            "2": {"step_id": "2", "status": "success", "output": "data2"},
        }
        merged = _merge_step_results(left, right)
        assert "1" in merged
        assert "2" in merged
        assert merged["1"]["output"] == "data1"
        assert merged["2"]["output"] == "data2"

    def test_overwrite_same_key(self):
        """同 step_id 被 later write 覆盖（状态更新）"""
        left = {
            "1": {"step_id": "1", "status": "running", "started_at": 100.0},
        }
        right = {
            "1": {"step_id": "1", "status": "success", "output": "result",
                  "started_at": 100.0, "finished_at": 105.0},
        }
        merged = _merge_step_results(left, right)
        assert merged["1"]["status"] == "success"
        assert merged["1"]["output"] == "result"

    def test_left_empty(self):
        right = {"1": {"step_id": "1", "status": "success"}}
        merged = _merge_step_results({}, right)
        assert merged == right

    def test_right_empty(self):
        left = {"1": {"step_id": "1", "status": "success"}}
        merged = _merge_step_results(left, {})
        assert merged == left

    def test_both_empty(self):
        merged = _merge_step_results({}, {})
        assert merged == {}

    def test_merge_three_workers(self):
        """三 Worker 并发 → 链式合并"""
        w1 = {"1": {"step_id": "1", "status": "success"}}
        w2 = {"2": {"step_id": "2", "status": "running"}}
        w3 = {"3": {"step_id": "3", "status": "failed", "error": "timeout"}}

        merged = _merge_step_results(w1, w2)
        merged = _merge_step_results(merged, w3)
        assert len(merged) == 3
        assert merged["1"]["status"] == "success"
        assert merged["2"]["status"] == "running"
        assert merged["3"]["status"] == "failed"

    def test_does_not_mutate_inputs(self):
        """不修改输入参数"""
        left = {"1": {"step_id": "1", "status": "success"}}
        right = {"2": {"step_id": "2", "status": "success"}}

        left_copy = dict(left)
        right_copy = dict(right)

        _merge_step_results(left, right)

        assert left == left_copy
        assert right == right_copy

    def test_preserves_all_fields(self):
        """合并后保留 StepResult 所有字段"""
        left = {
            "1": {
                "step_id": "1",
                "capability": "query_database",
                "description": "查询数据",
                "status": "success",
                "output": "data",
                "error": None,
                "retries": 0,
                "started_at": 100.0,
                "finished_at": 105.0,
            },
        }
        right = {
            "2": {
                "step_id": "2",
                "capability": "search_knowledge",
                "status": "running",
                "started_at": 102.0,
            },
        }
        merged = _merge_step_results(left, right)
        assert merged["1"]["capability"] == "query_database"
        assert merged["1"]["finished_at"] == 105.0
        assert merged["2"]["capability"] == "search_knowledge"

    def test_none_input(self):
        """None 输入视同空 dict"""
        merged = _merge_step_results(None, {"1": {"step_id": "1", "status": "success"}})
        assert "1" in merged

        merged2 = _merge_step_results({"1": {"step_id": "1", "status": "success"}}, None)
        assert "1" in merged2

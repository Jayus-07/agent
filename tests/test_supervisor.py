"""
supervisor.py 测试 — 调度逻辑（纯逻辑，不依赖 LLM）

覆盖:
  - supervisor_node(): 依赖检查、步骤状态转换
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent.supervisor import supervisor_node


# ============================================================
# supervisor_node 测试
# ============================================================

class TestSupervisorNode:
    """调度节点逻辑"""

    def test_empty_plan_returns_all_done(self):
        state = {
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {},
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True

    def test_dependency_met_dispatches_step(self):
        """无依赖步骤 → 就绪派发"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询数据", "params": {}},
                },
                "edges": {},
            },
            "step_results": {},
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is False
        assert len(result["_ready_dispatch"]) == 1
        assert result["_ready_dispatch"][0]["step_id"] == "1"
        # 步骤设为 running
        assert result["step_results"]["1"]["status"] == "running"

    def test_dependency_not_met_waits(self):
        """依赖未满足 → 不派发"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询", "params": {}},
                    "2": {"step_id": "2", "capability": "generate_report",
                          "description": "报告", "params": {}},
                },
                "edges": {"2": ["1"]},  # step 2 依赖 step 1
            },
            "step_results": {},
        }
        result = supervisor_node(state)
        # step 1 无依赖 → 就绪
        ready_ids = [r["step_id"] for r in result["_ready_dispatch"]]
        assert "1" in ready_ids
        assert "2" not in ready_ids  # 依赖未满足

    def test_dep_failed_skips_step(self):
        """依赖失败 → 步骤跳过"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询", "params": {}},
                    "2": {"step_id": "2", "capability": "generate_report",
                          "description": "报告", "params": {}},
                },
                "edges": {"2": ["1"]},
            },
            "step_results": {
                "1": {"step_id": "1", "capability": "query_database",
                      "status": "failed", "error": "DB down"},
            },
        }
        result = supervisor_node(state)
        assert result["step_results"]["2"]["status"] == "skipped"

    def test_all_steps_success_triggers_done(self):
        """全部步骤成功 → _all_steps_done=True"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询", "params": {}},
                },
                "edges": {},
            },
            "step_results": {
                "1": {"step_id": "1", "capability": "query_database",
                      "status": "success", "output": "data"},
            },
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True

    def test_running_step_waits(self):
        """有运行中的步骤 → 等待"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询", "params": {}},
                },
                "edges": {},
            },
            "step_results": {
                "1": {"step_id": "1", "capability": "query_database",
                      "status": "running", "started_at": 1000.0},
            },
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is False
        assert len(result["_ready_dispatch"]) == 0

    def test_invalid_capability_fails_step(self):
        """未注册的 capability → 直接标记失败"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "unknown_cap",
                          "description": "未知能力", "params": {}},
                },
                "edges": {},
            },
            "step_results": {},
        }
        result = supervisor_node(state)
        assert result["step_results"]["1"]["status"] == "failed"
        assert "未注册" in result["step_results"]["1"]["error"]

    def test_mixed_status_count(self):
        """混合状态：部分成功 + 部分失败 → 全部完成"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询1", "params": {}},
                    "2": {"step_id": "2", "capability": "query_database",
                          "description": "查询2", "params": {}},
                },
                "edges": {},
            },
            "step_results": {
                "1": {"step_id": "1", "capability": "query_database",
                      "status": "success", "output": "ok"},
                "2": {"step_id": "2", "capability": "query_database",
                      "status": "failed", "error": "timeout"},
            },
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True

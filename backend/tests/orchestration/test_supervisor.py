"""P0.3 — backend/orchestration/supervisor/scheduler.py 单元测试

覆盖 supervisor_node 的核心路由决策 + route_after_supervisor 的 Send API。

CLAUDE.md 核心约束：
- Planner 只输出 capability DAG
- 路由决策必须正确（依赖失败 → skip / capability 无效 → failed / 超过最大循环 → 强制终止）
"""
import time
import pytest
from unittest.mock import patch, MagicMock
from langgraph.types import Send

from backend.orchestration.supervisor.scheduler import (
    supervisor_node,
    route_after_supervisor,
    MAX_SUPERVISOR_LOOPS,
)


# ==========================================================
# 1. supervisor_node — 空 plan
# ==========================================================

class TestSupervisorEmptyPlan:
    def test_empty_plan_completes_immediately(self):
        """plan 是空 dict → 立即标记完成，不 dispatch"""
        state = {"plan": {}, "step_results": {}, "_supervisor_loop_count": 0}
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True
        assert result["_ready_dispatch"] == []
        assert result["_supervisor_loop_count"] == 0  # 不递增（空 plan 提前 return）

    def test_no_nodes_no_dispatch(self):
        """plan 有 edges 但 nodes 为空 → 立即完成"""
        state = {
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {}, "_supervisor_loop_count": 0,
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True
        assert result["_ready_dispatch"] == []

    def test_missing_plan_treated_as_empty(self):
        """state 没 plan 字段 → 不崩，按空 plan 处理"""
        state = {"step_results": {}, "_supervisor_loop_count": 0}
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True


# ==========================================================
# 2. supervisor_node — 依赖关系
# ==========================================================

class TestSupervisorDependencies:
    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_dependency_not_met_no_dispatch(self, mock_reg, mock_degrade):
        """依赖步骤还在 pending → 当前 step 不 dispatch"""
        # step2 依赖 step1，step1 还 pending
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {
                "nodes": {
                    "1": {"capability": "sql.query"},
                    "2": {"capability": "rag.search"},
                },
                "edges": {"2": ["1"]},  # step2 依赖 step1
            },
            "step_results": {},  # 都 pending
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        # step1 应该 dispatch（无依赖），step2 不 dispatch（依赖未满足）
        assert len(result["_ready_dispatch"]) == 1
        assert result["_ready_dispatch"][0]["step_id"] == "1"

    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_dependency_met_dispatches(self, mock_reg, mock_degrade):
        """依赖步骤 success → 当前 step dispatch"""
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {
                "nodes": {
                    "1": {"capability": "sql.query"},
                    "2": {"capability": "rag.search"},
                },
                "edges": {"2": ["1"]},
            },
            "step_results": {
                "1": {"status": "success", "output": "data"},
            },
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        # step1 已 success（不重 dispatch），step2 应该 dispatch
        dispatched = [d["step_id"] for d in result["_ready_dispatch"]]
        assert "2" in dispatched
        assert "1" not in dispatched

    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_dependency_failed_marks_skipped(self, mock_reg, mock_degrade):
        """依赖步骤 failed → 当前 step status=skipped，不 dispatch"""
        mock_reg.get_worker.return_value = "sql_skill"
        # execute_degradation 真实契约：透传 new_results + 返回 ready 列表 + 降级 set
        def passthrough(nodes, edges, new_results, ready_dispatch, degraded_steps, question):
            return new_results, ready_dispatch, degraded_steps
        mock_degrade.side_effect = passthrough
        state = {
            "plan": {
                "nodes": {
                    "1": {"capability": "sql.query"},
                    "2": {"capability": "rag.search"},
                },
                "edges": {"2": ["1"]},
            },
            "step_results": {
                "1": {"status": "failed", "error": "DB down"},
            },
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        # step2 应该被标记为 skipped
        assert result["step_results"]["2"]["status"] == "skipped"
        assert "2" not in [d["step_id"] for d in result["_ready_dispatch"]]


# ==========================================================
# 3. supervisor_node — capability 校验
# ==========================================================

class TestSupervisorCapability:
    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_unregistered_capability_marks_failed(self, mock_reg, mock_degrade):
        """capability 未注册 → step status=failed，不 dispatch"""
        mock_reg.get_worker.return_value = None  # 返回 None 表示未注册
        def passthrough(nodes, edges, new_results, ready_dispatch, degraded_steps, question):
            return new_results, ready_dispatch, degraded_steps
        mock_degrade.side_effect = passthrough
        state = {
            "plan": {
                "nodes": {"1": {"capability": "unknown.thing"}},
                "edges": {},
            },
            "step_results": {},
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        assert result["step_results"]["1"]["status"] == "failed"
        assert "未注册的 capability" in result["step_results"]["1"]["error"]
        assert result["_ready_dispatch"] == []

    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_registered_capability_dispatches(self, mock_reg, mock_degrade):
        """capability 已注册 → 标记 running + dispatch"""
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {
                "nodes": {"1": {"capability": "sql.query"}},
                "edges": {},
            },
            "step_results": {},
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        assert result["step_results"]["1"]["status"] == "running"
        assert len(result["_ready_dispatch"]) == 1
        assert result["_ready_dispatch"][0]["worker"] == "sql_skill"
        assert result["_ready_dispatch"][0]["step_id"] == "1"


# ==========================================================
# 4. supervisor_node — 循环控制
# ==========================================================

class TestSupervisorLoopControl:
    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_max_loop_count_force_fail(self, mock_reg, mock_degrade):
        """达到 MAX_SUPERVISOR_LOOPS → 强制失败所有 pending step"""
        mock_reg.get_worker.return_value = "sql_skill"
        # 关键：step_results 必须显式标记 pending，否则 supervisor 看不到需要 force_fail 的目标
        state = {
            "plan": {
                "nodes": {
                    "1": {"capability": "sql.query"},
                    "2": {"capability": "rag.search"},
                },
                "edges": {},
            },
            "step_results": {
                "1": {"status": "pending"},
                "2": {"status": "pending"},
            },
            "_supervisor_loop_count": MAX_SUPERVISOR_LOOPS,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True
        # 所有 pending step 应该被强制 failed
        for sid in ["1", "2"]:
            assert result["step_results"][sid]["status"] == "failed"
            assert "超出最大调度轮次" in result["step_results"][sid]["error"]
            assert result["step_results"][sid]["error_type"] == "timeout"
        # 应该有 SUPERVISOR_MAX_LOOP 告警（PlanAlert 是 dataclass，用属性访问）
        assert any(a.code == "SUPERVISOR_MAX_LOOP" for a in result.get("alerts", []))

    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_loop_count_increments(self, mock_reg, mock_degrade):
        """正常情况下 _supervisor_loop_count 递增"""
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {"nodes": {"1": {"capability": "sql.query"}}, "edges": {}},
            "step_results": {},
            "_supervisor_loop_count": 5,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        assert result["_supervisor_loop_count"] == 6

    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_loop_count_resets_at_max(self, mock_reg, mock_degrade):
        """max 时 _supervisor_loop_count 不递增（直接返回当前值）"""
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {"nodes": {"1": {"capability": "sql.query"}}, "edges": {}},
            "step_results": {},
            "_supervisor_loop_count": MAX_SUPERVISOR_LOOPS,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        assert result["_supervisor_loop_count"] == MAX_SUPERVISOR_LOOPS


# ==========================================================
# 5. supervisor_node — 运行中 / 完成判定
# ==========================================================

class TestSupervisorStateTransitions:
    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_running_step_not_redispatched(self, mock_reg, mock_degrade):
        """status=running 的 step 不再重新 dispatch"""
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {"nodes": {"1": {"capability": "sql.query"}}, "edges": {}},
            "step_results": {
                "1": {"status": "running", "started_at": time.time()},
            },
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        # step1 已经在跑，不应该被再次 dispatch
        assert result["_ready_dispatch"] == []
        # 也不应该被标 failed
        assert result["step_results"]["1"]["status"] == "running"

    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_running_step_stale_marked_failed(self, mock_reg, mock_degrade):
        """running 步骤超过 RUNNING_STALE_TIMEOUT_SEC → 自动 failed（防死锁）"""
        # mock execute_degradation 返回原 step_results，不被覆盖
        def fake_degrade(nodes, edges, new_results, ready, degraded, q):
            return new_results, ready, degraded
        mock_degrade.side_effect = fake_degrade
        stale_started = time.time() - 600  # 10 分钟前，远超 300s 阈值
        state = {
            "plan": {"nodes": {"1": {"capability": "sql.query"}}, "edges": {}},
            "step_results": {
                "1": {"status": "running", "started_at": stale_started},
            },
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        assert result["step_results"]["1"]["status"] == "failed"
        assert result["step_results"]["1"]["error_type"] == "timeout"
        assert result["_all_steps_done"] is True

    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_all_success_returns_done(self, mock_reg, mock_degrade):
        """所有 step success → _all_steps_done=True"""
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {"nodes": {"1": {"capability": "sql.query"}}, "edges": {}},
            "step_results": {
                "1": {"status": "success", "output": "ok"},
            },
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True
        assert result["_ready_dispatch"] == []

    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_running_no_ready_returns_waiting(self, mock_reg, mock_degrade):
        """有 step 在跑、无 ready dispatch → 等待（_all_steps_done=False）"""
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {
                "nodes": {"1": {"capability": "sql.query"}, "2": {"capability": "rag.search"}},
                "edges": {},
            },
            "step_results": {
                "1": {"status": "running"},
            },
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        # step2 可以 dispatch（无依赖）
        # 实际上这里 step2 应该被 dispatch，step1 已在跑
        assert result["_all_steps_done"] is False
        assert any(d["step_id"] == "2" for d in result["_ready_dispatch"])


# ==========================================================
# 6. supervisor_node — degradation 集成
# ==========================================================

class TestSupervisorDegradation:
    @patch("backend.orchestration.supervisor.scheduler.execute_degradation")
    @patch("backend.orchestration.supervisor.scheduler.tool_registry")
    def test_degradation_called_when_no_ready_and_done(self, mock_reg, mock_degrade):
        """所有 step 都结束 + 无 ready → 调 execute_degradation 检查降级机会"""
        # mock execute_degradation 不返回任何 ready
        mock_degrade.return_value = ({"1": {"status": "success"}}, [], set())
        mock_reg.get_worker.return_value = "sql_skill"
        mock_degrade.return_value = ({}, [], set())
        state = {
            "plan": {"nodes": {"1": {"capability": "sql.query"}}, "edges": {}},
            "step_results": {"1": {"status": "success"}},
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = supervisor_node(state)
        # 验证 execute_degradation 被调用
        mock_degrade.assert_called_once()


# ==========================================================
# 7. route_after_supervisor
# ==========================================================

class TestRouteAfterSupervisor:
    def test_no_ready_returns_reporter_string(self):
        """无 ready → 返回 'reporter' 进入汇总"""
        state = {"_ready_dispatch": [], "question": "q"}
        result = route_after_supervisor(state)
        assert result == "reporter"

    def test_has_ready_returns_send_list(self):
        """有 ready → 返回 list[Send]"""
        state = {
            "_ready_dispatch": [{"worker": "sql_skill", "step_id": "1"}],
            "question": "test",
            "kb_id": "default",
            "plan": {},
            "step_results": {},
            "messages": [],
            "final_answer": "",
            "alerts": [],
            "_supervisor_loop_count": 1,
            "_degraded_steps": set(),
        }
        result = route_after_supervisor(state)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Send)
        assert result[0].node == "sql_skill"

    def test_send_payload_contains_required_fields(self):
        """Send 的 payload 必须包含 state 关键字段"""
        state = {
            "_ready_dispatch": [{"worker": "sql_skill", "step_id": "5"}],
            "question": "what is X?",
            "kb_id": "policy",
            "plan": {"nodes": {"5": {}}},
            "step_results": {"prev": {"status": "success"}},
            "messages": [{"role": "user", "content": "x"}],
            "final_answer": "",
            "alerts": [{"code": "INFO"}],
            "_supervisor_loop_count": 3,
            "_degraded_steps": {"step1", "step2"},
        }
        result = route_after_supervisor(state)
        assert len(result) == 1
        payload = result[0].arg
        # 注意：supervisor 把 step_id 复制成 current_step_id（LangGraph Send 约定）
        assert payload["current_step_id"] == "5"
        assert payload["question"] == "what is X?"
        assert payload["kb_id"] == "policy"
        assert payload["_supervisor_loop_count"] == 3
        # _degraded_steps 应该转成 frozenset
        assert isinstance(payload["_degraded_steps"], frozenset)
        assert "step1" in payload["_degraded_steps"]

    def test_multiple_ready_returns_multiple_sends(self):
        """多个 ready → 多个 Send（LangGraph 自动并行）"""
        state = {
            "_ready_dispatch": [
                {"worker": "sql_skill", "step_id": "1"},
                {"worker": "rag_skill", "step_id": "2"},
                {"worker": "report_skill", "step_id": "3"},
            ],
            "question": "q", "kb_id": "default", "plan": {},
            "step_results": {}, "messages": [], "final_answer": "",
            "alerts": [], "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
        }
        result = route_after_supervisor(state)
        assert len(result) == 3
        workers = {s.node for s in result}
        assert workers == {"sql_skill", "rag_skill", "report_skill"}

    def test_send_with_no_degraded_steps_uses_empty_frozenset(self):
        """_degraded_steps 为 None 时也安全"""
        state = {
            "_ready_dispatch": [{"worker": "sql_skill", "step_id": "1"}],
            "question": "q", "kb_id": "default", "plan": {},
            "step_results": {}, "messages": [], "final_answer": "",
            "alerts": [], "_supervisor_loop_count": 0,
            "_degraded_steps": None,  # None 值
        }
        result = route_after_supervisor(state)
        assert len(result) == 1
        assert result[0].arg["_degraded_steps"] == frozenset()


# ==========================================================
# 8. MAX_SUPERVISOR_LOOPS 常量
# ==========================================================

class TestConstants:
    def test_max_supervisor_loops_default(self):
        """默认最大循环次数 = 10（防止单步卡死拖垮整张图）"""
        assert MAX_SUPERVISOR_LOOPS == 10
        assert isinstance(MAX_SUPERVISOR_LOOPS, int)
"""test_direct_executor_async_boundary.py — STOP D：DirectExecutor async 边界

2026-10-07 全项目 Tool Failure Semantics 收口（任务 §八/§二十二）：
- 常态安全性证据：两个同步节点（skill_executor / workflow_executor）由
  LangGraph 放线程池执行，线程内 asyncio.run 合法；
- 防御边界：万一在 loop 线程上被直接调用，`_run_coro_sync` 专用线程隔离，
  不抛「asyncio.run() cannot be called from a running event loop」，
  不嵌套、不用 nest_asyncio；
- 失败状态保留：skill 失败时 step_results.status=failed 贯通到
  final_answer（空串交 reporter），不得伪装成功。
"""
from __future__ import annotations

import asyncio
import threading

from unittest.mock import patch as mp

from backend.orchestration.graph.direct_executor import (
    _run_coro_sync,
    skill_executor_node,
)


def _fake_skill_nodes(failed: bool = False):
    """构造 tool_registry.get_skill_nodes() 的替身：一个 async skill 节点。

    failed=True 时 skill 内部走失败 step_results（模拟 BaseSkill 失败贯通）；
    同时记录执行线程，用于证明协程不在 loop 线程上跑。
    """

    async def fake_sql_skill(state: dict) -> dict:
        seen["thread"] = threading.get_ident()
        step_id = state["current_step_id"]
        sr = (
            {
                "status": "failed",
                "output": None,
                "error": "服务暂时不可用",
                "error_type": "network",
                "tool_status": "unavailable",
            }
            if failed
            else {"status": "success", "output": {"columns": ["x"], "rows": [[1]]}}
        )
        return {"step_results": {step_id: sr}}

    seen: dict = {"node": fake_sql_skill}
    return {"sql_skill": fake_sql_skill}, seen


def _direct_state() -> dict:
    return {
        "question": "查一下今天的销售",
        "session_id": "s-stopd",
        "route_decision": {
            "candidates": [{"name": "sql.query", "score": 0.9}],
        },
    }


class TestDirectExecutorAsyncBoundary:

    def test_direct_executor_sync_invoke(self):
        """D-常态：无 loop 线程直接调用（LangGraph 同步节点线程形态）→ 正常执行"""
        nodes, _seen = _fake_skill_nodes()
        with mp("backend.orchestration.graph.direct_executor.tool_registry") as reg:
            reg.get_skill_nodes.return_value = nodes
            reg.get_node.return_value = "sql_skill"
            result = skill_executor_node(_direct_state())
        assert result["executor_mode"] == "direct"
        step = result["step_results"]["direct_1"]
        assert step["status"] == "success"
        assert result["final_answer"], "成功结果必须产出 final_answer"

    def test_direct_executor_async_invoke_no_nested_loop(self):
        """D1：async 调用方（LangGraph ainvoke 把同步节点丢线程池的形态，
        即 asyncio.to_thread）→ 不得出现嵌套 asyncio.run 报错"""
        nodes, _seen = _fake_skill_nodes()

        async def scenario():
            # 模拟 ainvoke：当前线程跑着事件循环，同步节点在线程池里执行
            with mp("backend.orchestration.graph.direct_executor.tool_registry") as reg:
                reg.get_skill_nodes.return_value = nodes
                reg.get_node.return_value = "sql_skill"
                return await asyncio.to_thread(skill_executor_node, _direct_state())

        result = asyncio.run(scenario())
        assert result["step_results"]["direct_1"]["status"] == "success"

    def test_direct_executor_loop_thread_call_isolated(self):
        """D-防御：在 loop 线程上直接调用同步节点（误接线形态）→
        `_run_coro_sync` 专用线程隔离执行，不抛 nested-loop RuntimeError，
        且协程实际运行在非 loop 线程"""
        nodes, seen = _fake_skill_nodes()
        probe: dict = {}

        async def scenario():
            probe["loop_thread"] = threading.get_ident()
            with mp("backend.orchestration.graph.direct_executor.tool_registry") as reg:
                reg.get_skill_nodes.return_value = nodes
                reg.get_node.return_value = "sql_skill"
                # 关键：不经 to_thread，直接在 loop 线程上调同步节点
                return skill_executor_node(_direct_state())

        result = asyncio.run(scenario())
        assert result["step_results"]["direct_1"]["status"] == "success"
        assert seen["thread"] != probe["loop_thread"], (
            "协程必须在专用工作线程执行，不得占用/嵌套调用线程的事件循环"
        )

    def test_run_coro_sync_returns_value_without_loop(self):
        """无 loop 上下文下 `_run_coro_sync` 等价 asyncio.run"""
        async def coro():
            return 42

        assert _run_coro_sync(coro()) == 42

    def test_direct_executor_tool_failure_status_preserved(self):
        """D4：skill 失败 → step_results.status=failed + final_answer 置空
        （交 reporter 降级提示），不得伪装成成功"""
        nodes, _seen = _fake_skill_nodes(failed=True)
        with mp("backend.orchestration.graph.direct_executor.tool_registry") as reg:
            reg.get_skill_nodes.return_value = nodes
            reg.get_node.return_value = "sql_skill"
            result = skill_executor_node(_direct_state())
        step = result["step_results"]["direct_1"]
        assert step["status"] == "failed"
        assert step["error"] == "服务暂时不可用"
        assert result["final_answer"] == "", "失败步骤不得产出 truthy final_answer"

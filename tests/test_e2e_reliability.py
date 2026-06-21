"""
端到端集成测试 — Agent 可靠性工程全链路

覆盖:
  - 图编译成功（含 Critique 节点）
  - Planner → Critique → Supervisor → Reporter 全链路
  - alerts 在 state 中的传递
  - StepResult 新字段 (row_count, is_empty, error_type) 与 _is_step_successful 集成
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch


# ============================================================
# 测试 1: 图编译验证
# ============================================================

def test_full_graph_compilation():
    """验证图编译成功（含 Critique 节点）"""
    from multi_agent.graph import build_graph

    graph = build_graph()
    assert graph is not None

    # 验证图包含所有预期节点
    nodes = graph.get_graph().nodes
    expected_nodes = {
        "planner", "critique", "supervisor",
        "sql_worker", "rag_worker", "report_worker",
        "reporter",
    }
    # nodes 可能是 dict_keys 或包含 __start__/__end__ 的集合
    node_names = set()
    for n in nodes:
        name = n if isinstance(n, str) else getattr(n, "name", str(n))
        node_names.add(name)
    for expected in expected_nodes:
        assert expected in node_names, f"图中缺少节点: {expected}"


# ============================================================
# 测试 2: Planner → Critique 流程
# ============================================================

@patch("multi_agent.workers.rag_worker.search_knowledge_tool")
@patch("multi_agent.workers.sql_worker.sql_query_tool")
@patch("multi_agent.reporter.llm")
@patch("multi_agent.critique.llm")
@patch("multi_agent.planner.llm")
def test_planner_to_critique_flow(
    mock_planner_llm,
    mock_critique_llm,
    mock_reporter_llm,
    mock_sql_tool,
    mock_rag_tool,
):
    """
    端到端：Planner → Critique → Supervisor → Reporter
    模拟 Planner 输出单步骤计划，Critique 跳过（单步），
    Worker 返回模拟数据，Reporter 汇总。
    """
    from multi_agent.graph import build_graph

    # Mock Planner: 输出 knowledge 检索计划
    planner_output = {
        "nodes": {
            "1": {
                "step_id": "1",
                "capability": "search_knowledge",
                "description": "检索请假流程",
                "params": {"question": "请假流程是什么"},
            },
        },
        "edges": {},
    }
    mock_planner_llm.invoke.return_value = MagicMock(
        content=json.dumps(planner_output, ensure_ascii=False)
    )

    # Mock Critique: 单步骤计划不触发 Critique LLM（node_count <= 1 跳过）
    # 这里设置 mock 以防万一
    mock_critique_llm.invoke.return_value = MagicMock(
        content=json.dumps(planner_output, ensure_ascii=False)
    )

    # Mock Worker tool: 返回模拟 RAG 结果
    mock_rag_tool.invoke.return_value = (
        "根据公司制度，员工请假流程如下：\n\n"
        "1. 提前至少1天在OA系统提交请假申请\n"
        "2. 部门主管审批（3天以内）\n"
        "3. HR备案（超过3天需要HR审批）\n\n"
        "请假类型包括：年假、事假、病假、婚假、产假等。"
    )

    # Mock Reporter LLM: 返回汇总
    mock_reporter_llm.invoke.return_value = MagicMock(
        content="## 请假流程汇总\n\n根据公司制度，请假需提前1天申请..."
    )

    graph = build_graph()
    initial_state = {
        "question": "请假流程是什么",
        "kb_id": "default",
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
        "current_step_id": None,
        "messages": [],
        "final_answer": "",
        "alerts": [],
        "_supervisor_loop_count": 0,
        "_plan_critiqued": False,
        "_plan_changed": False,
    }

    result = asyncio.run(graph.ainvoke(initial_state, config={"recursion_limit": 50}))
    assert result is not None
    assert "final_answer" in result
    # 验证流程走完：plan 应该有节点，step_results 应该有结果
    plan = result.get("plan", {})
    assert len(plan.get("nodes", {})) > 0, "计划应包含至少1个步骤"
    step_results = result.get("step_results", {})
    assert len(step_results) > 0, "应有至少1个步骤执行结果"


# ============================================================
# 测试 3: alerts 流在 state 中传递
# ============================================================

def test_alerts_flow_in_state():
    """验证 alerts 在 state 中正确传递"""
    from multi_agent.alerts import make_alert

    # 创建告警
    alert = make_alert("DEGRADATION_TRIGGER", {"from": "sql", "to": "rag"})
    assert alert.code == "DEGRADATION_TRIGGER"
    assert alert.level == "info"
    assert alert.detail["from"] == "sql"
    assert alert.detail["to"] == "rag"

    # 转为 dict 放入 state（模拟 SSE 流输出格式）
    alert_dict = {
        "timestamp": alert.timestamp,
        "level": alert.level,
        "code": alert.code,
        "message": alert.message,
        "detail": alert.detail,
    }

    # 模拟 state.alerts 列表
    state_alerts = [alert_dict]
    assert len(state_alerts) == 1
    assert state_alerts[0]["code"] == "DEGRADATION_TRIGGER"
    assert state_alerts[0]["level"] == "info"
    assert "from" in state_alerts[0]["detail"]

    # 添加多个告警
    alert2 = make_alert("PLAN_EMPTY", {"question": "测试"})
    alert2_dict = {
        "timestamp": alert2.timestamp,
        "level": alert2.level,
        "code": alert2.code,
        "message": alert2.message,
        "detail": alert2.detail,
    }
    state_alerts.append(alert2_dict)
    assert len(state_alerts) == 2
    assert state_alerts[1]["code"] == "PLAN_EMPTY"
    assert state_alerts[1]["level"] == "warn"


# ============================================================
# 测试 4: StepResult 新字段与 _is_step_successful 集成
# ============================================================

def test_new_step_result_fields_integration():
    """验证新字段 (row_count, is_empty, error_type) 在整个流程中可用"""
    from multi_agent.state import StepResult
    from multi_agent.reporter import _is_step_successful

    # —— 正常 SQL 结果 ——
    sr: StepResult = {
        "step_id": "sql_1",
        "capability": "query_database",
        "description": "SQL查询",
        "status": "success",
        "output": "查询结果：共找到5条记录，包含员工姓名、部门和职位信息",
        "row_count": 5,
        "is_empty": False,
        "error_type": None,
    }
    assert sr["row_count"] == 5
    assert not sr["is_empty"]
    assert sr["error_type"] is None
    assert _is_step_successful(sr) is True

    # —— 空 SQL 结果 ——
    sr_empty: StepResult = {
        "step_id": "sql_2",
        "capability": "query_database",
        "description": "SQL查询空结果",
        "status": "success",
        "output": "无结果",
        "row_count": 0,
        "is_empty": True,
        "error_type": None,
    }
    assert sr_empty["row_count"] == 0
    assert sr_empty["is_empty"] is True
    assert _is_step_successful(sr_empty) is False

    # —— 失败步骤（有 error_type） ——
    sr_failed: StepResult = {
        "step_id": "sql_3",
        "capability": "query_database",
        "description": "SQL查询超时",
        "status": "failed",
        "output": "",
        "row_count": None,
        "is_empty": None,
        "error_type": "timeout",
    }
    assert sr_failed["status"] == "failed"
    assert sr_failed["error_type"] == "timeout"
    assert _is_step_successful(sr_failed) is False

    # —— 短输出被视为无效 ——
    sr_short: StepResult = {
        "step_id": "rag_1",
        "capability": "search_knowledge",
        "description": "知识库检索",
        "status": "success",
        "output": "无",  # 太短
        "row_count": None,
        "is_empty": False,
        "error_type": None,
    }
    assert _is_step_successful(sr_short) is False

"""tests for multi_agent.supervisor — 循环上限 + 结构化降级"""

from multi_agent.supervisor import supervisor_node, MAX_SUPERVISOR_LOOPS


def test_max_loop_constant():
    """循环上限常量存在且合理"""
    assert MAX_SUPERVISOR_LOOPS == 10


def test_max_loop_forced_termination():
    """达到循环上限时强制终止"""
    state = {
        "question": "测试",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "检索",
                    "params": {"question": "测试"},
                },
            },
            "edges": {},
        },
        "step_results": {
            "1": {
                "step_id": "1",
                "status": "running",  # 假死状态
            }
        },
        "_supervisor_loop_count": MAX_SUPERVISOR_LOOPS,  # 已达到上限
        "alerts": [],
    }

    result = supervisor_node(state)
    assert result["_all_steps_done"] is True
    # 所有 running 的步骤应被标记为 failed
    assert result["step_results"]["1"]["status"] == "failed"
    assert "超出最大调度轮次" in result["step_results"]["1"].get("error", "")


def test_normal_loop_count_increment():
    """正常调度时循环计数递增"""
    state = {
        "question": "测试",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "检索",
                    "params": {"question": "测试"},
                },
            },
            "edges": {},
        },
        "step_results": {},
        "_supervisor_loop_count": 3,
        "alerts": [],
    }

    result = supervisor_node(state)
    # 应该派发 step 1
    assert len(result["_ready_dispatch"]) == 1
    assert result["_supervisor_loop_count"] == 4


def test_dependency_failed_triggers_skip():
    """前置步骤失败 → 后续步骤跳过"""
    state = {
        "question": "测试",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "query_database",
                    "description": "SQL 查询",
                    "params": {"question": "查询"},
                },
                "2": {
                    "step_id": "2",
                    "capability": "generate_report",
                    "description": "生成报告",
                    "params": {"question": "生成报告"},
                },
            },
            "edges": {"2": ["1"]},  # 2 依赖 1
        },
        "step_results": {
            "1": {
                "step_id": "1",
                "capability": "query_database",
                "status": "failed",
                "error": "查询失败",
            }
        },
        "_supervisor_loop_count": 0,
        "alerts": [],
    }

    result = supervisor_node(state)
    # step 2 应被跳过
    sr2 = result["step_results"].get("2", {})
    assert sr2.get("status") == "skipped"
    assert "前置步骤执行失败" in sr2.get("error", "")


def test_empty_plan_immediate_done():
    """空计划立即完成"""
    state = {
        "question": "测试",
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
        "_supervisor_loop_count": 0,
        "alerts": [],
    }

    result = supervisor_node(state)
    assert result["_all_steps_done"] is True
    assert result["_ready_dispatch"] == []

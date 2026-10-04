"""需求理解与 SQL Tool 动态事件测试。"""

from backend.orchestration.graph.events import stream_node_events


def _payload(step_result, include_output=True):
    return {"status": step_result.get("status"), "description": step_result.get("description", "")}


def _log(node, step_id, status, desc, step_result):
    return {
        "event": "log",
        "data": {
            "node": node,
            "step_id": step_id,
            "message": f"{status}: {desc}",
            "payload": {"status": status},
        },
    }


def test_router_emits_structured_understanding_log():
    events = list(stream_node_events(
        "router",
        {"query_understanding": {
            "intent": "sql_analysis",
            "domain": "sql",
            "query_type": "analysis",
            "complexity": "simple",
            "confidence": 0.9,
        }},
        set(), _payload, _log,
    ))

    assert len(events) == 1
    assert events[0]["event"] == "log"
    assert events[0]["data"]["payload"]["phase"] == "understanding"
    assert events[0]["data"]["payload"]["intent"] == "sql_analysis"


def test_sql_skill_result_marks_tool_phase():
    events = list(stream_node_events(
        "sql_skill",
        {"step_results": {"1": {
            "status": "success",
            "capability": "sql.query",
            "description": "查询库存",
            "output": {"sql": "SELECT 1", "row_count": 1},
        }}},
        {"sql_skill"}, _payload, _log,
    ))

    assert events[0]["data"]["payload"]["phase"] == "tool_result"
    assert events[0]["data"]["payload"]["tool"] == "sql.query"

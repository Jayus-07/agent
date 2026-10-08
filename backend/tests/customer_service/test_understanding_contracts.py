"""Understanding 输出契约只暴露低基数 TaskPlan 追踪字段。"""
from __future__ import annotations

from backend.customer_service.understanding.contracts import (
    CSUnderstandingResult,
    CSUnderstandingSource,
)


def test_understanding_result_tracks_plan_without_exposing_candidate_values():
    result = CSUnderstandingResult(
        source=CSUnderstandingSource.RULE,
        task_plan_candidate={
            "schema_version": 1,
            "primary_intent": "t_logistics",
            "tasks": [{"task_id": "q1", "capability": "query_logistics"}],
        },
        task_plan_source="rule_candidate",
    )

    trace = result.to_trace_fields()

    assert trace["cs_task_plan_source"] == "rule_candidate"
    assert trace["cs_task_count"] == 1
    assert "task_plan_candidate" not in trace
    assert "query_logistics" not in str(trace)

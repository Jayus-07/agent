# -*- coding: utf-8 -*-
"""Workflow 直调 Tool 的 Trace 归因测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.observability.tracer import trace_collector
from backend.orchestration.workflow.skill_adapter import call_sql


@pytest.fixture(autouse=True)
def _trace_isolation():
    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


@pytest.mark.asyncio
async def test_workflow_direct_sql_creates_canonical_tool_span(monkeypatch):
    trace = trace_collector.start("workflow 查询订单", session_id="workflow-1", workflow_name="workflow")
    fake_tool = MagicMock()
    fake_tool.ainvoke = AsyncMock(
        return_value='{"status": "success", "data": {"rows": [1]}}',
    )
    monkeypatch.setattr("backend.orchestration.tools.execute_sql_tool", fake_tool)

    result = await call_sql({"query": "SELECT 1"})

    assert result == {"rows": [1]}
    spans = [span for span in trace.spans if span.type == "tool_call"]
    assert len(spans) == 1
    assert spans[0].input["tool"] == "execute_sql_tool"
    assert spans[0].input["capability"] == "sql.query"
    assert spans[0].input["agent"] == "workflow_sql"
    assert spans[0].status == "success"

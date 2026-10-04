# -*- coding: utf-8 -*-
"""观察面板按 Tool 下钻时覆盖所有工作流入口。"""

from types import SimpleNamespace

import pytest

from backend.app.api.routes import observability


@pytest.mark.asyncio
async def test_has_tool_filter_does_not_restrict_workflow_name(monkeypatch):
    span = SimpleNamespace(
        type="tool_call", input={"tool": "travel_train_search_tool"},
        name="travel_train_search_tool",
    )
    records = [
        SimpleNamespace(id="agent-trace", workflow_name="agent", spans=[span]),
        SimpleNamespace(id="travel-trace", workflow_name="travel", spans=[span]),
    ]
    monkeypatch.setattr(
        observability, "trace_collector",
        SimpleNamespace(list=lambda *_args, **_kwargs: records),
    )
    monkeypatch.setattr(
        observability, "_to_trace_dto",
        lambda record: {"id": record.id, "workflow_name": record.workflow_name},
    )

    result = await observability.list_traces(
        workflow_name=None,
        session_id=None,
        has_tag=None,
        has_tool="travel_train_search_tool",
    )

    assert {item["id"] for item in result["traces"]} == {
        "agent-trace", "travel-trace",
    }

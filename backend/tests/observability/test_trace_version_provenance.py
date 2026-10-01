"""请求级 Prompt 快照和 Tool 契约版本的 Trace 验收测试。"""
from __future__ import annotations


def test_trace_contains_prompt_snapshot_and_tool_contract_hash(monkeypatch):
    from backend.observability.tracer import trace_collector
    from backend.orchestration.graph.runner import GraphRunner
    from backend.prompts.service import prompt_service
    from backend.skills.base import _tool_contract_hash

    captured = {}
    original_finish = trace_collector.finish

    monkeypatch.setattr(
        prompt_service,
        "current_versions",
        lambda: {"rag.qa": 7, "agent.report": 3},
    )
    monkeypatch.setattr(
        "backend.orchestration.graph.runner.get_input_guard",
        lambda: type("_Guard", (), {"guard": lambda *_args, **_kwargs: type(
            "_Result", (), {"action": "allow"}
        )()})(),
    )
    monkeypatch.setattr(
        "backend.prompts.hot_reload.ensure_prompt_snapshot_fresh",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.prompts.hot_reload.prompt_runtime_metadata",
        lambda versions: {
            "epoch": 12,
            "versions": dict(versions),
            "snapshot_time": "2026-10-01T00:00:00Z",
            "reload_source": "db",
        },
    )

    def capture_finish(record, answer, total_ms, model, provider=""):
        captured["trace"] = record
        original_finish(record, answer, total_ms, model, provider)

    monkeypatch.setattr(trace_collector, "finish", capture_finish)

    class _Memory:
        def start_session(self, *_args, **_kwargs):
            span = trace_collector.start_span(
                "governed-tool", name="execute_sql_tool", type="tool_call"
            )
            trace_collector.end_span(
                span,
                metrics={"contract_hash": _tool_contract_hash("execute_sql_tool")},
            )
            raise RuntimeError("stop after provenance assertion setup")

    events = list(GraphRunner(None, _Memory(), set()).iter_events("查询库存"))
    trace = captured["trace"]

    assert events[-1]["event"] == "error"
    assert trace.tags["prompt_versions"] == "agent.report=3,rag.qa=7"
    assert trace.tags["prompt_epoch"] == 12
    tool_spans = [span for span in trace.spans if span.type == "tool_call"]
    assert tool_spans
    assert tool_spans[0].name == "execute_sql_tool"
    assert len(tool_spans[0].metrics["contract_hash"]) == 16

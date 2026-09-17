"""tests/test_observability_routes.py — 可观测性路由薄层测试

2026-09-17 P1 余量：/traces 的 has_tag 服务端过滤（选品漏斗运行历史的取数口径）。
漏斗跑在主图内、workflow_name 仍是主图，历史页只能以 tags 键存在性为筛选口径。
"""
import asyncio


def _stored(trace_id: str, tags: dict) -> dict:
    return {
        "id": trace_id, "timestamp": "2026-09-17T15:00:00", "session_id": "s1",
        "question": "帮我做智能选品", "answer_preview": "a", "duration_ms": 100,
        "workflow_name": "agent", "tags": tags,
    }


def test_list_traces_has_tag_filter(monkeypatch):
    """has_tag=funnel_run_id → 只留带该标签的 trace；不传 → 全量。"""
    from backend.app.api.routes import observability as obs

    stored = [
        _stored("t1", {"funnel_run_id": "sel-1", "funnel_status": "ok"}),
        _stored("t2", {"funnel_run_id": "sel-2", "funnel_status": "failed"}),
        _stored("t3", {"kb_id": "d1"}),          # 非漏斗 trace
        _stored("t4", {}),                        # 无标签
    ]
    monkeypatch.setattr(obs.trace_collector, "list", lambda limit: stored)

    out = asyncio.run(obs.list_traces(
        limit=10, workflow_name=None, session_id=None, has_tag="funnel_run_id"))
    assert [t["id"] for t in out["traces"]] == ["t1", "t2"]

    out2 = asyncio.run(obs.list_traces(
        limit=10, workflow_name=None, session_id=None, has_tag=None))
    assert len(out2["traces"]) == 4

    # tags 缺失/为 None 的记录不过滤炸场
    monkeypatch.setattr(obs.trace_collector, "list",
                        lambda limit: [_stored("t5", None)])
    out3 = asyncio.run(obs.list_traces(
        limit=10, workflow_name=None, session_id=None, has_tag="funnel_run_id"))
    assert out3["traces"] == []

"""P0 E2E 验证：trace_summary 结构化分析层（免 Docker 本地开发）。

验证链路：
  1. TraceCollector.finish() 双写 → trace_store(详情) + analytics(摘要)
  2. analytics.list/sessions/cost_summary 聚合查询
  3. 与 SQLite 详情库行数一致性

运行：.venv\\Scripts\\python.exe backend\\scripts\\e2e_p0_analytics.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_ROOT, "backend", ".env"))

from backend.observability.analytics_store import get_analytics_store  # noqa: E402
from backend.observability.trace_store import get_trace_store  # noqa: E402
from backend.observability.tracer import Span, TraceRecord, trace_collector  # noqa: E402


def _mk(trace_id: str, session_id: str, q: str, cost: float) -> TraceRecord:
    rec = TraceRecord(
        id=trace_id, request_id=trace_id,
        timestamp="2026-09-03T09:00:00Z",
        session_id=session_id, question=q,
        workflow_name="agent", workflow_kind="rag_query",
        total_ms=1200, duration_ms=1200,
        model="qwen-plus", provider="dashscope",
        usage={"prompt_tokens": 90, "completion_tokens": 40, "total_tokens": 130},
        metadata={"kb_id": "kb-001"}, tags={"kb_id": "kb-001"},
    )
    rec.spans = [
        Span(span_id="root", parent_id=None, name="RAG 智能问答", type="agent",
             start_time="2026-09-03T09:00:00Z", end_time="2026-09-03T09:00:01Z",
             duration_ms=1200, sequence=0),
        Span(span_id="llm_generate", parent_id="root", name="生成", type="llm_call",
             start_time="2026-09-03T09:00:00Z", end_time="2026-09-03T09:00:01Z",
             duration_ms=900, sequence=1,
             metrics={"prompt_tokens": 90, "completion_tokens": 40,
                      "total_tokens": 130, "cost_usd": cost},
             status="success"),
    ]
    rec.status = "success"
    rec.root_span_id = "root"
    return rec


def main():
    assert os.getenv("OBS_ANALYTICS_ENABLED", "true").lower() != "false", \
        "OBS_ANALYTICS_ENABLED 被关闭，无法验证"
    store = get_analytics_store()
    detail = get_trace_store()
    before = store.count()
    print(f"[1] 初始 analytics 行数: {before}")

    # 双写：2 个会话共 3 条
    seeds = [
        ("e2e-p0-a", "e2e-p0-sess-1", "退货政策是什么", 0.002),
        ("e2e-p0-b", "e2e-p0-sess-1", "运费谁承担", 0.003),
        ("e2e-p0-c", "e2e-p0-sess-2", "发票怎么开", 0.0015),
    ]
    for tid, sess, q, cost in seeds:
        trace_collector.finish(_mk(tid, sess, q, cost), "答案预览", 1200, "qwen-plus")

    after = store.count()
    assert after == before + 3, f"双写数量不符: {before} -> {after}"
    print(f"[2] finish() 双写 OK: {before} -> {after}（+3）")

    # 与详情库一致性
    for tid, *_ in seeds:
        assert detail.get(tid) is not None, f"详情库缺失 {tid}"
    print("[3] trace_store 详情库 3 条齐全（主持久化未受影响）")

    # 服务端过滤
    rows = store.list(10, workflow_name="agent", session_id="e2e-p0-sess-1")
    assert len(rows) == 2 and {r["id"] for r in rows} == {"e2e-p0-a", "e2e-p0-b"}
    print(f"[4] 服务端过滤 OK: session=e2e-p0-sess-1 -> {len(rows)} 条")

    # Sessions 聚合
    sess = [s for s in store.sessions(50) if s["session_id"].startswith("e2e-p0")]
    by = {s["session_id"]: s for s in sess}
    assert by["e2e-p0-sess-1"]["turns"] == 2
    assert abs(by["e2e-p0-sess-1"]["total_cost_usd"] - 0.005) < 1e-9
    assert by["e2e-p0-sess-2"]["turns"] == 1
    print(f"[5] Sessions 聚合 OK: sess-1 turns=2 cost=0.005, sess-2 turns=1")

    # Cost 聚合
    cost_rows = [r for r in store.cost_summary(days=7) if r["day"] == "2026-09-03"]
    assert cost_rows, "cost_summary 无当日数据"
    print(f"[6] Cost 聚合 OK: {cost_rows[0]['day']} model={cost_rows[0]['model']} "
          f"traces={cost_rows[0]['traces']}")

    # 清理 e2e 种子（保持库干净）
    import sqlite3
    with sqlite3.connect(store._db_path) as conn:
        conn.execute("DELETE FROM trace_summary WHERE trace_id LIKE 'e2e-p0-%'")
    assert store.count() == before
    print(f"[7] 种子已清理，行数回到 {before}")
    print("E2E PASS")


if __name__ == "__main__":
    main()

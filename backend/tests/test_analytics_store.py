"""analytics_store.py 单元测试 — P0 结构化分析层（本地 SQLite 实现）。

覆盖：
- save 字段抽取与 _row_to_dict 映射
- 服务端过滤（workflow_name / session_id）
- Sessions 聚合（P1 地基）
- Cost 按日×模型聚合（P2 地基）
- 禁用开关软失败
- TraceCollector.finish 双写（trace_store + analytics）
"""
import pytest

import backend.observability.analytics_store as as_mod
import backend.observability.trace_store as ts_mod
from backend.observability.analytics_store import AnalyticsStore
from backend.observability.trace_store import TraceStore
from backend.observability.tracer import Span, TraceCollector, TraceRecord


def _flush():
    """Phase 3 异步写入后，强制同步刷到 SQLite（测试用）。"""
    from backend.observability.trace_writer import get_trace_write_queue
    get_trace_write_queue().flush()


# ═══════════════════════════════════════════════
# 构造工具
# ═══════════════════════════════════════════════

def _mk_record(trace_id: str, session_id: str = "sess-1",
               workflow_name: str = "agent", cost: float = 0.002,
               rejected: bool = False) -> TraceRecord:
    rec = TraceRecord(
        id=trace_id, request_id=trace_id,
        timestamp="2026-09-02T10:00:00Z",
        session_id=session_id, question="退货政策是什么",
        workflow_name=workflow_name, workflow_kind="rag_query",
        total_ms=1500, duration_ms=1500,
        model="qwen-plus", provider="dashscope",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        metadata={"rejection": {"rejected": rejected}, "kb_id": "kb-001"},
        tags={"kb_id": "kb-001"},
    )
    rec.spans = [
        Span(span_id="root", parent_id=None, name="RAG 智能问答", type="agent",
             start_time="2026-09-02T10:00:00Z", end_time="2026-09-02T10:00:01Z",
             duration_ms=1500, sequence=0),
        Span(span_id="llm_generate", parent_id="root", name="生成", type="llm_call",
             start_time="2026-09-02T10:00:00Z", end_time="2026-09-02T10:00:01Z",
             duration_ms=1100, sequence=1,
             metrics={"model_name": "qwen-plus", "prompt_tokens": 100,
                      "completion_tokens": 50, "total_tokens": 150,
                      "cost_usd": cost},
             status="success"),
    ]
    rec.status = "success"
    rec.root_span_id = "root"
    rec.answer_preview = "七天无理由退货"
    rec.answer_len = 7
    return rec


@pytest.fixture
def store(tmp_path, monkeypatch):
    """启用状态 + 临时库的独立实例。"""
    monkeypatch.setenv("OBS_ANALYTICS_ENABLED", "true")
    return AnalyticsStore(db_path=str(tmp_path / "analytics.db"))


# ═══════════════════════════════════════════════
# 写入与字段映射
# ═══════════════════════════════════════════════

class TestSaveAndList:

    def test_save_and_field_mapping(self, store):
        assert store.save(_mk_record("t-001")) is True
        rows = store.list(10)
        assert len(rows) == 1
        d = rows[0]
        # _row_to_dict：trace_id→id、ts→timestamp、token 三列→usage
        assert d["id"] == "t-001"
        assert d["timestamp"] == "2026-09-02T10:00:00Z"
        assert d["session_id"] == "sess-1"
        assert d["workflow_name"] == "agent"
        assert d["duration_ms"] == 1500
        assert d["model"] == "qwen-plus"
        assert d["usage"] == {"prompt_tokens": 100, "completion_tokens": 50,
                              "total_tokens": 150}
        assert d["cost_usd"] == pytest.approx(0.002)
        assert d["kb_id"] == "kb-001"
        assert d["tags"] == {"kb_id": "kb-001"}
        assert d["rejected"] is False

    def test_rejected_flag(self, store):
        store.save(_mk_record("t-rej", rejected=True))
        assert store.list(10)[0]["rejected"] is True

    def test_upsert_same_id(self, store):
        store.save(_mk_record("t-001"))
        store.save(_mk_record("t-001"))
        assert store.count() == 1

    def test_cost_from_span_metrics(self, store):
        rec = _mk_record("t-cost", cost=0.01)
        rec.spans.append(Span(span_id="llm2", parent_id="root", name="生成2",
                              type="llm_call", duration_ms=100, sequence=2,
                              metrics={"cost_usd": 0.005}))
        store.save(rec)
        # 两个 llm span 成本累计
        assert store.list(10)[0]["cost_usd"] == pytest.approx(0.015)


# ═══════════════════════════════════════════════
# 服务端过滤（前端不再拉 200 条本地 filter）
# ═══════════════════════════════════════════════

class TestServerSideFilter:

    @pytest.fixture(autouse=True)
    def _seed(self, store):
        self.store = store
        store.save(_mk_record("t-a1", session_id="s1", workflow_name="agent"))
        store.save(_mk_record("t-a2", session_id="s1", workflow_name="agent"))
        store.save(_mk_record("t-u1", session_id="s2", workflow_name="upload"))

    def test_filter_by_workflow(self):
        rows = self.store.list(10, workflow_name="agent")
        assert len(rows) == 2
        assert {r["id"] for r in rows} == {"t-a1", "t-a2"}

    def test_filter_by_session(self):
        rows = self.store.list(10, session_id="s2")
        assert len(rows) == 1 and rows[0]["id"] == "t-u1"

    def test_filter_combined(self):
        rows = self.store.list(10, workflow_name="agent", session_id="s2")
        assert rows == []


# ═══════════════════════════════════════════════
# Sessions 聚合（P1 地基）
# ═══════════════════════════════════════════════

class TestSessions:

    def test_group_by_session(self, store):
        store.save(_mk_record("t-1", session_id="s1"))
        store.save(_mk_record("t-2", session_id="s1"))
        store.save(_mk_record("t-3", session_id="s2", cost=0.004))
        rows = store.sessions(10)
        assert len(rows) == 2
        by_id = {r["session_id"]: r for r in rows}
        s1 = by_id["s1"]
        assert s1["turns"] == 2
        assert s1["total_tokens"] == 300
        assert s1["total_cost_usd"] == pytest.approx(0.004)
        assert s1["avg_duration_ms"] == pytest.approx(1500)
        assert by_id["s2"]["total_cost_usd"] == pytest.approx(0.004)

    def test_empty_session_id_excluded(self, store):
        store.save(_mk_record("t-nosess", session_id=""))
        assert store.sessions(10) == []


# ═══════════════════════════════════════════════
# Cost 聚合（P2 地基）
# ═══════════════════════════════════════════════

class TestCostSummary:

    def test_group_by_day_model(self, store):
        store.save(_mk_record("t-1"))
        r2 = _mk_record("t-2")
        r2.timestamp = "2026-09-02T12:00:00Z"
        r2.model = "qwen-max"
        store.save(r2)
        rows = store.cost_summary(days=7)
        assert len(rows) == 2
        by_model = {r["model"]: r for r in rows}
        assert by_model["qwen-plus"]["traces"] == 1
        assert by_model["qwen-plus"]["day"] == "2026-09-02"
        assert by_model["qwen-max"]["total_tokens"] == 150


# ═══════════════════════════════════════════════
# 禁用开关（软失败）
# ═══════════════════════════════════════════════

class TestDisabled:

    def test_save_returns_false_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBS_ANALYTICS_ENABLED", "false")
        s = AnalyticsStore(db_path=str(tmp_path / "a.db"))
        assert s.save(_mk_record("t-x")) is False
        assert s.count() == 0

    def test_queries_still_safe_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBS_ANALYTICS_ENABLED", "false")
        s = AnalyticsStore(db_path=str(tmp_path / "a.db"))
        assert s.list(10) == []
        assert s.sessions(10) == []
        assert s.cost_summary() == []


# ═══════════════════════════════════════════════
# TraceCollector 集成：finish 双写
# ═══════════════════════════════════════════════

class TestTracerDualWrite:

    @pytest.fixture
    def collector(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBS_ANALYTICS_ENABLED", "true")
        c = TraceCollector()
        ts_mod._trace_store = TraceStore(db_path=str(tmp_path / "t.db"))
        as_mod._analytics_store = AnalyticsStore(
            db_path=str(tmp_path / "analytics.db"))
        yield c
        c.clear_for_test()
        ts_mod._trace_store = None
        as_mod._analytics_store = None

    def test_finish_writes_both_stores(self, collector):
        collector.finish(_mk_record("t-dual"), "七天无理由退货", 1500, "qwen-plus")
        _flush()
        # 详情库（主持久化）
        assert ts_mod._trace_store.get("t-dual") is not None
        # 分析层（结构化摘要）
        rows = as_mod._analytics_store.list(10)
        assert len(rows) == 1 and rows[0]["id"] == "t-dual"
        assert rows[0]["usage"]["total_tokens"] == 150

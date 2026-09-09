"""langfuse_exporter.py 单元测试 — Langfuse 上报/读取层。

覆盖：
- enabled 开关（无 key / 显式关闭 / 配置齐全）
- _build_batch：trace-create + observation-create 映射（GENERATION/SPAN、usage、父子链）
- 写入→读取映射等价性（rec_meta 无损重建、span 字段还原）
- 软失败：服务不可达时不抛异常（与项目告警/持久化哲学一致）
- TraceCollector 集成：exporter 关闭时 finish/list/get 降级回 SQLite
"""
import time
from unittest.mock import patch

import pytest

import backend.observability.langfuse_exporter as lf_mod
import backend.observability.trace_store as ts_mod
from backend.observability.langfuse_exporter import LangfuseExporter
from backend.observability.trace_store import TraceStore
from backend.observability.tracer import Span, TraceCollector, TraceRecord


# ═══════════════════════════════════════════════
# 构造工具
# ═══════════════════════════════════════════════


def _flush():
    """Phase 3 异步写入后，强制同步刷到 SQLite（测试用）。"""
    from backend.observability.trace_writer import get_trace_write_queue
    get_trace_write_queue().flush()


def _mk_record() -> TraceRecord:
    """构造典型 RAG trace：root(agent) → retrieval → llm_call。"""
    rec = TraceRecord(
        id="t-lf-001", request_id="t-lf-001",
        timestamp="2026-09-02T10:00:00Z",
        session_id="sess-1", question="退货政策是什么",
        workflow_name="rag_agent", workflow_kind="rag_query",
        total_ms=1500, duration_ms=1500,
        model="qwen-plus", provider="dashscope",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        metadata={"rejection": {"rejected": False}},
        tags={"kb_id": "kb-001"},
    )
    rec.spans = [
        Span(span_id="root", parent_id=None, name="RAG 智能问答", type="agent",
             start_time="2026-09-02T10:00:00Z", end_time="2026-09-02T10:00:01Z",
             duration_ms=1500, sequence=0),
        Span(span_id="retrieval", parent_id="root", name="检索", type="retrieval",
             start_time="2026-09-02T10:00:00Z", end_time="2026-09-02T10:00:00Z",
             duration_ms=300, sequence=1, metrics={"top_k": 5}),
        Span(span_id="llm_generate", parent_id="root", name="生成", type="llm_call",
             start_time="2026-09-02T10:00:00Z", end_time="2026-09-02T10:00:01Z",
             duration_ms=1100, sequence=2,
             metrics={"model_name": "qwen-plus", "prompt_tokens": 100,
                      "completion_tokens": 50, "total_tokens": 150,
                      "cost_usd": 0.002},
             status="success"),
    ]
    rec.status = "success"
    rec.root_span_id = "root"
    rec.answer_preview = "七天无理由退货"
    rec.answer_len = 7
    return rec


@pytest.fixture
def exporter(monkeypatch):
    """配置齐全的独立实例（不依赖环境变量）。"""
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    return LangfuseExporter(host="http://langfuse.test",
                            public_key="pk-test", secret_key="sk-test")


# ═══════════════════════════════════════════════
# enabled 开关
# ═══════════════════════════════════════════════

class TestEnabled:

    def test_disabled_without_keys(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        ex = LangfuseExporter(host="http://langfuse.test")
        assert ex.enabled is False

    def test_disabled_by_flag(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        ex = LangfuseExporter(host="http://x", public_key="pk", secret_key="sk")
        assert ex.enabled is False

    def test_enabled_with_full_config(self, exporter):
        assert exporter.enabled is True


# ═══════════════════════════════════════════════
# 写入映射 _build_batch
# ═══════════════════════════════════════════════

class TestBuildBatch:

    def test_batch_structure(self, exporter):
        batch = exporter._build_batch(_mk_record())
        # 1 个 trace-create + 3 个 observation-create
        assert batch[0]["type"] == "trace-create"
        assert len(batch) == 4
        assert all(e["type"] == "observation-create" for e in batch[1:])

    def test_trace_body_fields(self, exporter):
        body = exporter._build_batch(_mk_record())[0]["body"]
        assert body["id"] == "t-lf-001"
        assert body["name"] == "rag_agent"
        assert body["sessionId"] == "sess-1"
        assert body["input"] == "退货政策是什么"
        assert "rag_query" in body["tags"]
        # 对等字段存入 metadata，读取时无损重建
        meta = body["metadata"]
        assert meta["workflow_kind"] == "rag_query"
        assert meta["duration_ms"] == 1500
        assert meta["usage"]["total_tokens"] == 150
        assert meta["sla_threshold_ms"] == 10000
        assert meta["record_metadata"]["rejection"]["rejected"] is False

    def test_llm_span_becomes_generation(self, exporter):
        batch = exporter._build_batch(_mk_record())
        gen = [e for e in batch[1:]
               if e["body"]["name"] == "生成"][0]["body"]
        assert gen["type"] == "GENERATION"
        assert gen["model"] == "qwen-plus"
        assert gen["usageDetails"] == {"input": 100, "output": 50, "total": 150}
        assert gen["costDetails"]["cost_amount"] == 0.002
        # 父子链：root 无 parent，子节点带 parentObservationId
        assert "parentObservationId" not in gen or gen.get("parentObservationId")
        assert gen["parentObservationId"] == "t-lf-001:root"

    def test_root_span_no_parent(self, exporter):
        batch = exporter._build_batch(_mk_record())
        root = [e for e in batch[1:]
                if e["body"]["name"] == "RAG 智能问答"][0]["body"]
        assert root["type"] == "SPAN"
        assert "parentObservationId" not in root

    def test_explicit_timestamps(self, exporter):
        batch = exporter._build_batch(_mk_record())
        obs = batch[1]["body"]
        # startTime/endTime 显式携带，保证时间线准确
        assert obs["startTime"] == "2026-09-02T10:00:00Z"
        assert obs["endTime"]


# ═══════════════════════════════════════════════
# 读取重建（写入→读取等价性）
# ═══════════════════════════════════════════════

class TestReadReconstruction:

    def _simulate_langfuse_responses(self, exporter):
        """用 _build_batch 的输出模拟 GET /traces/{id} 与 /observations 返回。"""
        batch = exporter._build_batch(_mk_record())
        trace_body = batch[0]["body"]
        fake_trace = {**trace_body, "sessionId": trace_body.get("sessionId")}
        fake_obs = {"data": [
            {"id": e["body"]["id"],
             "traceId": e["body"]["traceId"],
             "type": e["body"]["type"],
             "name": e["body"]["name"],
             "startTime": e["body"]["startTime"],
             "endTime": e["body"]["endTime"],
             "input": e["body"].get("input"),
             "output": e["body"].get("output"),
             "level": e["body"].get("level"),
             "metadata": e["body"].get("metadata"),
             "parentObservationId": e["body"].get("parentObservationId")}
            for e in batch[1:]
        ]}
        return fake_trace, fake_obs

    def test_summary_roundtrip(self, exporter):
        fake_trace, _ = self._simulate_langfuse_responses(exporter)
        d = exporter._trace_to_summary(fake_trace)
        assert d["id"] == "t-lf-001"
        assert d["question"] == "退货政策是什么"
        assert d["answer_preview"] == "七天无理由退货"
        assert d["duration_ms"] == 1500
        assert d["model"] == "qwen-plus"
        assert d["workflow_kind"] == "rag_query"
        assert d["usage"]["total_tokens"] == 150
        assert d["tags"] == {"kb_id": "kb-001"}
        assert d["metadata"]["rejection"]["rejected"] is False

    def test_span_reconstruction(self, exporter):
        _, fake_obs = self._simulate_langfuse_responses(exporter)
        spans = [exporter._observation_to_span(o) for o in fake_obs["data"]]
        by_id = {s["span_id"]: s for s in spans}
        # 字段还原
        llm = by_id["llm_generate"]
        assert llm["type"] == "llm_call"
        assert llm["parent_id"] == "root"
        assert llm["metrics"]["total_tokens"] == 150
        # duration 由 start/end 重建（1000ms）
        assert llm["duration_ms"] == 1000
        # root 的 parent 为 None
        assert by_id["root"]["parent_id"] is None

    def test_get_trace_full(self, exporter):
        fake_trace, fake_obs = self._simulate_langfuse_responses(exporter)

        def fake_request(method, path, **kw):
            if path.startswith("/api/public/traces/t-lf-001"):
                return fake_trace
            if "/observations" in path:
                return fake_obs
            return None

        with patch.object(exporter, "_request", side_effect=fake_request):
            d = exporter.get_trace("t-lf-001")
        assert d is not None
        assert len(d["spans"]) == 3
        # 按 sequence 排序
        assert [s["sequence"] for s in d["spans"]] == [0, 1, 2]


# ═══════════════════════════════════════════════
# 软失败：服务不可达
# ═══════════════════════════════════════════════

class TestSoftFail:

    def test_export_unreachable_returns_false(self):
        # 指向必然不可达的地址，不能抛异常
        ex = LangfuseExporter(host="http://127.0.0.1:1", public_key="pk",
                              secret_key="sk", timeout=0.5)
        assert ex.export_trace(_mk_record()) is False

    def test_list_unreachable_returns_empty(self):
        ex = LangfuseExporter(host="http://127.0.0.1:1", public_key="pk",
                              secret_key="sk", timeout=0.5)
        assert ex.list_traces(10) == []
        assert ex.get_trace("any") is None

    def test_disabled_export_returns_false(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        ex = LangfuseExporter(host="http://x", public_key="pk", secret_key="sk")
        assert ex.export_trace(_mk_record()) is False


# ═══════════════════════════════════════════════
# TraceCollector 集成：降级回 SQLite
# ═══════════════════════════════════════════════

class TestTracerFallback:

    @pytest.fixture
    def collector(self, tmp_path, monkeypatch):
        """exporter 关闭 + 临时 SQLite 的独立 collector。"""
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        lf_mod._exporter = None  # 重置单例
        c = TraceCollector()
        ts_mod._trace_store = TraceStore(db_path=str(tmp_path / "t.db"))
        yield c
        c.clear_for_test()
        ts_mod._trace_store = None
        lf_mod._exporter = None

    def test_finish_saves_sqlite_when_disabled(self, collector):
        rec = _mk_record()
        collector.finish(rec, "七天无理由退货", 1500, "qwen-plus")
        _flush()
        assert ts_mod._trace_store.get("t-lf-001") is not None

    def test_list_falls_back_to_sqlite(self, collector):
        rec = _mk_record()
        collector.finish(rec, "七天无理由退货", 1500, "qwen-plus")
        _flush()
        rows = collector.list(10)
        assert rows and rows[0]["id"] == "t-lf-001"

    def test_get_falls_back_to_sqlite(self, collector):
        rec = _mk_record()
        collector.finish(rec, "七天无理由退货", 1500, "qwen-plus")
        _flush()
        d = collector.get("t-lf-001")
        assert d is not None
        assert len(d["spans"]) == 3

    def test_list_prefers_langfuse_when_enabled(self, collector):
        """exporter 开启且有数据时，list 不走 SQLite。"""
        fake = [{"id": "lf-only", "question": "q", "duration_ms": 10,
                 "status": "success"}]
        fake_ex = LangfuseExporter(host="http://x", public_key="pk",
                                   secret_key="sk")
        with patch.object(fake_ex, "list_traces", return_value=fake):
            with patch.object(lf_mod, "get_langfuse_exporter", return_value=fake_ex):
                with patch.object(type(fake_ex), "enabled",
                                  new_callable=lambda: property(lambda self: True)):
                    rows = collector.list(10)
        assert rows and rows[0]["id"] == "lf-only"

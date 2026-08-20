"""trace_store.py 单元测试 — SQLite 持久化存储。

此前覆盖仅来自 e2e 脚本（e2e_doc_driven/e2e_evidence_gate，非 pytest 收集），
本文件补齐 pytest 单测：
- save/get/list 往返（含 spans 序列化与 list 摘要剥离）
- trace_id 重复 save 幂等（INSERT OR REPLACE）
- list_since 时间过滤 + only_rejected 过滤
- _MAX_ROWS 容量驱逐（旧数据清理）
"""
import json
import sqlite3
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import backend.observability.trace_store as ts_mod
from backend.observability.trace_store import TraceStore
from backend.observability.tracer import TraceRecord, Span


def _mk_record(rid: str, question: str = "问题", rejected: bool = False,
               n_spans: int = 1) -> TraceRecord:
    rec = TraceRecord(id=rid, request_id=rid, question=question,
                      timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      duration_ms=100, total_ms=100)
    rec.spans = [Span(span_id=f"s{i}", parent_id=None, name=f"span{i}",
                      type="tool_call") for i in range(n_spans)]
    if rejected:
        rec.metadata = {"rejection": {"rejected": True, "reason": "no answer"}}
    return rec


@pytest.fixture
def store(tmp_path):
    """每个用例独立临时 DB，避免污染 data/trace_store.db。"""
    return TraceStore(db_path=str(tmp_path / "trace.db"))


class TestSaveGetList:

    def test_roundtrip_with_spans(self, store):
        rec = _mk_record("t001", question="退货政策是什么", n_spans=2)
        store.save(rec)
        got = store.get("t001")
        assert got is not None
        assert got["id"] == "t001"
        assert got["question"] == "退货政策是什么"
        # 中文不被转义（ensure_ascii=False）
        raw = sqlite3.connect(store._db_path).execute(
            "SELECT data FROM trace_store WHERE trace_id='t001'").fetchone()[0]
        assert "退货政策" in raw
        # spans 完整序列化
        assert len(got["spans"]) == 2
        assert got["spans"][0]["span_id"] == "s0"

    def test_get_missing_returns_none(self, store):
        assert store.get("nonexistent") is None

    def test_list_strips_spans_and_orders_desc(self, store):
        store.save(_mk_record("t_old"))
        time.sleep(1.05)  # created_at 秒级精度，需跨秒保证顺序
        store.save(_mk_record("t_new", n_spans=3))
        rows = store.list(limit=10)
        assert [r["id"] for r in rows] == ["t_new", "t_old"]
        for r in rows:
            assert "spans" not in r, "list 摘要必须剥离 spans 减少传输量"

    def test_save_without_id_is_ignored(self, store):
        rec = TraceRecord(id="")
        store.save(rec)
        assert store.list(limit=10) == []

    def test_duplicate_save_is_idempotent(self, store):
        store.save(_mk_record("dup", question="第一版"))
        store.save(_mk_record("dup", question="第二版"))
        rows = store.list(limit=10)
        assert len(rows) == 1
        assert store.get("dup")["question"] == "第二版"


class TestListSince:

    def test_cutoff_filters_old_traces(self, store):
        store.save(_mk_record("past"))
        future = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        assert store.list_since(future) == [], "未来 cutoff 不应命中任何 trace"
        rows = store.list_since(past)
        assert [r["id"] for r in rows] == ["past"]

    def test_only_rejected_filters(self, store):
        store.save(_mk_record("ok_1", rejected=False))
        store.save(_mk_record("rej_1", rejected=True))
        store.save(_mk_record("rej_no_flag"))  # metadata 无 rejection 字段
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        rejected = store.list_since(past, only_rejected=True)
        assert [r["id"] for r in rejected] == ["rej_1"]

        all_rows = store.list_since(past)
        assert len(all_rows) == 3

    def test_limit_respected(self, store):
        for i in range(5):
            store.save(_mk_record(f"r{i}"))
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        assert len(store.list_since(past, limit=2)) == 2


class TestCapacityEviction:

    def test_exceeding_max_rows_evicts_oldest(self, store):
        """_MAX_ROWS 驱逐：超限后最旧的一批被清理，最新写入必保留。

        手工预填 110 条 created_at 递增的历史行，再写第 111 条触发驱逐：
        删除最旧 (111 - 110 + 100) = 101 条 → 剩 h101~h109 + newest 共 10 条。
        """
        conn = sqlite3.connect(store._db_path)
        for i in range(110):
            conn.execute(
                "INSERT INTO trace_store (trace_id, data, created_at) VALUES (?,?,?)",
                (f"h{i:03d}", json.dumps({"id": f"h{i:03d}"}, ensure_ascii=False),
                 f"2026-01-01 {i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}"))
        conn.commit()
        conn.close()

        with patch.object(ts_mod, "_MAX_ROWS", 110):
            store.save(_mk_record("newest"))

        ids = {r["id"] for r in store.list(limit=500)}
        assert "newest" in ids, "最新写入绝不能被驱逐"
        assert "h109" in ids, "历史最新行应保留"
        assert "h000" not in ids, "最旧行应被驱逐"
        assert len(ids) == 10


class TestSingleton:

    def test_get_trace_store_returns_same_instance(self, monkeypatch):
        # 注入 fake 避免触碰真实 data/trace_store.db
        fake = object.__new__(TraceStore)
        monkeypatch.setattr(ts_mod, "_trace_store", fake)
        assert ts_mod.get_trace_store() is fake

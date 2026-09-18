"""test_trace_retention.py — trace 留存期限清理（合规 §5.5：14 天 / 敏感 180 天）。

覆盖：
  1. 默认期删除（>14 天非敏感）、敏感保留（14~180 天内的敏感类）；
  2. 敏感期删除（>180 天无条件）；
  3. JSON 脏行按非敏感处理（宁可早删不可多留）；
  4. 批量循环推进（多批 + 全敏感批不死循环）；
  5. store 批量接口（iter_before/delete_by_ids）真 PG 行为。
"""
import pytest

from backend.observability.trace_retention import _cutoff, purge_expired_traces


class _FakeStore:
    """内存 store：按 created_at 文本比较（与 PG 行为同构）。"""

    def __init__(self, rows: list[dict]):
        # rows: {trace_id, created_at, doc_type?}（JSON 脏行用 data=None 模拟）
        self.rows = {r["trace_id"]: r for r in rows}

    def iter_before(self, cutoff: str, *, limit: int = 5000):
        hits = [r for r in self.rows.values() if r["created_at"] < cutoff]
        hits.sort(key=lambda r: r["created_at"])
        out = []
        for r in hits[:limit]:
            if r.get("data") is None:
                out.append({"trace_id": r["trace_id"], "data": None,
                            "_created_at": r["created_at"]})
            else:
                out.append({"trace_id": r["trace_id"], **r["data"],
                            "_created_at": r["created_at"]})
        return out

    def delete_by_ids(self, ids):
        n = 0
        for i in ids:
            if i in self.rows:
                del self.rows[i]
                n += 1
        return n


def _now():
    from datetime import datetime
    return datetime(2026, 9, 19, 12, 0, 0)


def _row(tid, created_at, doc_type=None):
    data = {} if doc_type is None else {"doc_type": doc_type}
    return {"trace_id": tid, "created_at": created_at, "data": data or None}


NOW = _now()
D19 = _cutoff(14, NOW)   # 参考点：14 天截止
D180 = _cutoff(180, NOW)


class TestPurgeExpiredTraces:
    def test_old_nonsensitive_deleted_sensitive_kept(self):
        store = _FakeStore([
            _row("t1", "2026-09-01 00:00:00", "policy"),       # 18 天前非敏感 → 删
            _row("t2", "2026-09-01 00:00:00", "financial"),    # 18 天前敏感 → 留
            _row("t3", "2026-09-10 00:00:00", "faq"),          # 9 天前 → 留
            _row("t4", "2026-09-01 00:00:00", "legal"),        # 18 天前敏感 → 留
            _row("t5", "2026-09-01 00:00:00", "customer_data"),
        ])
        s = purge_expired_traces(store, now=NOW)
        assert set(store.rows) == {"t2", "t3", "t4", "t5"}
        assert s["default_purged"] == 1 and s["sensitive_purged"] == 0

    def test_sensitive_deleted_after_180_days(self):
        store = _FakeStore([
            _row("old_fin", "2026-03-01 00:00:00", "financial"),  # ~202 天 → 删
            _row("old_legal", "2026-03-10 00:00:00", "legal"),    # ~193 天 → 删
            _row("old_policy", "2026-03-01 00:00:00", "policy"),  # 非敏感 → 删
            _row("keep", "2026-04-01 00:00:00", "customer_data"), # ~171 天 → 留
        ])
        s = purge_expired_traces(store, now=NOW)
        assert set(store.rows) == {"keep"}
        # 敏感期段 = >180 天无条件删：old_fin + old_legal + old_policy（非敏感
        # 也早已超 14 天期，落在此段一并删除），keep=171 天在敏感留存窗口内
        assert s["sensitive_purged"] == 3 and s["default_purged"] == 0

    def test_dirty_json_row_treated_as_nonsensitive(self):
        """JSON 解析失败行按非敏感处理：超过默认期即删（留存即风险）。"""
        store = _FakeStore([
            {"trace_id": "dirty", "created_at": "2026-09-01 00:00:00", "data": None},
            {"trace_id": "fresh_dirty", "created_at": "2026-09-18 00:00:00", "data": None},
        ])
        purge_expired_traces(store, now=NOW)
        assert set(store.rows) == {"fresh_dirty"}

    def test_batch_loop_advances_and_no_deadloop(self):
        """batch_size=2 多批推进；全敏感批不死循环。"""
        rows = [_row(f"n{i}", "2026-09-01 00:00:00", "policy") for i in range(5)]
        rows += [_row(f"s{i}", "2026-09-02 00:00:00", "financial") for i in range(5)]
        store = _FakeStore(rows)
        s = purge_expired_traces(store, now=NOW, batch_size=2)
        # 5 条非敏感全删；5 条敏感保留
        assert s["default_purged"] == 5
        assert sum(1 for r in store.rows.values() if r.get("data", {}).get("doc_type") == "financial") == 5

    def test_doc_type_from_metadata_nested(self):
        """doc_type 嵌在 metadata 里的 trace 也能识别敏感。"""
        store = _FakeStore([
            {"trace_id": "m1", "created_at": "2026-09-01 00:00:00",
             "data": {"metadata": {"doc_type": "customer_data"}}},
        ])
        purge_expired_traces(store, now=NOW)
        assert "m1" in store.rows, "metadata.doc_type=customer_data 必须按敏感保留 180 天"


class TestStoreBatchApi:
    """store 批量接口真 PG 行为（前缀隔离，与 test_obs_stores_pg 同模式）。"""

    def test_iter_and_delete(self, monkeypatch):
        monkeypatch.setenv("OBS_DB_BACKEND", "postgres")
        monkeypatch.setenv("OBS_DB_PG_TABLE_PREFIX", "pgtest_ret_")
        from backend.observability.trace_store_pg import PostgresTraceStore
        store = PostgresTraceStore()
        # 清空测试表
        with store._lock, store._conn() as conn:
            store._exec(conn, f"DELETE FROM {store._table}", ())
        store.save_dict({"id": "r1", "doc_type": "faq"})
        store.save_dict({"id": "r2", "doc_type": "faq"})
        rows = store.iter_before("2099-01-01 00:00:00", limit=10)
        assert {r["trace_id"] for r in rows} == {"r1", "r2"}
        assert store.delete_by_ids(["r1"]) == 1
        assert store.delete_by_ids([]) == 0
        assert store.get("r2") is not None and store.get("r1") is None

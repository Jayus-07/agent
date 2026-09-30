"""test_ttft_persistence.py — TTFT 落 trace_summary.ttft_ms（M13 尾项/D13）

锁：update_ttft_ms 旁路写（开关尊重/trace_id 空/SQL 失败软退化）+
save_dict 的 ON CONFLICT UPDATE SET 不含 ttft_ms（任何写入时序互不清值
的关键不变量，防回归）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.observability import analytics_store_pg


@pytest.fixture()
def store(monkeypatch):
    """真 PostgresAnalyticsStore 实例 + mock 掉 _conn（不连库）。"""
    s = analytics_store_pg.PostgresAnalyticsStore.__new__(
        analytics_store_pg.PostgresAnalyticsStore)
    s._lock = __import__("threading").Lock()
    s._table = "trace_summary"
    conn = MagicMock()
    monkeypatch.setattr(s, "_conn", lambda: _CM(conn))
    return s, conn


class _CM:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


class TestUpdateTtftMs:
    def test_disabled_cfg_skips(self, monkeypatch, store):
        monkeypatch.setattr(analytics_store_pg, "_cfg_enabled", lambda: False)
        assert analytics_store_pg.update_ttft_ms("t1", 123) is False

    def test_empty_trace_id_skips(self, monkeypatch):
        monkeypatch.setattr(analytics_store_pg, "_cfg_enabled", lambda: True)
        assert analytics_store_pg.update_ttft_ms("", 123) is False

    def test_sql_failure_soft(self, monkeypatch, store):
        """UPDATE 抛异常 → 返回 False 不穿透（观测旁路口径）。"""
        monkeypatch.setattr(analytics_store_pg, "_cfg_enabled", lambda: True)
        s, _conn = store
        import backend.observability.analytics_store as as_mod
        monkeypatch.setattr(as_mod, "get_analytics_store", lambda: s)
        s._conn = MagicMock(side_effect=RuntimeError("db down"))
        assert analytics_store_pg.update_ttft_ms("t1", 123) is False

    def test_happy_path_updates_target_row(self, monkeypatch, store):
        monkeypatch.setattr(analytics_store_pg, "_cfg_enabled", lambda: True)
        s, conn = store
        import backend.observability.analytics_store as as_mod
        monkeypatch.setattr(as_mod, "get_analytics_store", lambda: s)
        assert analytics_store_pg.update_ttft_ms("t1", 250) is True
        # _exec_scalar：conn.cursor() 无参 → SQL/params 在 cursor 的 execute 上
        execute = conn.cursor.return_value.execute
        sql, params = execute.call_args[0]
        assert "SET ttft_ms = %s" in sql
        assert params == (250, "t1")


class TestSaveDictNoClobber:
    def test_upsert_update_set_excludes_ttft(self):
        """save_dict 的 ON CONFLICT UPDATE SET 列清单不得含 ttft_ms——
        否则 writer 全量写会把 API 旁路补写的 TTFT 清回 NULL。"""
        import inspect

        src = inspect.getsource(
            analytics_store_pg.PostgresAnalyticsStore.save_dict)
        set_section = src.split("ON CONFLICT")[1]
        assert "ttft_ms" not in set_section

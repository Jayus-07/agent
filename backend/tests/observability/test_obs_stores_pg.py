"""test_obs_stores_pg.py — 迁移计划 2026-09-17 Batch A：可观测层 PG 连接层测试。

覆盖：
  1. 工厂分发（OBS_DB_BACKEND=postgres → PG 子类；默认 sqlite 不受影响）
  2. PostgresTraceStore：save_dict/get 往返、list 摘要（无 spans）、
     list_since 时间窗 + only_rejected、覆盖写语义
  3. PostgresAnalyticsStore：save_dict → list/sessions/cost_summary/count
  4. PostgresLLMUsageStore：record → by_trace/list_calls/dashboard

前置：本机 PostgreSQL 可达（backend/config/database.py::OBS_DB_PG_CONFIG，
默认 agent_memory 库）。不可达时整文件 skip；unit-only 运行用 -m "not pg"。
隔离：专用测试表前缀（env OBS_DB_PG_TABLE_PREFIX=pgtest_obs_），fixture 清空。
"""
from __future__ import annotations

import time

import psycopg2
import pytest

from backend.config.database import OBS_DB_PG_CONFIG

PG_PREFIX = "pgtest_obs_"


def _pg_alive() -> bool:
    try:
        conn = psycopg2.connect(**OBS_DB_PG_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(
        not _pg_alive(),
        reason="No PostgreSQL reachable (OBS_DB_PG_CONFIG)",
    ),
]


@pytest.fixture()
def obs_env(monkeypatch):
    """PG 模式 + 专用测试表前缀 + 打开 analytics 写入（conftest 默认关闭）。"""
    monkeypatch.setenv("OBS_DB_BACKEND", "postgres")
    monkeypatch.setenv("OBS_DB_PG_TABLE_PREFIX", PG_PREFIX)
    monkeypatch.setenv("OBS_ANALYTICS_ENABLED", "true")
    yield monkeypatch


@pytest.fixture()
def _clean_tables(obs_env):
    """重置工厂单例 + 用例前后清空测试表。

    单例必须重置：get_*_store 缓存实例不会因删表而重建（_init_db 不再执行），
    下一个用例会拿到指向已 DROP 表的旧实例（写入被软失败吞掉 → 断言失败）。
    monkeypatch 结束时恢复原值，不污染其他用例的 SQLite 单例。
    """
    import backend.observability.analytics_store as as_mod
    import backend.observability.llm_usage_store as lu_mod
    import backend.observability.trace_store as ts_mod

    obs_env.setattr(ts_mod, "_trace_store", None)
    obs_env.setattr(as_mod, "_analytics_store", None)
    obs_env.setattr(lu_mod, "_llm_usage_store", None)

    tables = [f"{PG_PREFIX}trace_store", f"{PG_PREFIX}trace_summary",
              f"{PG_PREFIX}llm_usage"]

    def _drop():
        conn = psycopg2.connect(**OBS_DB_PG_CONFIG)
        try:
            cur = conn.cursor()
            for t in tables:
                cur.execute(f"DROP TABLE IF EXISTS {t}")
            conn.commit()
        finally:
            conn.close()

    _drop()
    yield
    _drop()


def _trace_dict(tid: str, *, rejected: bool = False, wf: str = "agent",
                with_spans: bool = True, ts: str = "2026-09-17 10:00:00") -> dict:
    d = {
        "id": tid, "timestamp": ts, "session_id": f"s-{tid[-4:]}",
        "workflow_name": wf, "workflow_kind": "other", "status": "success",
        "question": "q?", "answer_preview": "a", "total_ms": 123,
        "model": "gpt-test", "provider": "test",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "tags": {"kb_id": "kb1"},
        "metadata": {"rejection": {"rejected": rejected}} if rejected else {},
    }
    if with_spans:
        d["spans"] = [{"id": "sp1", "type": "load", "metrics": {"cost_usd": 0.01}}]
    return d


# ── 工厂分发 ─────────────────────────────────────────────────────────

class TestFactoryDispatch:
    def test_default_also_pg(self, monkeypatch):
        """2026-09-17 SQLite 轨删除：无 env 时工厂也直连 PG 实现。"""
        monkeypatch.delenv("OBS_DB_BACKEND", raising=False)
        import backend.observability.trace_store as ts_mod
        monkeypatch.setattr(ts_mod, "_trace_store", None)
        store = ts_mod.get_trace_store()
        from backend.observability.trace_store_pg import PostgresTraceStore
        assert isinstance(store, PostgresTraceStore)

    def test_postgres_dispatch(self, monkeypatch):
        import backend.observability.trace_store as ts_mod
        import backend.observability.analytics_store as as_mod
        import backend.observability.llm_usage_store as lu_mod
        monkeypatch.setenv("OBS_DB_BACKEND", "postgres")
        monkeypatch.setenv("OBS_DB_PG_TABLE_PREFIX", PG_PREFIX)
        monkeypatch.setattr(ts_mod, "_trace_store", None)
        monkeypatch.setattr(as_mod, "_analytics_store", None)
        monkeypatch.setattr(lu_mod, "_llm_usage_store", None)

        from backend.observability.trace_store_pg import PostgresTraceStore
        from backend.observability.analytics_store_pg import PostgresAnalyticsStore
        from backend.observability.llm_usage_store_pg import PostgresLLMUsageStore

        assert isinstance(ts_mod.get_trace_store(), PostgresTraceStore)
        assert isinstance(as_mod.get_analytics_store(), PostgresAnalyticsStore)
        assert isinstance(lu_mod.get_llm_usage_store(), PostgresLLMUsageStore)


# ── PostgresTraceStore ───────────────────────────────────────────────

class TestTraceStorePG:
    def test_save_get_roundtrip(self, _clean_tables):
        from backend.observability.trace_store import get_trace_store
        store = get_trace_store()
        store.save_dict(_trace_dict("pgtid0001"))
        got = store.get("pgtid0001")
        assert got is not None
        assert got["question"] == "q?"
        assert got["usage"]["total_tokens"] == 15
        assert store.get("nope") is None

    def test_overwrite_same_id(self, _clean_tables):
        from backend.observability.trace_store import get_trace_store
        store = get_trace_store()
        d1 = _trace_dict("pgtid0002"); d1["answer_preview"] = "v1"
        d2 = _trace_dict("pgtid0002"); d2["answer_preview"] = "v2"
        store.save_dict(d1)
        store.save_dict(d2)
        assert store.get("pgtid0002")["answer_preview"] == "v2"

    def test_list_strips_spans(self, _clean_tables):
        from backend.observability.trace_store import get_trace_store
        store = get_trace_store()
        for i in range(3):
            store.save_dict(_trace_dict(f"pgtid03{i}"))
        rows = store.list(10)
        assert len(rows) == 3
        assert all("spans" not in r for r in rows)

    def test_list_since_window_and_rejected(self, _clean_tables):
        from backend.observability.trace_store import get_trace_store
        store = get_trace_store()
        store.save_dict(_trace_dict("pgtid0400"))
        store.save_dict(_trace_dict("pgtid0401", rejected=True))
        # created_at 为写入时刻（本地时间文本），cutoff 用相对写入时刻的窗口
        fmt = "%Y-%m-%d %H:%M:%S"
        cutoff_before = time.strftime(fmt, time.localtime(time.time() - 60))
        cutoff_after = time.strftime(fmt, time.localtime(time.time() + 3600))
        rows = store.list_since(cutoff_before)
        assert {r["id"] for r in rows} == {"pgtid0400", "pgtid0401"}
        assert store.list_since(cutoff_after) == []
        rej = store.list_since(cutoff_before, only_rejected=True)
        assert [r["id"] for r in rej] == ["pgtid0401"]


# ── PostgresAnalyticsStore ───────────────────────────────────────────

class TestAnalyticsStorePG:
    def test_save_list_count(self, _clean_tables):
        from backend.observability.analytics_store import get_analytics_store
        store = get_analytics_store()
        assert store.save_dict(_trace_dict("pgtid0500", with_spans=False)) is True
        rows = store.list(10)
        assert len(rows) == 1
        r = rows[0]
        assert r["id"] == "pgtid0500"
        assert r["usage"]["total_tokens"] == 15
        assert r["tags"] == {"kb_id": "kb1"}
        assert r["rejected"] is False
        assert store.count() == 1

    def test_rejected_and_sessions(self, _clean_tables):
        from backend.observability.analytics_store import get_analytics_store
        store = get_analytics_store()
        store.save_dict(_trace_dict("pgtid0600", rejected=True, with_spans=False))
        rows = store.list()
        assert rows[0]["rejected"] is True
        sessions = store.sessions()
        assert len(sessions) == 1
        assert sessions[0]["turns"] == 1
        assert sessions[0]["total_tokens"] == 15

    def test_cost_summary(self, _clean_tables):
        from backend.observability.analytics_store import get_analytics_store
        store = get_analytics_store()
        store.save_dict(_trace_dict("pgtid0700", with_spans=False))
        cs = store.cost_summary(days=7)
        assert len(cs) == 1
        assert cs[0]["model"] == "gpt-test"
        assert cs[0]["total_tokens"] == 15


# ── PostgresLLMUsageStore ────────────────────────────────────────────

def _usage_event(trace_id: str, *, component: str = "llm",
                 model: str = "gpt-test", ts: str | None = None) -> dict:
    return {
        "timestamp": ts, "trace_id": trace_id, "session_id": "s-1",
        "component": component, "model": model, "provider": "test",
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
        "cached_tokens": 8, "reasoning_tokens": 0, "cost_usd": 0.002,
        "duration_ms": 350.0, "finish_reason": "stop",
    }


class TestLLMUsageStorePG:
    def test_record_by_trace(self, _clean_tables):
        from backend.observability.llm_usage_store import get_llm_usage_store
        store = get_llm_usage_store()
        assert store.record(_usage_event("pgtid0800")) is True
        rows = store.by_trace("pgtid0800")
        assert len(rows) == 1
        assert rows[0]["total_tokens"] == 120
        assert rows[0]["cached_tokens"] == 8
        assert store.by_trace("") == []

    def test_list_calls_filter_and_pagination(self, _clean_tables):
        from backend.observability.llm_usage_store import get_llm_usage_store
        store = get_llm_usage_store()
        for i in range(5):
            store.record(_usage_event(f"pgtid09{i}"))
        store.record(_usage_event("pgtid0emb", component="embedding"))
        res = store.list_calls(days=7)
        assert res["total"] == 6
        res = store.list_calls(days=7, component="llm", limit=3, offset=0)
        assert res["total"] == 5 and len(res["calls"]) == 3
        res = store.list_calls(days=7, model="gpt-test", component="embedding")
        assert res["total"] == 1

    def test_dashboard_aggregates(self, _clean_tables):
        from backend.observability.llm_usage_store import get_llm_usage_store
        store = get_llm_usage_store()
        for i in range(3):
            store.record(_usage_event("pgtid1000" if i == 0 else f"pgtid100{i}"))
        dash = store.dashboard(days=7)
        assert dash["totals"]["calls"] == 3
        assert dash["totals"]["requests"] == 3
        assert dash["totals"]["prompt_tokens"] == 300
        assert len(dash["daily"]) == 1
        assert len(dash["models"]) == 1
        assert dash["models"][0]["model"] == "gpt-test"

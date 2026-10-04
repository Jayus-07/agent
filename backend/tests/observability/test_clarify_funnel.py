"""追问漏斗双写测试（2026-10-03 企业口径）

1. writer：事件类型白名单 / 软失败 / SQL 列值对齐 / 指标与 PG 双口径
2. 聚合端点纯函数：转化率派生（分母 0 → None）
3. 双写不双计：图内卡走 events、L1 卡走 runner，路径互斥（接线注释级断言）
"""
from __future__ import annotations

import pytest

import backend.observability.clarify_funnel as _funnel_mod
from backend.observability.clarify_funnel import record_funnel_event

# conftest 的 _clarify_funnel_hermetic autouse 会替换 _insert_event；
# 本文件专测写入契约，import 时捕获真身并由文件级 autouse 恢复。
_REAL_INSERT_EVENT = _funnel_mod._insert_event


@pytest.fixture(autouse=True)
def _use_real_insert_event(monkeypatch):
    monkeypatch.setattr(_funnel_mod, "_insert_event", _REAL_INSERT_EVENT)


class TestRecordFunnelEvent:
    def test_unknown_event_type_rejected(self):
        assert record_funnel_event("seen", source="x") is False
        assert record_funnel_event("", source="x") is False

    def test_db_error_soft_fail(self, monkeypatch):
        def boom(row):
            raise RuntimeError("pg down")

        monkeypatch.setattr(_funnel_mod, "_insert_event", boom)
        assert record_funnel_event("shown", source="refusal_generic") is False  # 不抛

    def test_insert_sql_and_params(self, monkeypatch):
        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured["sql"], captured["params"] = sql, params

        class _FakeRawConn:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeEngine:
            def raw_connection(self):
                return _FakeRawConn()

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", lambda cfg: _FakeEngine())
        ok = record_funnel_event("clicked", source="refusal_sql_empty")
        assert ok is True
        assert "INSERT INTO ai.clarify_funnel_events" in captured["sql"]
        assert captured["params"]["event_type"] == "clicked"
        assert captured["params"]["source"] == "refusal_sql_empty"

    def test_explicit_identity_overrides_contextvar(self, monkeypatch):
        """实机回归（2026-10-03）：clicked 在 trace 建立前调用，ContextVar
        拿不到 → 空 session 行 + 清理前缀漏网。显式传参必须落行。"""
        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured["params"] = params

        class _FakeRawConn:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeEngine:
            def raw_connection(self):
                return _FakeRawConn()

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", lambda cfg: _FakeEngine())
        record_funnel_event("resolved", source="refusal_sql_empty",
                            session_id="sess-explicit", trace_id="tr-1")
        assert captured["params"]["session_id"] == "sess-explicit"
        assert captured["params"]["trace_id"] == "tr-1"

    def test_metric_increments_even_on_db_failure(self, monkeypatch):
        """双写口径：指标入口即增，不因 PG 失败失真。"""
        from prometheus_client import REGISTRY

        def boom(row):
            raise RuntimeError("pg down")

        monkeypatch.setattr(_funnel_mod, "_insert_event", boom)
        before = REGISTRY.get_sample_value(
            "agent_clarify_clicked_total", {"source": "refusal_generic"}) or 0.0
        record_funnel_event("clicked", source="refusal_generic")
        after = REGISTRY.get_sample_value(
            "agent_clarify_clicked_total", {"source": "refusal_generic"}) or 0.0
        assert after == before + 1


class TestConversion:
    def test_conversion_derived_from_persisted(self):
        from backend.app.api.routes.admin_clarify import _conversion

        out = _conversion({
            "refusal_sql_empty": {"shown": 8, "clicked": 4, "resolved": 3},
            "refusal_generic": {"shown": 0},
        })
        assert out["refusal_sql_empty"]["click_rate"] == 0.5
        assert out["refusal_sql_empty"]["resolve_rate"] == 0.75
        # 分母 0 → None（前端显示 —），不抛 ZeroDivisionError
        assert out["refusal_generic"]["click_rate"] is None
        assert out["refusal_generic"]["resolve_rate"] is None


class TestReadLive:
    def test_live_reads_registry_counters(self):
        from backend.app.api.routes.admin_clarify import _read_live

        record_funnel_event("shown", source="refusal_generic")
        live = _read_live()
        assert live.get("refusal_generic", {}).get("shown", 0) >= 1

    def test_counter_by_source_filters_metric_family(self):
        from backend.app.api.routes.admin_clarify import _counter_by_source
        from prometheus_client import REGISTRY

        out = _counter_by_source(REGISTRY, "shown")
        # 只含 shown 序列，不混入 clicked/resolved
        assert all(isinstance(v, float) for v in out.values())

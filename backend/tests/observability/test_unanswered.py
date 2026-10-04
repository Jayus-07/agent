"""未答问题旁路登记测试（2026-10-03 知识运营闭环）

1. writer：来源白名单 / 软失败（DB 异常返回 False 不抛）/ SQL 列值对齐 /
   问题哈希归一化
2. 漏斗口径：指标在入口即增（不因 PG 失败失真）
"""
from __future__ import annotations

import pytest

import backend.observability.unanswered as _unanswered_mod

# conftest 的 _unanswered_hermetic autouse 会在 fixture 阶段替换 _insert_row
# 防 PG 落库；本文件专测写入契约，import 时（fixture setup 之前）捕获真身，
# 再由下方文件级 autouse 恢复（conftest autouse 先于本文件 autouse 执行）。
_REAL_INSERT_ROW = _unanswered_mod._insert_row


@pytest.fixture(autouse=True)
def _use_real_insert_row(monkeypatch):
    monkeypatch.setattr(_unanswered_mod, "_insert_row", _REAL_INSERT_ROW)


class TestRecordUnansweredQuestion:
    def test_unknown_source_rejected(self):
        from backend.observability.unanswered import record_unanswered_question

        assert record_unanswered_question("任意问题", source="not_a_source") is False

    def test_empty_question_rejected(self):
        from backend.observability.unanswered import record_unanswered_question

        assert record_unanswered_question("   ", source="rag_miss") is False

    def test_db_error_soft_fail(self, monkeypatch):
        from backend.observability import unanswered as mod

        def boom(cfg):
            raise RuntimeError("pg down")

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", boom)
        assert mod.record_unanswered_question("什么时候放假",
                                              source="rag_miss") is False  # 不抛

    def test_insert_sql_and_params(self, monkeypatch):
        from backend.observability import unanswered as mod

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
        ok = mod.record_unanswered_question(
            "什么时候放假", source="rag_miss",
            department="general", kb_id="policy",
            detail={"route_mode": "direct"},
        )
        assert ok is True
        sql, params = captured["sql"], captured["params"]
        assert "INSERT INTO ai.unanswered_questions" in sql
        assert params["question"] == "什么时候放假"
        assert params["source"] == "rag_miss"
        assert params["department"] == "general" and params["kb_id"] == "policy"
        # 归一化哈希：去空白+小写，稳定可聚类
        assert len(params["question_hash"]) == 32
        assert '"route_mode": "direct"' in params["detail"] \
            or '"route_mode":"direct"' in params["detail"]

    def test_question_hash_normalized(self):
        from backend.observability.unanswered import _question_hash

        assert _question_hash("什么时候 放假") == _question_hash("  什么时候放假")
        assert _question_hash("ABC") == _question_hash("abc")

    def test_metric_increments_even_on_db_failure(self, monkeypatch):
        """漏斗口径：拒答量计数不因 PG 不可用失真（PG 只是明细）。"""
        from backend.observability import unanswered as mod
        from backend.observability.metrics import agent_unanswered_total
        from prometheus_client import REGISTRY

        before = REGISTRY.get_sample_value(
            "agent_unanswered_total", {"source": "rag_miss"}) or 0.0

        def boom(row):
            raise RuntimeError("pg down")

        monkeypatch.setattr(mod, "_insert_row", boom)
        mod.record_unanswered_question("测试拒答计数", source="rag_miss")
        after = REGISTRY.get_sample_value(
            "agent_unanswered_total", {"source": "rag_miss"}) or 0.0
        assert after == before + 1


class TestUnansweredSources:
    def test_sources_whitelist_matches_migration(self):
        """词表单一源：writer 白名单与迁移注释、metrics label 同口径。"""
        from backend.observability.unanswered import UNANSWERED_SOURCES

        assert set(UNANSWERED_SOURCES) == {"rag_miss", "sql_empty"}

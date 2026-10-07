"""tests/observability/test_question_ledger.py — 线上问题台账（2026-10-08 #13）

契约：
  - 写入旁路软失败：任何异常不抛；未知域/空问题丢弃
  - 问题截断 500 字、哈希按规范化文本稳定、同域同哈希 24h 去重
  - trace 适配器：#12 三分类推导域、plan 支线细分 planner、合成问题不收、
    selection_funnel/rag_agent 不冒充
  - 状态机只接受 accepted/dismissed；转候选调既有候选管道（redacted=True）
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.observability import question_ledger as ledger


class _FakeCursor:
    def __init__(self, fetchone_result=None, rowcount=1):
        self.executed: list[tuple[str, object]] = []
        self._fetchone_result = fetchone_result
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_connect(monkeypatch, cursor):
    conn = _FakeConn(cursor)
    monkeypatch.setattr(ledger, "_connect", lambda: conn)
    return conn


class TestRecordQuestion:
    def test_unknown_domain_and_empty_dropped(self, monkeypatch):
        cursor = _FakeCursor()
        _patch_connect(monkeypatch, cursor)
        assert ledger.record_question(domain="unknown_domain", question="x") is False
        assert ledger.record_question(domain="travel", question="  ") is False
        assert cursor.executed == []

    def test_truncates_and_hashes_stably(self, monkeypatch):
        cursor = _FakeCursor(fetchone_result=None)
        conn = _patch_connect(monkeypatch, cursor)
        long_q = "福州" * 500
        assert ledger.record_question(domain="travel", question=long_q) is True
        insert_sql, params = cursor.executed[-1]
        assert "INSERT INTO ai.question_ledger" in insert_sql
        assert len(params[4]) == ledger.QUESTION_MAX_LEN  # question 截断
        assert params[5] == ledger.question_hash(params[4])
        assert conn.commits == 1
        # 规范化空白后哈希一致
        assert ledger.question_hash("a b") == ledger.question_hash("  a   b ")

    def test_dedupe_within_window_skips_insert(self, monkeypatch):
        cursor = _FakeCursor(fetchone_result=(1,))  # 命中 24h 内同哈希
        conn = _patch_connect(monkeypatch, cursor)
        assert ledger.record_question(domain="cs", question="退款怎么走") is False
        # 只有去重 SELECT，没有 INSERT
        assert len(cursor.executed) == 1
        assert conn.commits == 0

    def test_db_failure_swallowed(self, monkeypatch):
        def boom():
            raise RuntimeError("pg down")

        monkeypatch.setattr(ledger, "_connect", boom)
        assert ledger.record_question(domain="travel", question="x") is False


class TestTraceAdapter:
    @staticmethod
    def _trace(**overrides):
        base = dict(
            question="福州有什么好玩的", answer_preview="鼓浪屿…",
            session_id="sess-1", id="t-1", workflow_name="agent",
            tags={"runtime_domain": "travel", "user_id": "u9",
                  "tenant_id": "default"},
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_domain_from_classifier_and_fields(self, monkeypatch):
        captured: dict = {}

        def fake_record(**kwargs):
            captured.update(kwargs)
            return True

        monkeypatch.setattr(ledger, "record_question", fake_record)
        assert ledger.record_question_from_trace(self._trace()) is True
        assert captured["domain"] == "travel"
        assert captured["user_id"] == "u9"
        assert captured["trace_id"] == "t-1"

    def test_plan_subflow_becomes_planner(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            ledger, "record_question",
            lambda **kw: captured.update(kw) or True)
        trace = self._trace(tags={"runtime_domain": "general",
                                  "runtime_execution_mode": "plan"})
        assert ledger.record_question_from_trace(trace) is True
        assert captured["domain"] == "planner"

    def test_main_graph_defaults_to_ai_assistant(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            ledger, "record_question",
            lambda **kw: captured.update(kw) or True)
        assert ledger.record_question_from_trace(self._trace(tags={})) is True
        assert captured["domain"] == "ai_assistant"

    def test_synthetic_and_unclassified_skipped(self, monkeypatch):
        recorded: list = []
        monkeypatch.setattr(
            ledger, "record_question", lambda **kw: recorded.append(kw) or True)
        # 守卫占位问题不收
        assert ledger.record_question_from_trace(
            self._trace(question="[cs-guard-rejected:policy]")) is False
        # 选品漏斗/独立 RAG 链不冒充三分类
        assert ledger.record_question_from_trace(
            self._trace(tags={"runtime_domain": "selection_funnel"})) is False
        assert ledger.record_question_from_trace(
            self._trace(workflow_name="rag_agent", tags={})) is False
        assert recorded == []

    def test_explicit_domain_override(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            ledger, "record_question",
            lambda **kw: captured.update(kw) or True)
        # travel 入口已掩码，走 domain 直传
        assert ledger.record_question_from_trace(
            self._trace(), domain="travel", source="travel") is True
        assert captured["domain"] == "travel"
        assert captured["source"] == "travel"


class TestStatusAndCandidates:
    def test_set_status_rejects_invalid(self, monkeypatch):
        cursor = _FakeCursor()
        _patch_connect(monkeypatch, cursor)
        assert ledger.set_status(1, "pending_review") is False

    def test_set_status_updates(self, monkeypatch):
        cursor = _FakeCursor(rowcount=1)
        _patch_connect(monkeypatch, cursor)
        assert ledger.set_status(7, "dismissed") is True

    def test_to_candidates_uses_existing_pipeline(self, monkeypatch):
        ledger_row = ("travel", "鼓浪屿怎么去", "坐轮渡", "u1", "t-9")
        cursor = _FakeCursor(fetchone_result=ledger_row)
        conn = _patch_connect(monkeypatch, cursor)
        captured: dict = {}

        def fake_register(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(candidate_id=kwargs["candidate_id"])

        monkeypatch.setattr(
            "backend.app.api.routes.evaluation_datasets.register_dataset_candidate",
            fake_register)
        out = ledger.to_candidates([9], reviewer="admin-x")
        assert out["converted"] == [
            {"ledger_id": 9, "candidate_id": "online-ledger-9"}]
        assert captured["module"] == "travel"
        assert captured["source_type"] == "online_ledger"
        assert captured["redacted"] is True
        assert captured["owner"] == "admin-x"
        assert captured["metadata"]["origin"] == "online_ledger"
        # 转候选后置 accepted
        assert any("SET status = %s" in sql for sql, _ in cursor.executed)
        assert conn.commits >= 1

    def test_to_candidates_missing_row_skipped(self, monkeypatch):
        cursor = _FakeCursor(fetchone_result=None)
        _patch_connect(monkeypatch, cursor)
        monkeypatch.setattr(
            "backend.app.api.routes.evaluation_datasets.register_dataset_candidate",
            lambda **kw: SimpleNamespace(candidate_id="x"))
        out = ledger.to_candidates([404])
        assert out["converted"] == []
        assert out["skipped"] == [404]

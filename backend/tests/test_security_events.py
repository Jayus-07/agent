"""安全事件统一落库测试（M9 / 台账 D9）

1. writer：白名单校验 / 软失败（DB 异常返回 False 不抛）/ SQL 列值对齐
2. 埋点接线：verify_access_token 失败分类 / guard BLOCK 落事件（BLOCK 之外不落）
"""
from __future__ import annotations

import pytest


class TestRecordSecurityEvent:
    def test_unknown_type_rejected(self):
        from backend.security.events import record_security_event

        assert record_security_event("NOT_A_TYPE") is False

    def test_db_error_soft_fail(self, monkeypatch):
        from backend.security import events as mod

        def boom(cfg):
            raise RuntimeError("pg down")

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", boom)
        assert mod.record_security_event("AUTHZ_DENIED") is False  # 不抛

    def test_insert_sql_and_params(self, monkeypatch):
        from backend.security import events as mod

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
        ok = mod.record_security_event(
            "EVIDENCE_REJECT", category="low_relevance",
            detail={"layer": "retrieval"},
            user_id="u1", tenant_id="t1",
        )
        assert ok is True
        sql, params = captured["sql"], captured["params"]
        assert "INSERT INTO ai.security_events" in sql
        assert params["event_type"] == "EVIDENCE_REJECT"
        assert params["category"] == "low_relevance"
        assert params["user_id"] == "u1" and params["tenant_id"] == "t1"
        assert '"layer": "retrieval"' in params["detail"] or '"layer":"retrieval"' in params["detail"]


class TestJWTEmission:
    def test_invalid_token_classified(self, monkeypatch):
        """伪造/过期/坏 token → JWT_INVALID 事件（category 区分原因）。"""
        from backend.security import local_jwt

        recorded: list[tuple] = []

        def fake_record(event_type, *, category="", detail=None, **kw):
            recorded.append((event_type, category))

        import backend.security.events as events_mod
        monkeypatch.setattr(events_mod, "record_security_event", fake_record)
        # 也 patch local_jwt 里局部导入的引用源（模块属性级 patch 即可生效）
        import backend.security as sec_pkg
        if not hasattr(sec_pkg, "events"):
            sec_pkg.events = events_mod

        assert local_jwt.verify_access_token("no-dots-token") is None
        assert ("JWT_INVALID", "malformed") in recorded

        assert local_jwt.verify_access_token("aaa.bbb.ccc") is None
        assert any(cat == "bad_signature" for _, cat in recorded)

    def test_valid_token_no_event(self, monkeypatch):
        from backend.security import local_jwt

        recorded: list = []

        def fake_record(event_type, **kw):
            recorded.append(event_type)

        monkeypatch.setattr(
            "backend.security.events.record_security_event", fake_record)
        token = local_jwt.issue_access_token(
            user_id=1, username="tester", tenant_id="t", roles=["viewer"],
            session_id="s")
        assert local_jwt.verify_access_token(token["token"]) is not None
        assert recorded == []


class TestGuardEmission:
    def test_block_records_event(self, monkeypatch):
        from backend.security.input_guard.guard import InputGuard
        from backend.security.input_guard.types import (
            GuardAction, GuardResult, RiskLevel,
        )
        import time as _time

        result = GuardResult(
            action=GuardAction.BLOCK, category=None, risk_level=RiskLevel.HIGH,
            confidence=0.9, layer="L1", needs_permission=False,
        ) if False else None
        # GuardResult 构造签名不确定时退化为 MagicMock 路径
        from unittest.mock import MagicMock
        result = MagicMock()
        result.action.value = "block"
        result.category.value = "prompt_injection"
        result.risk_level.value = "high"
        result.confidence = 0.9
        result.layer = "L1"
        result.policy_version = "v1"
        result.needs_permission = False
        result.domain = ""

        recorded: list[dict] = []

        def fake_record(event_type, *, category="", detail=None, **kw):
            recorded.append({"type": event_type, "category": category})

        monkeypatch.setattr(
            "backend.security.events.record_security_event", fake_record)
        guard = InputGuard.__new__(InputGuard)
        InputGuard._audit(guard, result, "sess-1", "恶意输入")
        assert recorded and recorded[0]["type"] == "INPUT_GUARD_BLOCK"
        assert recorded[0]["category"] == "prompt_injection"

    def test_allow_does_not_record(self, monkeypatch):
        from unittest.mock import MagicMock

        from backend.security.input_guard.guard import InputGuard

        result = MagicMock()
        result.action.value = "allow"
        # _audit 的日志 f-string 会格式化这些字段，mock 需给真值
        result.category.value = "normal"
        result.risk_level.value = "low"
        result.confidence = 0.1
        result.layer = "L0"
        result.policy_version = "v1"
        result.needs_permission = False
        result.domain = ""

        recorded: list = []
        monkeypatch.setattr(
            "backend.security.events.record_security_event",
            lambda *a, **kw: recorded.append(a))
        guard = InputGuard.__new__(InputGuard)
        InputGuard._audit(guard, result, "sess-1", "正常输入")
        assert recorded == []

"""tests/travel/test_decision_audit.py — 用户决策留痕（M4/G1）单测

decision_store 是薄 SQL 层（照 plan_store 模式），外部边界只有一个 PG
连接——按测试铁律 mock 掉 _conn/_ensure_table，验证：
  1. 决策类型白名单（非法类型拒绝落库，防御性）
  2. 留痕不可用（建表失败/缺 user_id/缺 conversation_id）→ 返回 0 软失败
  3. record 写成功返回行 id、参数按列裁剪
  4. record 写失败（PG 异常）→ 0 不抛（留痕绝不挡 UI 动作）
  5. list 的 user_id scope（越权 = 空列表由 SQL WHERE 保证，这里验证
     参数为空时短路）与行→dict 组装（jsonb 容错）
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from backend.travel.core import decision_store


class _FakeCursor:
    def __init__(self, rows=None, fail=False):
        self._rows = rows or []
        self._fail = fail
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self._fail:
            raise RuntimeError("pg down")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _patch_store(monkeypatch, cursor, *, table_ready=True):
    """把 decision_store 的 PG 边界换成 fake 连接。"""
    if not table_ready:
        # 建表失败口径：_ensure_table 恒 False（与真实降级路径同形）
        monkeypatch.setattr(decision_store, "_initialized", False)
        monkeypatch.setattr(decision_store, "_ensure_table", lambda: False)
        return

    # 表已就绪：_ensure_table 真实现查 _initialized 直接放行，不进建表 SQL
    monkeypatch.setattr(decision_store, "_initialized", True)

    @contextmanager
    def fake_conn():
        yield _FakeConn(cursor)

    monkeypatch.setattr(decision_store, "_conn", fake_conn)


@pytest.fixture(autouse=True)
def _reset_init_state(monkeypatch):
    """模块级 _initialized 是进程内单例状态，用例间必须复位。"""
    monkeypatch.setattr(decision_store, "_initialized", False)


class TestRecordDecision:
    def test_rejects_unknown_decision_type(self, monkeypatch):
        cursor = _FakeCursor(rows=[(1,)])
        _patch_store(monkeypatch, cursor)
        assert decision_store.record_decision(
            "u1", "c1", "hack_the_planet") == 0
        assert cursor.executed == []  # 白名单拒绝：SQL 都不进

    def test_skips_when_table_unavailable(self, monkeypatch):
        _patch_store(monkeypatch, _FakeCursor(), table_ready=False)
        assert decision_store.record_decision(
            "u1", "c1", "apply_draft") == 0

    def test_skips_when_missing_user_or_conversation(self, monkeypatch):
        cursor = _FakeCursor(rows=[(1,)])
        _patch_store(monkeypatch, cursor)
        assert decision_store.record_decision("", "c1", "apply_draft") == 0
        assert decision_store.record_decision("u1", "", "apply_draft") == 0
        assert cursor.executed == []

    def test_records_and_returns_row_id(self, monkeypatch):
        cursor = _FakeCursor(rows=[(42,)])
        _patch_store(monkeypatch, cursor)
        rid = decision_store.record_decision(
            "u1", "conv-1", "tier_switch",
            tenant_id="default", plan_version=3,
            tier_from="economy", tier_to="comfortable",
            payload={"message": "切舒适"}, source="tier_switch",
            client_run_id="client-1-abc",
        )
        assert rid == 42
        assert len(cursor.executed) == 1
        params = cursor.executed[0][1]
        assert params[0] == "default"
        assert params[2] == "conv-1"
        assert params[3] == "tier_switch"
        assert params[4] == 3
        assert params[7] == '{"message": "切舒适"}'

    def test_write_failure_returns_zero_not_raise(self, monkeypatch):
        # 留痕写失败绝不挡 UI 动作：PG 异常被吞成 0
        _patch_store(monkeypatch, _FakeCursor(fail=True))
        assert decision_store.record_decision(
            "u1", "c1", "apply_draft") == 0


class TestListDecisions:
    def test_empty_params_short_circuit(self, monkeypatch):
        cursor = _FakeCursor()
        _patch_store(monkeypatch, cursor)
        assert decision_store.list_decisions("", "c1") == []
        assert decision_store.list_decisions("u1", "") == []
        assert cursor.executed == []

    def test_rows_assembled_with_jsonb_fallback(self, monkeypatch):
        from datetime import datetime

        rows = [
            (1, datetime(2026, 10, 4, 10, 0, 0), "c1", "apply_draft", 2,
             "", "", '{"k": "v"}', "manual", "client-1"),
            (2, datetime(2026, 10, 4, 9, 0, 0), "c1", "discard_draft", 3,
             "", "", "not-json", "", ""),
        ]
        cursor = _FakeCursor(rows=rows)
        _patch_store(monkeypatch, cursor)
        out = decision_store.list_decisions("u1", "c1")
        assert [r["decision"] for r in out] == ["apply_draft", "discard_draft"]
        assert out[0]["payload"] == {"k": "v"}
        assert out[1]["payload"] == {}  # 坏 jsonb 容错为空 dict
        assert out[0]["created_at"] == "2026-10-04T10:00:00"

    def test_read_failure_returns_empty(self, monkeypatch):
        _patch_store(monkeypatch, _FakeCursor(fail=True))
        assert decision_store.list_decisions("u1", "c1") == []

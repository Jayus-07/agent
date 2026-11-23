"""Golden 标注 repository 单测（批次 6a）：append-only/幂等/状态门槛/UUID 清洗。

照 test_case_archive 的假 cursor 模式，不依赖真库；
红线依据：revision content_hash 覆盖 metadata_json，标注必须独立成表。
"""
from contextlib import contextmanager
import uuid as uuid_mod

import pytest

from backend.evaluation_v2 import repository

CASE_ID = "3f2b8c1a-5d4e-4f6a-9b7c-1a2b3c4d5e6f"


class GoldenCursor:
    """按 SQL 片段分发：case 状态查询 / 最新标注查询 / INSERT。"""

    def __init__(self, *, case_row=None, latest_mark=None, inserted=None):
        self.case_row = case_row
        self.latest_mark = latest_mark
        self.inserted = inserted if inserted is not None else []
        self.pending = None

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        if "FROM evaluation_v2.eval_cases" in normalized:
            self.pending = self.case_row
        elif "FROM evaluation_v2.eval_case_golden" in normalized and normalized.startswith("SELECT golden"):
            self.pending = self.latest_mark
        elif normalized.startswith("INSERT INTO evaluation_v2.eval_case_golden"):
            self.inserted.append(params)
            self.pending = {"mark_id": uuid_mod.uuid4(), "created_at": "2026-11-23T00:00:00Z"}
        elif "SELECT DISTINCT ON (case_id)" in normalized:
            self.pending = None
        else:
            raise AssertionError(f"未预期 SQL: {normalized}")

    def fetchone(self):
        row, self.pending = self.pending, None
        return row

    def fetchall(self):
        return []


@contextmanager
def transaction_for(cursor):
    yield object(), cursor


def install_transaction(monkeypatch, cursor):
    monkeypatch.setattr(repository, "_transaction", lambda _tenant_id: transaction_for(cursor))


def test_set_case_golden_inserts_mark_when_first_time(monkeypatch):
    cursor = GoldenCursor(case_row={"case_id": CASE_ID, "lifecycle_status": "published"})
    install_transaction(monkeypatch, cursor)
    result = repository.set_case_golden(
        "tenant-a", "user:alice", CASE_ID, golden=True, reason="人工核验通过",
    )
    assert result["changed"] is True
    assert result["golden"] is True
    assert result["reason"] == "人工核验通过"
    assert len(cursor.inserted) == 1
    assert cursor.inserted[0][:3] == ("tenant-a", CASE_ID, True)


def test_set_case_golden_idempotent_same_value(monkeypatch):
    cursor = GoldenCursor(
        case_row={"case_id": CASE_ID, "lifecycle_status": "published"},
        latest_mark={"golden": True},
    )
    install_transaction(monkeypatch, cursor)
    result = repository.set_case_golden("tenant-a", "user:alice", CASE_ID, golden=True)
    assert result == {"case_id": CASE_ID, "golden": True, "changed": False}
    assert cursor.inserted == []


def test_set_case_golden_toggles_back_adds_new_mark(monkeypatch):
    cursor = GoldenCursor(
        case_row={"case_id": CASE_ID, "lifecycle_status": "approved"},
        latest_mark={"golden": True},
    )
    install_transaction(monkeypatch, cursor)
    result = repository.set_case_golden("tenant-a", "user:alice", CASE_ID, golden=False)
    assert result["changed"] is True
    assert result["golden"] is False


def test_set_case_golden_rejects_missing_or_archived_case(monkeypatch):
    for case_row in (None, {"case_id": CASE_ID, "lifecycle_status": "archived"}):
        cursor = GoldenCursor(case_row=case_row)
        install_transaction(monkeypatch, cursor)
        with pytest.raises(repository.RepositoryNotFoundError):
            repository.set_case_golden("tenant-a", "user:alice", CASE_ID, golden=True)


def test_set_case_golden_rejects_invalid_uuid(monkeypatch):
    cursor = GoldenCursor()
    install_transaction(monkeypatch, cursor)
    with pytest.raises(ValueError):
        repository.set_case_golden("tenant-a", "user:alice", "not-a-uuid", golden=True)


def test_latest_golden_map_normalizes_and_maps(monkeypatch):
    other_id = str(uuid_mod.uuid4())
    captured = {}

    def fake_cursor_factory(tenant_id):
        captured["tenant"] = tenant_id

        @contextmanager
        def ctx():
            class Cur:
                def execute(self, query, params=None):
                    captured["params"] = params

                def fetchall(self):
                    return [
                        {"case_id": CASE_ID, "golden": True, "reason": "r", "marked_by": "user:1",
                         "created_at": __import__("datetime").datetime(2026, 11, 23)},
                    ]

            yield object(), Cur()
        return ctx()

    monkeypatch.setattr(repository, "_transaction", fake_cursor_factory)
    result = repository.latest_golden_map("tenant-a", [CASE_ID, "bad-id", other_id])
    # 非法 UUID 被清洗掉，合法两条都进查询参数
    assert len(captured["params"][1]) == 2
    assert result[CASE_ID] == {
        "golden": True, "reason": "r", "marked_by": "user:1",
        "created_at": "2026-11-23T00:00:00",
    }
    assert other_id not in result


def test_latest_golden_map_empty_input_short_circuits(monkeypatch):
    monkeypatch.setattr(
        repository, "_transaction",
        lambda _t: (_ for _ in ()).throw(AssertionError("空输入不得触库")),
    )
    assert repository.latest_golden_map("tenant-a", []) == {}

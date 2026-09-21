"""会话撤销服务契约测试。"""

from __future__ import annotations

import logging

import pytest

from backend.security.session_service import SessionService


class _Result:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        if self.fail and len(self.calls) == 2:
            raise RuntimeError("数据库不可用")
        sql = " ".join(str(statement).lower().split())
        if "select id, user_id" in sql:
            return _Result([
                {"id": "sid-1", "user_id": 7},
                {"id": "sid-2", "user_id": 7},
            ])
        if "select user_id" in sql:
            return _Result([{"user_id": 7}])
        return _Result()


class _Redis:
    def __init__(self):
        self.sets = {
            "auth:session_idx:7:sid-1": {"jti-a", "jti-b"},
            "auth:session_idx:7:sid-2": {"jti-c"},
        }
        self.deleted = []

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def delete(self, key):
        self.deleted.append(key)
        self.sets.pop(key, None)
        return 1


@pytest.mark.anyio
async def test_revoke_user_and_single_session_use_db_authority():
    db = _Db()
    redis = _Redis()
    service = SessionService(redis_getter=lambda: redis)

    refs = await service.revoke_user_sessions(db, 7, reason="role_changed")
    assert {ref.session_id for ref in refs} == {"sid-1", "sid-2"}
    assert any("UPDATE auth.sessions" in sql for sql, _ in db.calls)
    assert any("UPDATE auth.refresh_tokens" in sql for sql, _ in db.calls)

    single_db = _Db()
    ref = await service.revoke_session(single_db, "sid-1", reason="logout")
    assert ref.session_id == "sid-1"
    assert ref.user_id == 7

    service.clear_redis_for_sessions(refs)
    assert "auth:session:7:jti-a" in redis.deleted
    assert "auth:session_idx:7:sid-2" in redis.deleted


def test_redis_unavailable_does_not_undo_db_revocation(caplog):
    service = SessionService(redis_getter=lambda: None)
    caplog.set_level(logging.WARNING)

    service.clear_redis_for_sessions([
        service.session_ref("sid-1", 7),
    ])

    assert "Redis 不可用" in caplog.text


@pytest.mark.anyio
async def test_database_exception_is_not_swallowed():
    service = SessionService(redis_getter=lambda: _Redis())

    with pytest.raises(RuntimeError, match="数据库不可用"):
        await service.revoke_user_sessions(_Db(fail=True), 7, reason="role_changed")

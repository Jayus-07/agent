"""super_admin 运维 bootstrap 契约测试。"""

from __future__ import annotations

import asyncio

from backend.scripts import bootstrap_super_admin as bootstrap
from backend.security.session_service import SessionRef


def test_bootstrap_requires_tenant_and_exactly_one_selector():
    """缺租户或歧义用户选择会把提升操作施加到错误账号。"""

    assert bootstrap.main(["--tenant", "tenant-a"]) == 2
    assert bootstrap.main(
        ["--tenant", "tenant-a", "--username", "alice", "--user-id", "7"]
    ) == 2


def test_bootstrap_rejects_missing_user_without_creating_account(monkeypatch):
    """错误 selector 不得隐式创建账号或写入提权记录。"""

    class Result:
        def mappings(self):
            return self

        def first(self):
            return None

    class Db:
        rolled_back = False

        async def execute(self, *_args, **_kwargs):
            return Result()

        async def rollback(self):
            self.rolled_back = True

    db = Db()

    async def fake_sessions():
        yield db

    monkeypatch.setattr(bootstrap, "get_session", fake_sessions)

    try:
        asyncio.run(bootstrap.bootstrap_super_admin(
            tenant_id="tenant-a",
            username="missing",
            user_id=None,
        ))
    except bootstrap.BootstrapError as exc:
        assert "不存在" in str(exc)
    else:  # pragma: no cover - 明确保护错误分支
        raise AssertionError("不存在用户必须失败")

    assert db.rolled_back is True


def test_bootstrap_writes_audit_revokes_sessions_and_clears_redis(monkeypatch):
    """提升必须同时留下审计并清除旧 access/refresh 会话权威。"""

    class Result:
        def __init__(self, row=None):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class Db:
        def __init__(self):
            self.statements = []
            self.committed = False

        async def execute(self, statement, params=None):
            self.statements.append((str(statement), params or {}))
            if len(self.statements) == 1:
                return Result({"id": 7, "username": "alice", "role": "admin"})
            return Result()

        async def commit(self):
            self.committed = True

    class Service:
        def __init__(self):
            self.cleared = []

        async def revoke_user_sessions(self, db, user_id, *, reason):
            assert user_id == 7
            assert reason == "bootstrap_super_admin"
            return [SessionRef(session_id="sid-7", user_id=7)]

        def clear_redis_for_sessions(self, refs):
            self.cleared.extend(refs)
            return 1

    db = Db()
    service = Service()

    async def fake_sessions():
        yield db

    monkeypatch.setattr(bootstrap, "get_session", fake_sessions)

    result = asyncio.run(bootstrap.bootstrap_super_admin(
        tenant_id="tenant-a",
        username="alice",
        user_id=None,
        session_service=service,
    ))

    assert result.changed is True
    assert db.committed is True
    assert service.cleared == [SessionRef(session_id="sid-7", user_id=7)]
    sql = "\n".join(statement for statement, _ in db.statements)
    assert "UPDATE auth.users SET role = 'super_admin'" in sql
    assert "user.bootstrap_super_admin" in sql

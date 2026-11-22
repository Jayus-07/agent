"""客服坐席认领与归属落库回归测试。"""

import pytest
from fastapi import HTTPException

from backend.app.api.routes import cs_admin


class _HandoffRow:
    conversation_id = "conv-1"
    user_id = "user-1"
    tenant_id = "default"
    handoff_state = "waiting_human"


class _Result:
    def __init__(self, row=None, rowcount=None, scalar=None):
        self._row = row
        self.rowcount = rowcount
        self._scalar = scalar

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._scalar


class _FakeDB:
    def __init__(self):
        self.execute_count = 0
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        db = self

        class _Transaction:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *args):
                db.committed = True
                return False

        return _Transaction()

    async def execute(self, query):
        self.execute_count += 1
        if self.execute_count == 1:
            return _Result(row=_HandoffRow())
        if self.execute_count == 2:
            return _Result(rowcount=1)
        raise AssertionError("认领不应绕过 ConversationManager 再直接写状态")

    async def commit(self):
        self.committed = True

    def add(self, _entity):
        """Outbox 事件由事务统一提交；桩只需接受 session.add。"""
        return None


class _FakeConversationManager:
    calls = []

    def __init__(self, db):
        self.db = db

    async def get_or_create(self, conversation_id, user_id, **kwargs):
        self.calls.append(
            ("get_or_create", conversation_id, user_id, kwargs.get("tenant_id"))
        )
        return object(), False

    async def escalate_to_human(self, conversation_id, agent_id, *, assigned_by):
        self.calls.append(
            ("escalate_to_human", conversation_id, agent_id, assigned_by)
        )


@pytest.mark.asyncio
async def test_claim_persists_conversation_assignment(monkeypatch):
    """认领必须通过会话管理器写入 assigned_agent_id 与 assignment。"""
    db = _FakeDB()
    _FakeConversationManager.calls = []

    monkeypatch.setattr(
        "backend.memory.database.AsyncSessionLocal", lambda: db
    )
    monkeypatch.setattr(
        "backend.customer_service.managers.conversation_manager.ConversationManager",
        _FakeConversationManager,
    )
    monkeypatch.setattr(
        cs_admin, "_ensure_cs_agent", _fake_ensure_cs_agent, raising=False
    )
    monkeypatch.setattr(
        "backend.customer_service.handoff_store.get_handoff_store",
        lambda: _FakeStore(),
    )
    monkeypatch.setattr(
        "backend.customer_service.realtime.get_agent_hub",
        lambda: _FakeHub(),
    )

    result = await cs_admin._async_claim("conv-1", "agent-1", None, None)

    assert result == {
        "conversation_id": "conv-1",
        "handoff_state": "human_active",
        "agent_id": "agent-1",
        "already_claimed": False,
    }
    assert _FakeConversationManager.calls == [
        ("get_or_create", "conv-1", "user-1", "default"),
        ("escalate_to_human", "conv-1", "agent-1", "agent-1"),
    ]
    assert db.committed is True


def test_agent_actions_require_current_assignment():
    """人工消息/结束会话不能由未认领或其他坐席代发。"""
    with pytest.raises(HTTPException) as missing:
        cs_admin._assert_current_agent(None, "agent-1")
    assert missing.value.status_code == 409

    with pytest.raises(HTTPException) as other:
        cs_admin._assert_current_agent("agent-2", "agent-1")
    assert other.value.status_code == 409

    cs_admin._assert_current_agent("agent-1", "agent-1")


async def _fake_ensure_cs_agent(db, agent_id):
    return None


class _FakeStore:
    def invalidate(self, user_id, conversation_id):
        return None


class _FakeHub:
    def publish(self, event_type, **payload):
        return None


class _RaceResult:
    def __init__(self, *, row=None, rowcount=None, scalar=None):
        self._row = row
        self.rowcount = rowcount
        self._scalar = scalar

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._scalar


class _RaceDB:
    """模拟赢家尚未提交 assignment 时，失败方看到的 DB 快照。"""

    def __init__(self):
        self.execute_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        class _Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        return _Transaction()

    async def execute(self, query):
        self.execute_count += 1
        if self.execute_count == 1:
            return _RaceResult(row=_HandoffRow())
        if self.execute_count == 2:
            return _RaceResult(rowcount=0)
        if self.execute_count == 3:
            return _RaceResult(scalar="human_active")
        if self.execute_count == 4:
            return _RaceResult(scalar=None)
        raise AssertionError("竞态失败方不应继续写入会话或 assignment")


@pytest.mark.asyncio
async def test_claim_race_without_visible_assignment_is_not_success(monkeypatch):
    """赢家未提交 assignment 时，失败方必须返回 409 而非伪成功。"""
    db = _RaceDB()

    monkeypatch.setattr(
        "backend.memory.database.AsyncSessionLocal", lambda: db
    )

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._async_claim("conv-1", "agent-2", None, None)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail


# ── 2026-11-08 跨租户审计（cs-claim-close.missing-tenant-predicate）──


class _CapturingDB(_FakeDB):
    """记录发出的 SQL，用于断言租户谓词真的落到查询上。"""

    def __init__(self):
        super().__init__()
        self.statements: list[str] = []

    async def execute(self, query):
        self.statements.append(str(query))
        return await super().execute(query)


@pytest.mark.asyncio
async def test_claim_scopes_handoff_queries_to_tenant(monkeypatch):
    """JWT 坐席认领时 handoff 读取/条件更新必须带 tenant_id。"""
    db = _CapturingDB()
    _FakeConversationManager.calls = []

    monkeypatch.setattr("backend.memory.database.AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "backend.customer_service.managers.conversation_manager.ConversationManager",
        _FakeConversationManager,
    )
    monkeypatch.setattr(
        cs_admin, "_ensure_cs_agent", _fake_ensure_cs_agent, raising=False
    )
    monkeypatch.setattr(
        "backend.customer_service.handoff_store.get_handoff_store",
        lambda: _FakeStore(),
    )

    await cs_admin._async_claim("conv-1", "agent-1", "tenant-a", None)

    assert db.statements, "认领必须至少执行一次 handoff 查询"
    assert all(":tenant_id_" in stmt for stmt in db.statements)


@pytest.mark.asyncio
async def test_claim_without_tenant_keeps_service_channel_scope(monkeypatch):
    """API-Key 服务通道（tenant=None）保持既有跨租户语义，不加租户谓词。"""
    db = _CapturingDB()
    _FakeConversationManager.calls = []

    monkeypatch.setattr("backend.memory.database.AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "backend.customer_service.managers.conversation_manager.ConversationManager",
        _FakeConversationManager,
    )
    monkeypatch.setattr(
        cs_admin, "_ensure_cs_agent", _fake_ensure_cs_agent, raising=False
    )
    monkeypatch.setattr(
        "backend.customer_service.handoff_store.get_handoff_store",
        lambda: _FakeStore(),
    )

    await cs_admin._async_claim("conv-1", "agent-1", None, None)

    assert db.statements
    assert all(":tenant_id_" not in stmt for stmt in db.statements)


class _JwtIdentity:
    authenticated = True
    user_id = "user-1"
    tenant_id = "tenant-a"
    roles = ("agent",)


class _Request:
    def __init__(self, headers=None):
        self.headers = headers or {}


@pytest.mark.asyncio
async def test_resolve_agent_identity_returns_trusted_tenant(monkeypatch):
    """JWT 通道返回可信租户，供认领/关闭/发言加租户谓词。"""
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity", lambda _r: _JwtIdentity()
    )

    async def _lookup(**_kwargs):
        return "agent-1"

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", _lookup)

    agent_id, tenant_id = await cs_admin._resolve_agent_identity_and_tenant(
        _Request(), ""
    )
    assert (agent_id, tenant_id) == ("agent-1", "tenant-a")


@pytest.mark.asyncio
async def test_resolve_agent_identity_fail_closed_without_tenant(monkeypatch):
    """JWT 坐席缺少可信租户时 403，不回落 default。"""

    class _NoTenant(_JwtIdentity):
        tenant_id = ""

    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity", lambda _r: _NoTenant()
    )

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._resolve_agent_identity_and_tenant(_Request(), "")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_agent_identity_service_channel_has_no_tenant():
    """API-Key 服务通道返回声明的 agent_id 且不带租户范围。"""
    from backend.config.auth import AUTH_TYPE_HEADER

    agent_id, tenant_id = await cs_admin._resolve_agent_identity_and_tenant(
        _Request({AUTH_TYPE_HEADER: "api-key"}), "agent-svc"
    )
    assert (agent_id, tenant_id) == ("agent-svc", None)


# ── D1/D2（2026-11-22 实机验证）──


@pytest.mark.asyncio
async def test_claim_update_writes_handoff_assignment(monkeypatch):
    """D1：认领的原子 UPDATE 必须同步写 handoffs.assigned_agent_id。

    之前只写 handoff_state，分配关系只落 conversations 表，mine=true
    （handoff_repo 谓词查 handoffs 表）永远查不到 →「我的会话」恒空、
    F5 刷新即丢。本用例钉住写入侧，防回归。
    """
    db = _CapturingDB()
    _FakeConversationManager.calls = []

    monkeypatch.setattr("backend.memory.database.AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "backend.customer_service.managers.conversation_manager.ConversationManager",
        _FakeConversationManager,
    )
    monkeypatch.setattr(
        cs_admin, "_ensure_cs_agent", _fake_ensure_cs_agent, raising=False
    )
    monkeypatch.setattr(
        "backend.customer_service.handoff_store.get_handoff_store",
        lambda: _FakeStore(),
    )
    monkeypatch.setattr(
        "backend.customer_service.realtime.get_agent_hub",
        lambda: _FakeHub(),
    )

    await cs_admin._async_claim("conv-1", "agent-1", None, None)

    updates = [s for s in db.statements if "UPDATE" in s.upper()]
    assert updates, "认领必须发出 UPDATE 语句"
    assert any(
        "assigned_agent_id" in s for s in updates
    ), "UPDATE 必须写 handoffs.assigned_agent_id（D1 回归）"


@pytest.mark.asyncio
async def test_save_my_message_takes_latest_handoff(monkeypatch):
    """D2：人工态发消息的 handoff 归属查询必须取最新一条。

    多轮转人工的会话有多条 handoff 行，裸 scalar_one_or_none() 抛
    MultipleRows → 用户发消息 503。
    """
    class _ScalarResult:
        def __init__(self, scalar=None):
            self._scalar = scalar

        def scalar_one_or_none(self):
            return self._scalar

    class _SaveMsgDB(_FakeDB):
        def __init__(self):
            super().__init__()
            self.select_statements: list[str] = []

        async def execute(self, query):
            self.execute_count += 1
            if self.execute_count == 1:
                self.select_statements.append(str(query))
                return _ScalarResult(None)
            raise AssertionError("归属校验后不应再查 handoff")

        async def add(self, _entity):
            return None

        async def flush(self):
            return None

    class _FakeMessageManager:
        def __init__(self, db):
            self.db = db

        async def save_user_message(self, conversation_id, content, **kw):
            class _Msg:
                id = 42

            return _Msg()

    db = _SaveMsgDB()
    monkeypatch.setattr("backend.memory.database.AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "backend.customer_service.managers.message_manager.MessageManager",
        _FakeMessageManager,
    )

    await cs_admin._async_save_my_message(
        "conv-1", "user-1", "default", "你好", None
    )

    assert db.select_statements, "必须先查 handoff 做归属校验"
    stmt = db.select_statements[0]
    normalized = " ".join(stmt.upper().split())
    assert "ORDER BY" in normalized and "DESC" in normalized, (
        "handoff 归属查询必须按 id 倒序取最新一条（D2 回归）"
    )
    assert "LIMIT" in normalized, "handoff 归属查询必须 LIMIT 1（LIMIT 值为参数化占位）"
    assert "CS_HANDOFFS" in normalized or "handoff" in normalized.lower()


@pytest.mark.asyncio
async def test_save_my_message_rejects_foreign_user(monkeypatch):
    """归属校验语义不变：handoff.user_id 与当前用户不一致 → 403。"""

    class _ForeignHandoffRow:
        user_id = "user-999"

    class _ForeignDB(_FakeDB):
        def __init__(self):
            super().__init__()
            self.execute_count = 0

        async def execute(self, query):
            self.execute_count += 1
            if self.execute_count == 1:
                # 归属校验走 scalar_one_or_none()：handoff 行必须经 scalar 传回
                return _Result(scalar=_ForeignHandoffRow())
            raise AssertionError("403 后不应继续写消息")

    db = _ForeignDB()
    monkeypatch.setattr("backend.memory.database.AsyncSessionLocal", lambda: db)

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._async_save_my_message(
            "conv-1", "user-1", "default", "你好", None
        )
    assert exc_info.value.status_code == 403



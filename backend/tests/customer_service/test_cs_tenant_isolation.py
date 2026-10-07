"""STOP CS-A P0-2 回归：CS 租户隔离单元测试。

覆盖：ConversationManager.create fail-closed / HandoffRepository.list_open
租户谓词 / AgentHub._broadcast 租户过滤（tenant 单条件 + target 双条件）/
realtime 事件落库透传租户。
"""
from __future__ import annotations

import pytest

from backend.customer_service.errors import ValidationError
from backend.customer_service.managers.conversation_manager import (
    ConversationManager,
)


class _StubSession:
    """最小 AsyncSession 桩：flush 无操作，execute 由用例注入。"""

    def __init__(self, execute_result=None):
        self._execute_result = execute_result
        self.added = []
        self.executed = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self._execute_result


class TestConversationManagerTenant:

    @pytest.mark.asyncio
    async def test_create_without_tenant_fail_closed(self):
        """缺可信 tenant → ValidationError，绝不静默落 default（P0-2 B1）。"""
        mgr = ConversationManager(_StubSession())
        with pytest.raises(ValidationError):
            await mgr.create("user-1", tenant_id="")

    @pytest.mark.asyncio
    async def test_create_with_tenant_persists_value(self):
        session = _StubSession()
        mgr = ConversationManager(session)
        conv = await mgr.create("user-1", tenant_id="tenant-b")
        assert conv.tenant_id == "tenant-b"
        assert session.added and session.added[0].tenant_id == "tenant-b"


class TestHandoffRepoTenantPredicate:

    @pytest.mark.asyncio
    async def test_list_open_carries_tenant_predicate(self):
        from sqlalchemy.dialects import postgresql

        from backend.customer_service.repository.handoff_repo import (
            HandoffRepository,
        )

        captured = {}

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return []

        class _Session(_StubSession):
            async def execute(self, stmt):
                captured["sql"] = str(
                    stmt.compile(
                        dialect=postgresql.dialect(),
                        compile_kwargs={"literal_binds": True},
                    )
                )
                return _Result()

        await HandoffRepository(_Session()).list_open(tenant_id="tenant-b")
        where_part = captured["sql"].split("WHERE", 1)[-1]
        assert "handoffs.tenant_id" in where_part
        assert "'tenant-b'" in where_part.replace(" ", "")

    @pytest.mark.asyncio
    async def test_list_open_without_tenant_keeps_cross_view(self):
        """服务间跨租户视图（tenant_id=None）不拼租户条件。"""
        from sqlalchemy.dialects import postgresql

        from backend.customer_service.repository.handoff_repo import (
            HandoffRepository,
        )

        captured = {}

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return []

        class _Session(_StubSession):
            async def execute(self, stmt):
                captured["sql"] = str(
                    stmt.compile(
                        dialect=postgresql.dialect(),
                        compile_kwargs={"literal_binds": True},
                    )
                )
                return _Result()

        await HandoffRepository(_Session()).list_open()
        # SELECT 列表本就含 tenant_id 列 —— 断言口径 = 无租户 WHERE 谓词
        where_part = captured["sql"].split("WHERE", 1)[-1]
        assert "handoffs.tenant_id" not in where_part


class TestAgentHubTenantBroadcast:
    """WS 信封租户过滤（P0-2 B3）：tenant 不匹配不投递；target 双条件。"""

    def _hub_with_conns(self, identities):
        from backend.customer_service.realtime import AgentHub

        hub = AgentHub()

        class _WS:
            def __init__(self, name):
                self.name = name

            async def send_text(self, data):
                self.sent = data

        conns = []
        for name, (tenant, agent) in identities.items():
            ws = _WS(name)
            hub.register_connection(ws, agent_id=agent, tenant_id=tenant)
            conns.append(ws)
        return hub, conns

    @pytest.mark.asyncio
    async def test_message_created_not_delivered_cross_tenant(self):
        """无 target 的 message.created（带租户）不得投给其他租户坐席。"""
        import json

        hub, conns = self._hub_with_conns({
            "a": ("tenant-a", "agent-a"),
            "b": ("tenant-b", "agent-b"),
        })
        envelope = {
            "type": "message.created",
            "event_id": "e1",
            "seq": 1,
            "ts": "t",
            "tenant_id": "tenant-a",
            "conversation_id": "c1",
        }
        await hub._broadcast(json.dumps(envelope))
        assert conns[0].name == "a" and "sent" in conns[0].__dict__
        assert "sent" not in conns[1].__dict__

    @pytest.mark.asyncio
    async def test_target_agent_double_condition(self):
        """target_agent_id 存在 → tenant AND agent 双匹配才投递。"""
        import json

        hub, conns = self._hub_with_conns({
            "a": ("tenant-a", "agent-a"),
            "b": ("tenant-a", "agent-b"),   # 同租户不同坐席
            "c": ("tenant-b", "agent-a"),   # 同坐席 ID 不同租户
        })
        envelope = {
            "type": "conversation.offered",
            "event_id": "e2",
            "seq": 2,
            "ts": "t",
            "tenant_id": "tenant-a",
            "target_agent_id": "agent-a",
        }
        await hub._broadcast(json.dumps(envelope))
        assert "sent" in conns[0].__dict__
        assert "sent" not in conns[1].__dict__
        assert "sent" not in conns[2].__dict__

    @pytest.mark.asyncio
    async def test_envelope_without_tenant_keeps_legacy_broadcast(self):
        """无租户信封（内部/旧测试路径）保持原有广播行为。"""
        import json

        hub, conns = self._hub_with_conns({
            "a": ("tenant-a", "agent-a"),
            "b": ("tenant-b", "agent-b"),
        })
        envelope = {"type": "hello", "event_id": "e3", "seq": None, "ts": "t"}
        await hub._broadcast(json.dumps(envelope))
        assert "sent" in conns[0].__dict__
        assert "sent" in conns[1].__dict__


class TestRealtimePersistTenant:

    @pytest.mark.asyncio
    async def test_persist_event_passes_tenant(self, monkeypatch):
        """_persist_event 从信封 payload 透传租户给 EventRepository.append。"""
        captured = {}

        class _Repo:
            def __init__(self, db):
                pass

            async def append(self, **kw):
                captured.update(kw)
                return 1

        class _DB:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal", lambda: _DB()
        )
        monkeypatch.setattr(
            "backend.customer_service.repository.event_repo.EventRepository",
            _Repo,
        )

        from backend.customer_service.realtime import AgentHub

        hub = AgentHub()
        await hub._persist_event(
            {"conversation_id": "c1", "tenant_id": "tenant-a"},
            {"type": "message.created", "event_id": "e9"},
        )
        assert captured["tenant_id"] == "tenant-a"
        assert captured["conversation_id"] == "c1"

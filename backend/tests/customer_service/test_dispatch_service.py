"""P6 自动派单事务与 repository 契约测试。

本文件覆盖两类断言：

1. **SQL 契约**（编译后的 PostgreSQL 语句）：租户隔离、优先级/FIFO 顺序、
   `FOR UPDATE SKIP LOCKED`、坐席按活动负载 + 轮询排序、容量上限谓词。
2. **事务语义**（脚本化 fake session）：无工单/无候选/Redis 故障不改动工单；
   成功派单在一个事务内完成 handoff + assignment + 会话投影 + 持久事件；
   提交后才广播；广播失败不回滚。

真实 PostgreSQL 上的并发绑定与容量验收在本环境缺少 P1 字段（028 迁移未
应用到共享库），按 TDD 纪律显式 skip 并记录为部署门槛，不用内存 fake 冒充。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from backend.config.cs_dispatch import CS_OFFER_TIMEOUT_SECONDS
from backend.customer_service.dispatch import event_relay, presence, repository, service
from backend.services import sys_config
from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import CSHandoff

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
TENANT = "tenant-a"


# ── SQL 契约 ────────────────────────────────────────────────


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    )


def _normalized_sql(statement) -> str:
    return " ".join(_sql(statement).split()).lower()


def test_handoff_queue_query_is_tenant_scoped_and_priority_ordered() -> None:
    stmt = repository.next_waiting_handoff_stmt(tenant_id=TENANT, now=NOW)
    sql = _normalized_sql(stmt)

    assert "from customer_service.handoffs" in sql
    assert "handoffs.tenant_id = " in sql
    assert "handoffs.handoff_state = " in sql

    order_clause = sql.split("order by", 1)[1]
    assert order_clause.index("handoffs.priority desc") < order_clause.index(
        "handoffs.created_at asc"
    )
    assert order_clause.index("handoffs.created_at asc") < order_clause.index(
        "handoffs.id asc"
    )
    assert "limit" in sql
    # 预读不加锁：否则同一队头会被所有 dispatcher 串行争抢。
    assert "for update" not in sql


def test_handoff_queue_query_skips_past_deadline_rows() -> None:
    sql = _normalized_sql(repository.next_waiting_handoff_stmt(tenant_id=TENANT, now=NOW))

    assert "total_deadline_at is null" in sql
    assert "total_deadline_at > " in sql


def test_handoff_lock_query_is_scoped_to_one_conversation() -> None:
    stmt = repository.lock_waiting_handoff_stmt(
        tenant_id=TENANT, conversation_id="conv-1", now=NOW
    )
    sql = _normalized_sql(stmt)

    assert "handoffs.conversation_id = " in sql
    assert "handoffs.tenant_id = " in sql
    assert "for update skip locked" in sql


def test_conversation_lock_query_is_tenant_scoped() -> None:
    stmt = repository.lock_conversation_stmt(tenant_id=TENANT, conversation_id="conv-1")
    sql = _normalized_sql(stmt)

    assert "from customer_service.conversations" in sql
    assert "conversations.conversation_id = " in sql
    assert "conversations.tenant_id = " in sql
    assert "for update skip locked" in sql


def test_agent_selection_query_locks_rows_and_orders_by_load_then_round_robin() -> None:
    stmt = repository.least_loaded_agent_stmt(
        tenant_id=TENANT, online_agent_ids=["agent-1", "agent-2"]
    )
    sql = _normalized_sql(stmt)

    assert "from customer_service.cs_agents" in sql
    assert "cs_agents.tenant_id = " in sql
    assert "cs_agents.enabled is true" in sql
    assert "cs_agents.available is true" in sql
    assert "cs_agents.accepting is true" in sql
    assert "cs_agents.agent_id in " in sql
    assert "for update of cs_agents skip locked" in sql
    # 活动 assignment（offered|accepted）只在同一租户内计数
    assert "from customer_service.assignments" in sql
    assert "assignments.state in " in sql
    assert "assignments.tenant_id = customer_service.cs_agents.tenant_id" in sql
    assert "assignments.agent_id = customer_service.cs_agents.agent_id" in sql
    # 容量上限谓词：active_count < max_conversations
    assert "< customer_service.cs_agents.max_conversations" in sql
    assert "limit" in sql


def test_agent_selection_query_orders_by_load_then_last_assigned_then_agent_id() -> None:
    sql = _normalized_sql(
        repository.least_loaded_agent_stmt(
            tenant_id=TENANT, online_agent_ids=["agent-b", "agent-a"]
        )
    )
    order_clause = sql.split("order by", 1)[1]

    load_index = order_clause.index("select count(*)")
    last_assigned_index = order_clause.index("cs_agents.last_assigned_at asc nulls first")
    agent_id_index = order_clause.index("cs_agents.agent_id asc")

    assert load_index < last_assigned_index < agent_id_index


def test_agent_selection_only_counts_offered_and_accepted_assignments() -> None:
    stmt = repository.least_loaded_agent_stmt(tenant_id=TENANT, online_agent_ids=["a"])
    params = stmt.compile(dialect=postgresql.dialect()).params

    assert params["state_1"] == ["offered", "accepted"]
    assert "declined" not in params["state_1"]
    assert "expired" not in params["state_1"]
    assert "closed" not in params["state_1"]


def test_agent_selection_filters_by_the_online_candidate_set() -> None:
    stmt = repository.least_loaded_agent_stmt(
        tenant_id=TENANT, online_agent_ids=["agent-2", "agent-1"]
    )
    params = stmt.compile(dialect=postgresql.dialect()).params

    # 候选集合来自 Redis presence 判定，必须以绑定参数进入 IN 子句。
    assert params["agent_id_1"] == ["agent-2", "agent-1"]
    assert params["tenant_id_1"] == TENANT


def test_tenant_head_query_returns_global_queue_order() -> None:
    stmt = repository.waiting_tenant_heads_stmt(limit=10)
    sql = _normalized_sql(stmt)

    assert "distinct on (customer_service.handoffs.tenant_id)" in sql
    assert "handoffs.handoff_state = " in sql

    order_clause = sql.split("order by", 1)[1]
    assert order_clause.index("anon_1.priority desc") < order_clause.index(
        "anon_1.created_at asc"
    )
    assert order_clause.index("anon_1.created_at asc") < order_clause.index(
        "anon_1.id asc"
    )
    assert "limit" in sql


# ── 事务语义 ────────────────────────────────────────────────


class _Txn:
    def __init__(self, session: "FakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> "FakeSession":
        self._session.record("begin")
        return self._session

    async def __aexit__(self, exc_type, _exc, _tb) -> bool:
        self._session.record("rollback" if exc_type is not None else "commit")
        return False


class FakeSession:
    """只实现 dispatch_once 用到的会话面：begin/add/flush。"""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.records: list[str] = []

    def record(self, name: str) -> None:
        self.records.append(name)

    def begin(self) -> _Txn:
        return _Txn(self)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.record("flush")


def _handoff(**overrides) -> CSHandoff:
    values = dict(
        handoff_id=uuid.uuid4().hex,
        conversation_id="conv-1",
        user_id="user-1",
        tenant_id=TENANT,
        handoff_state="waiting_human",
        trigger_type="explicit_request",
        priority=50,
        assignment_version=0,
        attempt_count=0,
        created_at=NOW,
        updated_at=NOW,
        total_deadline_at=NOW + timedelta(seconds=600),
    )
    values.update(overrides)
    return CSHandoff(**values)


def _conversation(**overrides) -> CSConversation:
    values = dict(
        conversation_id="conv-1",
        user_id="user-1",
        tenant_id=TENANT,
        conversation_status="open",
        handling_mode="waiting_human",
        assigned_agent_id=None,
    )
    values.update(overrides)
    return CSConversation(**values)


def _agent(**overrides) -> CSAgent:
    values = dict(
        agent_id="agent-1",
        tenant_id=TENANT,
        display_name="坐席一",
        enabled=True,
        available=True,
        accepting=True,
        max_conversations=10,
        last_assigned_at=None,
    )
    values.update(overrides)
    return CSAgent(**values)


class Scenario:
    """按需覆盖单个 repository/presence 步骤，未覆盖的走默认成功路径。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.session = FakeSession()
        self.handoff = _handoff()
        self.conversation = _conversation()
        self.agent = _agent()
        self.candidate_ids = ["agent-1"]
        self.online: set[str] | None = {"agent-1"}
        self.published: list[dict] = []
        self.publish_error: Exception | None = None
        self.calls: list[str] = []

        self._install(monkeypatch)

    def _install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def find_candidate(*_a, **_k):
            self.calls.append("find_candidate")
            return self.handoff

        async def lock_conversation(*_a, **_k):
            self.calls.append("lock_conversation")
            return self.conversation

        async def lock_handoff(*_a, **_k):
            self.calls.append("lock_handoff")
            return self.handoff

        async def list_agent_ids(*_a, **_k):
            self.calls.append("list_agent_ids")
            return list(self.candidate_ids)

        async def lock_agent(*_a, **_k):
            self.calls.append("lock_agent")
            return self.agent

        async def online_agent_ids(*_a, **_k):
            self.calls.append("presence")
            return self.online

        async def busy_agent_ids(*_a, **_k):
            # 默认无人置忙（隔离真实 Redis）；置忙场景在用例内覆盖。
            return set()

        def publish(envelope):
            self.calls.append("publish")
            if self.publish_error is not None:
                raise self.publish_error
            self.published.append(envelope)
            return True

        monkeypatch.setattr(
            repository, "find_dispatchable_handoff_candidate", find_candidate
        )
        monkeypatch.setattr(repository, "lock_conversation", lock_conversation)
        monkeypatch.setattr(repository, "lock_dispatchable_handoff", lock_handoff)
        monkeypatch.setattr(repository, "list_accepting_agent_ids", list_agent_ids)
        monkeypatch.setattr(repository, "lock_least_loaded_agent", lock_agent)
        monkeypatch.setattr(presence, "online_agent_ids", online_agent_ids)
        monkeypatch.setattr(service.agent_busy, "busy_agent_ids", busy_agent_ids)
        monkeypatch.setattr(event_relay, "publish_persisted_event", publish)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch) -> Scenario:
    return Scenario(monkeypatch)


async def _dispatch(scenario: Scenario):
    return await service.dispatch_once(
        scenario.session, tenant_id=TENANT, now=NOW
    )


async def test_empty_queue_is_a_noop(scenario: Scenario, monkeypatch) -> None:
    async def no_candidate(*_a, **_k):
        return None

    monkeypatch.setattr(
        repository, "find_dispatchable_handoff_candidate", no_candidate
    )

    result = await _dispatch(scenario)

    assert result.status == "no_handoff"
    assert scenario.session.added == []
    assert scenario.published == []
    assert scenario.session.records == ["begin", "commit"]


async def test_contended_candidate_does_not_change_the_handoff(
    scenario: Scenario, monkeypatch
) -> None:
    """预读到的工单在加锁前被别的 dispatcher 拿走 → 本轮放弃，不改任何行。"""

    async def taken(*_a, **_k):
        return None

    monkeypatch.setattr(repository, "lock_dispatchable_handoff", taken)

    result = await _dispatch(scenario)

    assert result.status == "contended"
    assert scenario.session.added == []
    assert scenario.handoff.handoff_state == "waiting_human"


async def test_missing_conversation_is_contended(
    scenario: Scenario, monkeypatch
) -> None:
    async def gone(*_a, **_k):
        return None

    monkeypatch.setattr(repository, "lock_conversation", gone)

    result = await _dispatch(scenario)

    assert result.status == "contended"
    assert scenario.session.added == []
    assert "lock_handoff" not in scenario.calls


async def test_presence_unavailable_is_fail_closed(
    scenario: Scenario,
) -> None:
    scenario.online = None

    result = await _dispatch(scenario)

    assert result.status == "presence_unavailable"
    assert scenario.session.added == []
    assert scenario.published == []
    assert "lock_agent" not in scenario.calls
    assert scenario.handoff.handoff_state == "waiting_human"
    assert scenario.handoff.assigned_agent_id is None


async def test_no_online_candidate_is_a_noop(
    scenario: Scenario,
) -> None:
    scenario.online = set()

    result = await _dispatch(scenario)

    assert result.status == "no_candidate"
    assert scenario.session.added == []
    assert "lock_agent" not in scenario.calls
    assert scenario.handoff.assigned_agent_id is None


async def test_full_agent_pool_is_a_noop(
    scenario: Scenario, monkeypatch
) -> None:
    async def no_agent(*_a, **_k):
        return None

    monkeypatch.setattr(repository, "lock_least_loaded_agent", no_agent)

    result = await _dispatch(scenario)

    assert result.status == "no_candidate"
    assert scenario.session.added == []
    assert scenario.handoff.handoff_state == "waiting_human"


async def test_successful_dispatch_binds_handoff_and_agent(
    scenario: Scenario,
) -> None:
    result = await _dispatch(scenario)

    assert result.status == "dispatched"
    assert result.handoff_id == scenario.handoff.handoff_id
    assert result.agent_id == "agent-1"
    assert result.conversation_id == "conv-1"
    assert result.assignment_version == 1

    assert scenario.handoff.handoff_state == "agent_offered"
    assert scenario.handoff.assigned_agent_id == "agent-1"
    assert scenario.handoff.assignment_version == 1
    assert scenario.handoff.attempt_count == 1
    assert scenario.handoff.offered_at == NOW
    assert scenario.handoff.offer_expires_at == NOW + timedelta(
        seconds=CS_OFFER_TIMEOUT_SECONDS
    )
    assert scenario.handoff.updated_at == NOW
    assert scenario.agent.last_assigned_at == NOW


async def test_dispatch_writes_exactly_one_offered_assignment(
    scenario: Scenario,
) -> None:
    await _dispatch(scenario)

    assignments = [o for o in scenario.session.added if isinstance(o, CSAssignment)]
    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment.state == "offered"
    assert assignment.handoff_id == scenario.handoff.handoff_id
    assert assignment.conversation_id == "conv-1"
    assert assignment.agent_id == "agent-1"
    assert assignment.tenant_id == TENANT
    assert assignment.attempt_no == 1
    assert assignment.offer_version == 1
    assert assignment.offered_at == NOW
    assert assignment.offer_expires_at == scenario.handoff.offer_expires_at
    assert assignment.assigned_by == "cs_dispatcher"


async def test_dispatch_keeps_conversation_in_waiting_mode(
    scenario: Scenario,
) -> None:
    await _dispatch(scenario)

    assert scenario.conversation.assigned_agent_id == "agent-1"
    assert scenario.conversation.handling_mode == "waiting_human"
    assert scenario.conversation.updated_at == NOW


async def test_dispatch_persists_one_targeted_event(
    scenario: Scenario,
) -> None:
    await _dispatch(scenario)

    events = [o for o in scenario.session.added if isinstance(o, CSEvent)]
    assert len(events) == 1
    event = events[0]
    assert event.type == "conversation.offered"
    assert event.tenant_id == TENANT
    assert event.handoff_id == scenario.handoff.handoff_id
    assert event.conversation_id == "conv-1"
    assert event.target_agent_id == "agent-1"
    assert event.outbox_status == "pending"
    assert event.payload["agent_id"] == "agent-1"
    assert event.payload["assignment_version"] == 1


async def test_publish_reuses_the_persisted_event_id_and_happens_after_commit(
    scenario: Scenario,
) -> None:
    await _dispatch(scenario)

    event = next(o for o in scenario.session.added if isinstance(o, CSEvent))
    assert len(scenario.published) == 1
    envelope = scenario.published[0]
    assert envelope["event_id"] == event.event_id
    assert envelope["type"] == "conversation.offered"
    assert envelope["target_agent_id"] == "agent-1"
    assert envelope["tenant_id"] == TENANT

    assert scenario.session.records.index("commit") < scenario.calls.index("publish")
    assert "rollback" not in scenario.session.records


async def test_publish_failure_does_not_rollback_the_dispatch(
    scenario: Scenario,
) -> None:
    scenario.publish_error = RuntimeError("ws broadcast exploded")

    result = await _dispatch(scenario)

    assert result.status == "dispatched"
    assert "rollback" not in scenario.session.records
    assert scenario.handoff.handoff_state == "agent_offered"


async def test_state_is_flushed_before_commit(scenario: Scenario) -> None:
    await _dispatch(scenario)

    assert scenario.session.records.index("flush") < scenario.session.records.index(
        "commit"
    )


async def test_second_offer_increments_version_and_attempt(
    scenario: Scenario,
) -> None:
    scenario.handoff.assignment_version = 3
    scenario.handoff.attempt_count = 3

    result = await _dispatch(scenario)

    assert result.assignment_version == 4
    assert scenario.handoff.assignment_version == 4
    assert scenario.handoff.attempt_count == 4
    assignment = next(o for o in scenario.session.added if isinstance(o, CSAssignment))
    assert assignment.attempt_no == 4
    assert assignment.offer_version == 4


async def test_dry_run_observes_selection_without_writing(
    scenario: Scenario,
) -> None:
    result = await service.dispatch_once(
        scenario.session, tenant_id=TENANT, now=NOW, dry_run=True
    )

    assert result.status == "shadow"
    assert result.agent_id == "agent-1"
    assert result.handoff_id == scenario.handoff.handoff_id

    assert scenario.session.added == []
    assert scenario.published == []
    assert "flush" not in scenario.session.records
    assert scenario.handoff.handoff_state == "waiting_human"
    assert scenario.handoff.assignment_version == 0
    assert scenario.handoff.attempt_count == 0
    assert scenario.agent.last_assigned_at is None
    assert scenario.conversation.assigned_agent_id is None


async def test_missing_tenant_is_rejected_without_a_transaction(
    scenario: Scenario,
) -> None:
    result = await service.dispatch_once(scenario.session, tenant_id="   ")

    assert result.status == "no_candidate"
    assert scenario.session.records == []
    assert scenario.session.added == []


# ── 真实 PostgreSQL 部署门槛 ─────────────────────────────────


def test_real_postgres_dispatch_requires_p1_schema() -> None:
    """共享库缺少 028 迁移字段时显式 skip —— 不冒充已通过。"""
    import os

    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()

    try:
        connection = psycopg2.connect(
            host=os.getenv("PGHOST", "127.0.0.1"),
            port=int(os.getenv("PGPORT", "5432")),
            user=os.getenv("PGUSER", "postgres"),
            password=os.getenv("PGPASSWORD", ""),
            dbname=os.getenv("PGDATABASE", "agent_memory"),
            connect_timeout=4,
        )
    except Exception as exc:  # noqa: BLE001 - 环境缺失一律 skip
        pytest.skip(f"PostgreSQL 不可用，跳过 P6 真实派单验收: {exc}")

    with connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'customer_service' AND table_name = 'handoffs' "
            "AND column_name IN ('tenant_id', 'offer_expires_at', "
            "'assignment_version', 'attempt_count')"
        )
        present = cursor.fetchone()[0]
    connection.close()

    if present == 0:
        pytest.skip(
            "共享库尚未应用 028_cs_dispatch 迁移（handoffs 缺 tenant_id/"
            "offer_expires_at/assignment_version/attempt_count），"
            "P6 真实并发派单验收记录为部署门槛"
        )

    assert present == 4


# ── P9 灰度放量门控 ─────────────────────────────────────────


def _bucket_for(conversation_id: str) -> int:
    return int(
        hashlib.sha1(f"{TENANT}:{conversation_id}".encode("utf-8")).hexdigest(),
        16,
    ) % 100


async def test_rollout_percent_zero_skips_enforce_but_not_shadow(
    scenario: Scenario, monkeypatch
) -> None:
    """percent=0：enforce 全部 rollout_skipped；shadow 仍全量计算。"""
    monkeypatch.setattr(
        sys_config, "get_mode", lambda key: "0" if key == "CS_DISPATCH_ROLLOUT_PERCENT" else ""
    )

    result = await _dispatch(scenario)
    assert result.status == "rollout_skipped"
    assert scenario.handoff.handoff_state == "waiting_human"
    assert scenario.session.added == []

    shadow = await service.dispatch_once(
        scenario.session, tenant_id=TENANT, now=NOW, dry_run=True
    )
    assert shadow.status == "shadow"
    assert shadow.agent_id == "agent-1"


async def test_rollout_percent_boundary_is_stable(
    scenario: Scenario, monkeypatch
) -> None:
    """同一会话的桶位稳定：percent=桶位+1 放行，percent=桶位 拦截。"""
    bucket = _bucket_for("conv-1")

    monkeypatch.setattr(
        sys_config,
        "get_mode",
        lambda key: str(bucket) if key == "CS_DISPATCH_ROLLOUT_PERCENT" else "",
    )
    result = await _dispatch(scenario)
    assert result.status == "rollout_skipped"

    scenario.handoff.handoff_state = "waiting_human"
    scenario.handoff.assigned_agent_id = None
    monkeypatch.setattr(
        sys_config,
        "get_mode",
        lambda key: str(bucket + 1) if key == "CS_DISPATCH_ROLLOUT_PERCENT" else "",
    )
    result = await _dispatch(scenario)
    assert result.status == "dispatched"


async def test_rollout_default_100_does_not_gate(scenario: Scenario) -> None:
    """默认 100%：未配置 DB 覆盖时全部放行（env 缺省）。"""
    result = await _dispatch(scenario)
    assert result.status == "dispatched"


# ── 2026-09-21 派单治理：本单永久排除 / 技能匹配 / 自动置忙 ──────


def test_agent_selection_query_permanently_excludes_declined_or_expired_agents() -> None:
    """本单排除：存在该工单的 declined/expired assignment 即排除，无时间窗。"""
    stmt = repository.least_loaded_agent_stmt(
        tenant_id=TENANT, online_agent_ids=["agent-1"], handoff_id="hd-1", now=NOW
    )
    sql = _normalized_sql(stmt)
    params = stmt.compile(dialect=postgresql.dialect()).params

    assert "unassigned_at >" not in sql  # 冷却时间窗已移除
    excluded_states = [
        v
        for v in params.values()
        if isinstance(v, list) and "declined" in v and "expired" in v
    ]
    assert excluded_states, "排除谓词应包含 declined/expired 终结态"
    assert all("released" not in v for v in excluded_states), (
        "released 不排除——主管重派是人工决策，允许指定回同一坐席"
    )


def test_agent_selection_query_without_handoff_has_no_exclusion_predicate() -> None:
    """缺省（无 handoff_id）不排除任何坐席，保持 P6 主管重派行为。"""
    stmt = repository.least_loaded_agent_stmt(
        tenant_id=TENANT, online_agent_ids=["agent-1"]
    )
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert not [
        v
        for v in params.values()
        if isinstance(v, list) and "declined" in v and "expired" in v
    ]


def test_agent_selection_query_filters_by_required_skill() -> None:
    """技能匹配：传 required_skill 时按 cs_agents.skill 精确过滤。"""
    stmt = repository.least_loaded_agent_stmt(
        tenant_id=TENANT, online_agent_ids=["agent-1"], required_skill="billing"
    )
    where = _normalized_sql(stmt).split(" where ", 1)[1]
    assert "cs_agents.skill = " in where

    plain_where = _normalized_sql(
        repository.least_loaded_agent_stmt(
            tenant_id=TENANT, online_agent_ids=["agent-1"]
        )
    ).split(" where ", 1)[1]
    # 主管指定重派不设技能门槛（SELECT 列表天然含全部列，只看 WHERE）
    assert "cs_agents.skill" not in plain_where


async def test_busy_agents_are_excluded_from_dispatch(
    scenario: Scenario, monkeypatch
) -> None:
    """自动置忙：置忙坐席从可派池剔除；全员置忙 → no_candidate。"""

    async def all_busy(*_a, **_k):
        return {"agent-1"}

    monkeypatch.setattr(service.agent_busy, "busy_agent_ids", all_busy)

    result = await _dispatch(scenario)

    assert result.status == "no_candidate"
    assert scenario.session.added == []
    assert scenario.handoff.assigned_agent_id is None


async def test_skill_mismatch_is_a_noop(scenario: Scenario, monkeypatch) -> None:
    """技能不匹配：lock_agent 收到的 required_skill 与坐席技能不一致时选不出人。"""
    # ORM 列默认值只在 flush 时生效，这里显式对齐 DB 存量行的 'general'
    scenario.handoff.required_skill = "general"
    captured: dict = {}

    async def lock_agent(*_a, **_k):
        captured["required_skill"] = _k.get("required_skill")
        return None

    monkeypatch.setattr(repository, "lock_least_loaded_agent", lock_agent)

    result = await _dispatch(scenario)

    assert result.status == "no_candidate"
    # 默认 handoff.required_skill = general（模型 default），必须传给选人语句
    assert captured["required_skill"] == "general"

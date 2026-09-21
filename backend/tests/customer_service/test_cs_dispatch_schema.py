"""客服自动派单数据模型与迁移契约测试。

2026-09-21：Alembic 退役，迁移事实源统一为 backend/sql/migrations/*.sql
（scripts/init_db.py 扫描执行）。本文件同步从「alembic 0023/0024 链」
切换到「原生 028/029」，验收语义不变：幂等重放、存量库兼容升级、
租户 FK 收敛与重复 assignment 守卫。
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    String,
)

from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import ASSIGNMENT_STATES, CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import HANDOFF_STATES, CSHandoff

BACKEND_ROOT = Path(__file__).resolve().parents[2]
NATIVE_MIGRATION = BACKEND_ROOT / "sql" / "migrations" / "028_cs_dispatch.sql"
P2_NATIVE_MIGRATION = BACKEND_ROOT / "sql" / "migrations" / "029_rbac_audit.sql"
INIT_DB_SCRIPT = BACKEND_ROOT.parent / "scripts" / "init_db.py"


def _memory_migrations_before_028() -> tuple[Path, ...]:
    """按 init_db.py 的真实执行顺序取 028 之前的 memory 线迁移。"""
    spec = importlib.util.spec_from_file_location("init_db_module", INIT_DB_SCRIPT)
    assert spec is not None and spec.loader is not None
    init_db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init_db)
    plan, _ = init_db.discover_migrations()
    return tuple(
        BACKEND_ROOT / "sql" / "migrations" / name
        for name, target in plan
        if target == "memory" and name < "028"
    )


def _column(model, name: str):
    assert name in model.__table__.c, f"{model.__name__} 缺少列 {name}"
    return model.__table__.c[name]


def _default(model, name: str):
    default = _column(model, name).default
    assert default is not None, f"{model.__name__}.{name} 缺少 ORM 默认值"
    return default.arg


def _index(model, name: str):
    for index in model.__table__.indexes:
        if index.name == name:
            return index
    raise AssertionError(f"{model.__name__} 缺少索引 {name}")


def _constraint(model, name: str):
    for constraint in model.__table__.constraints:
        if constraint.name == name:
            return constraint
    raise AssertionError(f"{model.__name__} 缺少约束 {name}")


def _native_sql(path: Path) -> str:
    assert path.exists(), f"缺少原生迁移: {path}"
    return path.read_text(encoding="utf-8")


def test_all_dispatch_models_have_tenant_contract():
    models = (CSHandoff, CSAgent, CSAssignment, CSEvent, CSConversation)
    for model in models:
        tenant_id = _column(model, "tenant_id")
        assert isinstance(tenant_id.type, String)
        assert tenant_id.type.length == 64
        assert tenant_id.nullable is False
        assert _default(model, "tenant_id") == "default"
        assert model.__table__.schema == "customer_service"


def test_handoff_model_exposes_dispatch_fields_and_queue_indexes():
    expected_fields = {
        "priority",
        "assigned_agent_id",
        "assignment_version",
        "attempt_count",
        "idempotency_key",
        "total_deadline_at",
        "offered_at",
        "offer_expires_at",
        "closed_reason",
    }
    assert expected_fields <= set(CSHandoff.__table__.c.keys())
    assert isinstance(_column(CSHandoff, "priority").type, Integer)
    assert _column(CSHandoff, "priority").nullable is False
    assert _default(CSHandoff, "priority") == 50
    assert _default(CSHandoff, "assignment_version") == 0
    assert _default(CSHandoff, "attempt_count") == 0
    assert _default(CSHandoff, "handoff_state") == "initiated"

    active = _index(CSHandoff, "uq_cs_handoff_tenant_conversation_active")
    assert active.unique is True
    assert "tenant_id" in {column.name for column in active.expressions}
    assert "conversation_id" in {column.name for column in active.expressions}
    assert "handoff_state" in str(active.dialect_options["postgresql"]["where"])

    queue = _index(CSHandoff, "idx_cs_handoff_dispatch_queue")
    assert [
        str(expression).replace("handoffs.", "")
        for expression in queue.expressions
    ] == [
        "tenant_id", "handoff_state", "priority DESC", "created_at", "id"
    ]


def test_agent_model_exposes_identity_and_capacity_fields():
    expected_fields = {
        "auth_user_id",
        "enabled",
        "accepting",
        "last_assigned_at",
        "version",
        "available",
    }
    assert expected_fields <= set(CSAgent.__table__.c.keys())
    assert _default(CSAgent, "enabled") is True
    assert _default(CSAgent, "accepting") is True
    assert _default(CSAgent, "version") == 0
    assert CSAgent.__table__.c.role.comment == "agent|supervisor|admin"

    tenant_index = _index(CSAgent, "idx_cs_agent_tenant_status")
    assert [column.name for column in tenant_index.expressions] == [
        "tenant_id",
        "enabled",
        "accepting",
    ]
    identity = _index(CSAgent, "uq_cs_agent_tenant_auth_user")
    assert identity.unique is True
    assert [column.name for column in identity.expressions] == [
        "tenant_id",
        "auth_user_id",
    ]
    assert "IS NOT NULL" in str(identity.dialect_options["postgresql"]["where"])


def test_assignment_model_exposes_offer_state_and_active_indexes():
    expected_fields = {
        "handoff_id",
        "state",
        "attempt_no",
        "offer_version",
        "offered_at",
        "offer_expires_at",
        "accepted_at",
        "declined_at",
        "closed_at",
        "assigned_at",
        "unassigned_at",
    }
    assert expected_fields <= set(CSAssignment.__table__.c.keys())
    assert isinstance(_column(CSAssignment, "attempt_no").type, Integer)
    assert _default(CSAssignment, "state") == "offered"
    assert _default(CSAssignment, "attempt_no") == 1
    assert _default(CSAssignment, "offer_version") == 0

    active = _index(CSAssignment, "uq_cs_assignment_tenant_handoff_active")
    assert active.unique is True
    assert "tenant_id" in {column.name for column in active.expressions}
    assert "handoff_id" in {column.name for column in active.expressions}
    assert "state IN" in str(active.dialect_options["postgresql"]["where"])

    agent_state = _index(CSAssignment, "idx_cs_assignment_tenant_agent_state")
    assert [column.name for column in agent_state.expressions] == [
        "tenant_id",
        "agent_id",
        "state",
    ]

    active_handoff = _constraint(
        CSAssignment, "ck_cs_assignment_active_handoff_required"
    )
    assert isinstance(active_handoff, CheckConstraint)
    assert "handoff_id IS NOT NULL" in str(active_handoff.sqltext)


def test_dispatch_relationships_use_tenant_scoped_foreign_keys():
    handoff_agent = _constraint(CSHandoff, "fk_cs_handoff_tenant_agent")
    assert isinstance(handoff_agent, ForeignKeyConstraint)
    assert handoff_agent.column_keys == ["tenant_id", "assigned_agent_id"]
    assert [
        element.target_fullname for element in handoff_agent.elements
    ] == [
        "customer_service.cs_agents.tenant_id",
        "customer_service.cs_agents.agent_id",
    ]
    assert handoff_agent.ondelete == "SET NULL (assigned_agent_id)"

    assignment_handoff = _constraint(
        CSAssignment, "fk_cs_assignment_tenant_handoff"
    )
    assert isinstance(assignment_handoff, ForeignKeyConstraint)
    assert assignment_handoff.column_keys == ["tenant_id", "handoff_id"]
    assignment_agent = _constraint(CSAssignment, "fk_cs_assignment_tenant_agent")
    assert isinstance(assignment_agent, ForeignKeyConstraint)
    assert assignment_agent.column_keys == ["tenant_id", "agent_id"]
    assert assignment_agent.ondelete == "SET NULL (agent_id)"

    assignment_conversation = _constraint(
        CSAssignment, "fk_cs_assignment_tenant_conversation"
    )
    assert isinstance(assignment_conversation, ForeignKeyConstraint)
    assert assignment_conversation.column_keys == [
        "tenant_id",
        "conversation_id",
    ]
    assert assignment_conversation.ondelete == "CASCADE"

    _index(CSAgent, "uq_cs_agent_tenant_agent_id")
    _index(CSHandoff, "uq_cs_handoff_tenant_handoff_id")
    _index(CSConversation, "uq_cs_conversation_tenant_conversation_id")


def test_event_model_exposes_tenant_event_sequence_and_outbox_contract():
    expected_fields = {
        "handoff_id",
        "target_agent_id",
        "actor_user_id",
        "event_seq",
        "outbox_status",
        "published_at",
    }
    assert expected_fields <= set(CSEvent.__table__.c.keys())
    assert isinstance(_column(CSEvent, "event_seq").type, BigInteger)
    assert _column(CSEvent, "event_seq").default is None
    assert _default(CSEvent, "outbox_status") == "pending"
    assert _column(CSEvent, "outbox_status").nullable is False

    sequence = _index(CSEvent, "uq_cs_event_tenant_handoff_seq")
    assert sequence.unique is True
    assert [column.name for column in sequence.expressions] == [
        "tenant_id",
        "handoff_id",
        "event_seq",
    ]
    outbox = _index(CSEvent, "idx_cs_event_outbox_pending")
    assert [column.name for column in outbox.expressions] == [
        "outbox_status",
        "created_at",
        "id",
    ]


def test_dispatch_states_are_explicit_and_preserve_legacy_values():
    assert {
        "ai_active",
        "initiated",
        "handoff_requested",
        "waiting_human",
        "human_active",
        "closed",
    } <= set(HANDOFF_STATES)
    assert {"agent_offered", "waiting_human", "human_active", "closed"} <= set(
        HANDOFF_STATES
    )
    assert {
        "offered",
        "accepted",
        "declined",
        "expired",
        "released",
        "closed",
    } <= set(ASSIGNMENT_STATES)


def test_native_migrations_are_registered_in_init_db():
    """028/029 必须存在于迁移目录且登记进 scripts/init_db.py 的归属表。"""
    init_db_text = INIT_DB_SCRIPT.read_text(encoding="utf-8")
    for path in (NATIVE_MIGRATION, P2_NATIVE_MIGRATION):
        assert path.exists(), f"缺少原生迁移: {path}"
        assert f'"{path.name}"' in init_db_text, (
            f"{path.name} 未登记进 scripts/init_db.py 的 MIGRATION_TARGETS，"
            "一键初始化会漏掉它"
        )


def test_rbac_native_migration_is_idempotent_and_has_no_implicit_tenant_default():
    assert P2_NATIVE_MIGRATION.exists(), f"缺少原生迁移: {P2_NATIVE_MIGRATION}"
    sql = P2_NATIVE_MIGRATION.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", sql).upper()

    assert "ADD COLUMN IF NOT EXISTS VERSION" in normalized
    assert "ADD COLUMN IF NOT EXISTS TENANT_ID" in normalized
    assert "CREATE TABLE IF NOT EXISTS AUTH.RBAC_AUDITS" in normalized
    assert "CREATE INDEX IF NOT EXISTS" in normalized
    assert "TENANT_ID VARCHAR(64) NOT NULL DEFAULT 'DEFAULT'" not in normalized


def _run_dispatch_upgrade(connection, path: Path = NATIVE_MIGRATION):
    """在给定连接上执行原生迁移 SQL（幂等，可直接重放）。"""
    with connection.cursor() as cursor:
        cursor.execute(_native_sql(path))
    connection.commit()


def _run_p2_upgrade(connection):
    _run_dispatch_upgrade(connection, P2_NATIVE_MIGRATION)


def test_p2_migration_chain_is_repeatable_and_real_display_name_constraint_holds(
    dispatch_postgres_db,
):
    psycopg2 = pytest.importorskip("psycopg2")
    connection = dispatch_postgres_db
    _run_dispatch_upgrade(connection)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE SCHEMA IF NOT EXISTS auth;
            CREATE TABLE IF NOT EXISTS auth.users (
                id BIGSERIAL PRIMARY KEY,
                username VARCHAR(20) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'viewer',
                status SMALLINT NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    connection.commit()

    _run_p2_upgrade(connection)
    _run_p2_upgrade(connection)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'auth' AND table_name = 'users'
            AND column_name IN ('version', 'tenant_id')
            ORDER BY column_name
            """
        )
        assert [row[0] for row in cursor.fetchall()] == ["tenant_id", "version"]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'auth' AND table_name = 'rbac_audits'
            """
        )
        assert cursor.fetchone()[0] == 1

        with pytest.raises(psycopg2.errors.NotNullViolation):
            cursor.execute(
                """
                INSERT INTO customer_service.cs_agents
                    (agent_id, tenant_id, auth_user_id, role)
                VALUES ('missing-display-name', 'tenant-a', '1', 'agent')
                """
            )
        connection.rollback()


def test_native_migration_is_idempotent_and_contains_dispatch_contract():
    assert NATIVE_MIGRATION.exists(), f"缺少原生迁移: {NATIVE_MIGRATION}"
    sql = NATIVE_MIGRATION.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", sql).upper()

    assert "ADD COLUMN IF NOT EXISTS" in normalized
    assert "CREATE INDEX IF NOT EXISTS" in normalized
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in normalized
    assert not re.search(
        r"\bDROP\s+(TABLE|INDEX|COLUMN|CONSTRAINT)\s+(?!IF\s+EXISTS\b)",
        normalized,
    )

    required_columns = {
        "handoffs": {
            "tenant_id",
            "priority",
            "assigned_agent_id",
            "assignment_version",
            "attempt_count",
            "idempotency_key",
            "total_deadline_at",
            "offered_at",
            "offer_expires_at",
            "closed_reason",
        },
        "cs_agents": {
            "tenant_id",
            "auth_user_id",
            "enabled",
            "accepting",
            "last_assigned_at",
            "version",
        },
        "assignments": {
            "tenant_id",
            "handoff_id",
            "state",
            "attempt_no",
            "offer_version",
            "offered_at",
            "offer_expires_at",
            "accepted_at",
            "declined_at",
            "closed_at",
        },
        "events": {
            "tenant_id",
            "handoff_id",
            "target_agent_id",
            "actor_user_id",
            "event_seq",
            "outbox_status",
            "published_at",
        },
        "conversations": {"tenant_id"},
    }
    for table, columns in required_columns.items():
        table_sql = " ".join(
            re.findall(
                rf"ALTER TABLE\s+CUSTOMER_SERVICE\.{table.upper()}\s+"
                rf"(ADD COLUMN IF NOT EXISTS.*?);",
                normalized,
                flags=re.DOTALL,
            )
        )
        assert table_sql, f"{table} 缺少对应 ALTER TABLE 语句"
        for column in columns:
            assert re.search(
                rf"ADD COLUMN IF NOT EXISTS\s+{column.upper()}\b", table_sql
            ), f"{table} 缺少幂等列迁移 {column}"

    for index_name in (
        "UQ_CS_HANDOFF_TENANT_CONVERSATION_ACTIVE",
        "IDX_CS_HANDOFF_DISPATCH_QUEUE",
        "IDX_CS_AGENT_TENANT_STATUS",
        "UQ_CS_AGENT_TENANT_AGENT_ID",
        "UQ_CS_AGENT_TENANT_AUTH_USER",
        "UQ_CS_CONVERSATION_TENANT_CONVERSATION_ID",
        "UQ_CS_HANDOFF_TENANT_HANDOFF_ID",
        "UQ_CS_ASSIGNMENT_TENANT_HANDOFF_ACTIVE",
        "IDX_CS_ASSIGNMENT_TENANT_AGENT_STATE",
        "UQ_CS_EVENT_TENANT_HANDOFF_SEQ",
        "IDX_CS_EVENT_OUTBOX_PENDING",
        "IDX_CS_CONV_TENANT_STATUS",
    ):
        assert index_name in normalized

    assert "HANDOFF_STATE <> 'CLOSED'" in normalized
    assert "HANDOFF_ID IS NOT NULL AND STATE IN ('OFFERED', 'ACCEPTED')" in normalized
    assert "SET STATE = 'RELEASED'" in normalized
    assert "FK_CS_ASSIGNMENT_TENANT_HANDOFF" in normalized
    assert "FK_CS_ASSIGNMENT_TENANT_AGENT" in normalized
    assert "FK_CS_ASSIGNMENT_TENANT_CONVERSATION" in normalized
    assert "FK_CS_HANDOFF_TENANT_AGENT" in normalized
    assert "TENANT_ID, HANDOFF_ID, EVENT_SEQ" in normalized
    assert "DROP CONSTRAINT IF EXISTS ASSIGNMENTS_CONVERSATION_ID_FKEY" in normalized
    assert "DROP CONSTRAINT IF EXISTS ASSIGNMENTS_AGENT_ID_FKEY" in normalized

    assert re.search(
        r"FOREIGN KEY \(TENANT_ID, ASSIGNED_AGENT_ID\).*?"
        r"ON DELETE SET NULL \(ASSIGNED_AGENT_ID\)",
        normalized,
        flags=re.DOTALL,
    )
    assert re.search(
        r"FOREIGN KEY \(TENANT_ID, AGENT_ID\).*?"
        r"ON DELETE SET NULL \(AGENT_ID\)",
        normalized,
        flags=re.DOTALL,
    )

    handoff_create = re.search(
        r"CREATE TABLE IF NOT EXISTS CUSTOMER_SERVICE\.HANDOFFS\s*\(.*?\);",
        normalized,
        flags=re.DOTALL,
    )
    assert handoff_create is not None
    assert "ASSIGNED_AGENT_ID VARCHAR(64) REFERENCES" not in handoff_create.group(0)

    assignment_create = re.search(
        r"CREATE TABLE IF NOT EXISTS CUSTOMER_SERVICE\.ASSIGNMENTS\s*\(.*?\);",
        normalized,
        flags=re.DOTALL,
    )
    assert assignment_create is not None
    assert "FK_CS_ASSIGNMENT_HANDOFF" not in assignment_create.group(0)
    assert "AGENT_ID VARCHAR(64) REFERENCES" not in assignment_create.group(0)


def _legacy_schema_sql() -> str:
    return """
    CREATE SCHEMA customer_service;
    CREATE TABLE customer_service.cs_agents (
        id BIGSERIAL PRIMARY KEY,
        agent_id VARCHAR(64) NOT NULL UNIQUE,
        display_name VARCHAR(128) NOT NULL,
        email VARCHAR(128),
        role VARCHAR(20) NOT NULL DEFAULT 'agent',
        available BOOLEAN NOT NULL DEFAULT TRUE,
        max_conversations BIGINT NOT NULL DEFAULT 10,
        custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE TABLE customer_service.conversations (
        id BIGSERIAL PRIMARY KEY,
        conversation_id VARCHAR(64) NOT NULL UNIQUE,
        user_id VARCHAR(64) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        channel VARCHAR(20) NOT NULL DEFAULT 'web',
        assigned_agent VARCHAR(64),
        ai_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        summary TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        closed_at TIMESTAMPTZ
    );
    CREATE TABLE customer_service.handoffs (
        id BIGSERIAL PRIMARY KEY,
        handoff_id VARCHAR(64) NOT NULL UNIQUE,
        conversation_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        handoff_state VARCHAR(20) NOT NULL DEFAULT 'initiated',
        trigger_type VARCHAR(30),
        trigger_reason TEXT,
        ticket_id VARCHAR(64),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        closed_at TIMESTAMPTZ
    );
    CREATE TABLE customer_service.assignments (
        id BIGSERIAL PRIMARY KEY,
        conversation_id VARCHAR(64) NOT NULL,
        agent_id VARCHAR(64),
        assigned_by VARCHAR(64),
        assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        unassigned_at TIMESTAMPTZ
    );
    CREATE TABLE customer_service.events (
        id BIGSERIAL PRIMARY KEY,
        conversation_id VARCHAR(64) NOT NULL,
        event_id VARCHAR(64) NOT NULL UNIQUE,
        type VARCHAR(64) NOT NULL,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    INSERT INTO customer_service.cs_agents (agent_id, display_name)
    VALUES ('legacy-agent', 'Legacy Agent');
    INSERT INTO customer_service.conversations (conversation_id, user_id)
    VALUES ('legacy-conversation', 'legacy-user');
    INSERT INTO customer_service.handoffs
        (handoff_id, conversation_id, user_id, handoff_state)
    VALUES ('legacy-handoff', 'legacy-conversation', 'legacy-user', 'waiting_human');
    INSERT INTO customer_service.assignments
        (conversation_id, agent_id)
    VALUES
        ('legacy-conversation', 'legacy-agent'),
        ('legacy-conversation', 'legacy-agent');
    """


def _historical_006_schema_sql() -> str:
    """准备 006 raw migration 提供给 0003/0004 的最小前置表。"""
    return """
    CREATE SCHEMA customer_service;
    CREATE TABLE customer_service.conversations (
        id BIGSERIAL PRIMARY KEY,
        conversation_id VARCHAR(64) NOT NULL UNIQUE,
        user_id VARCHAR(64) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'open',
        assigned_agent VARCHAR(64),
        ai_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        closed_at TIMESTAMPTZ
    );
    CREATE TABLE customer_service.messages (
        id BIGSERIAL PRIMARY KEY,
        conversation_id VARCHAR(64) NOT NULL,
        role VARCHAR(20) NOT NULL DEFAULT 'user',
        content TEXT NOT NULL DEFAULT '',
        intent_domain VARCHAR(64),
        intent_name VARCHAR(64),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE TABLE public.llm_usage (
        id BIGSERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    INSERT INTO customer_service.conversations
        (conversation_id, user_id)
    VALUES ('historical-conversation', 'historical-user');
    """


@pytest.fixture
def dispatch_postgres_db():
    """创建临时数据库；不可用时明确 skip，不用 SQLite 伪造迁移验证。"""
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2 import sql as pg_sql

    from backend.config.database import MEMORY_DB_CONFIG

    database_name = f"cs_dispatch_test_{uuid.uuid4().hex[:16]}"
    admin_config = {**MEMORY_DB_CONFIG, "dbname": "postgres"}
    try:
        admin = psycopg2.connect(**admin_config, connect_timeout=2)
    except Exception as exc:
        pytest.skip(f"PostgreSQL 不可用，跳过真实迁移测试: {exc}")

    try:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("CREATE DATABASE {}\n").format(
                    pg_sql.Identifier(database_name)
                )
            )
    except Exception as exc:
        admin.close()
        pytest.skip(f"无法创建临时 PostgreSQL 数据库，跳过真实迁移测试: {exc}")
    finally:
        if not admin.closed:
            admin.close()

    test_config = {**MEMORY_DB_CONFIG, "dbname": database_name}
    try:
        connection = psycopg2.connect(**test_config, connect_timeout=2)
    except Exception as exc:
        cleanup = psycopg2.connect(**admin_config, connect_timeout=2)
        cleanup.autocommit = True
        with cleanup.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("DROP DATABASE IF EXISTS {}\n").format(
                    pg_sql.Identifier(database_name)
                )
            )
        cleanup.close()
        pytest.skip(f"无法连接临时 PostgreSQL 数据库，跳过真实迁移测试: {exc}")

    try:
        yield connection
    finally:
        connection.close()
        cleanup = psycopg2.connect(**admin_config, connect_timeout=2)
        cleanup.autocommit = True
        with cleanup.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("DROP DATABASE IF EXISTS {}\n").format(
                    pg_sql.Identifier(database_name)
                )
            )
        cleanup.close()


def test_dispatch_upgrade_handles_legacy_assignments_and_tenant_guards(
    dispatch_postgres_db,
):
    psycopg2 = pytest.importorskip("psycopg2")
    connection = dispatch_postgres_db
    with connection.cursor() as cursor:
        cursor.execute(_legacy_schema_sql())
    connection.commit()

    _run_dispatch_upgrade(connection)
    _run_dispatch_upgrade(connection)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT state, COUNT(*)
            FROM customer_service.assignments
            WHERE handoff_id IS NULL
            GROUP BY state
            """
        )
        assert cursor.fetchall() == [("released", 2)]
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM customer_service.assignments
            WHERE handoff_id IS NULL AND state IN ('offered', 'accepted')
            """
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM customer_service.assignments
            WHERE state IN ('offered', 'accepted') AND handoff_id IS NULL
            """
        )
        assert cursor.fetchone()[0] == 0

        with pytest.raises(psycopg2.errors.CheckViolation):
            cursor.execute(
                """
                INSERT INTO customer_service.assignments
                    (tenant_id, handoff_id, conversation_id, agent_id, state)
                VALUES ('tenant-a', NULL, 'legacy-conversation',
                        'legacy-agent', 'offered')
                """
            )
        connection.rollback()

        cursor.execute(
            """
            INSERT INTO customer_service.cs_agents
                (agent_id, tenant_id, display_name)
            VALUES
                ('tenant-a-agent', 'tenant-a', 'Tenant A'),
                ('tenant-b-agent', 'tenant-b', 'Tenant B')
            """
        )
        cursor.execute(
            """
            INSERT INTO customer_service.conversations
                (conversation_id, user_id, tenant_id)
            VALUES ('tenant-a-conversation', 'tenant-a-user', 'tenant-a')
            """
        )
        cursor.execute(
            """
            INSERT INTO customer_service.handoffs
                (handoff_id, conversation_id, user_id, tenant_id, handoff_state)
            VALUES
                ('tenant-a-handoff', 'tenant-a-conversation', 'tenant-a-user',
                 'tenant-a', 'waiting_human')
            """
        )
    connection.commit()

    with connection.cursor() as cursor:
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cursor.execute(
                """
                INSERT INTO customer_service.assignments
                    (tenant_id, handoff_id, conversation_id, agent_id, state)
                VALUES ('tenant-a', 'tenant-a-handoff', 'tenant-a-conversation',
                        'tenant-b-agent', 'offered')
                """
            )
        connection.rollback()

        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cursor.execute(
                """
                INSERT INTO customer_service.assignments
                    (tenant_id, handoff_id, conversation_id, agent_id, state)
                VALUES ('tenant-b', 'tenant-a-handoff', 'tenant-a-conversation',
                        'tenant-b-agent', 'offered')
                """
            )
        connection.rollback()

        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cursor.execute(
                """
                UPDATE customer_service.handoffs
                SET assigned_agent_id = 'tenant-b-agent'
                WHERE handoff_id = 'tenant-a-handoff'
                """
            )
        connection.rollback()

        cursor.execute(
            """
            INSERT INTO customer_service.assignments
                (tenant_id, handoff_id, conversation_id, agent_id, state)
            VALUES ('tenant-a', 'tenant-a-handoff', 'tenant-a-conversation',
                    'tenant-a-agent', 'offered')
            """
        )
    connection.commit()

    with connection.cursor() as cursor:
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cursor.execute(
                """
                INSERT INTO customer_service.assignments
                    (tenant_id, handoff_id, conversation_id, agent_id, state)
                VALUES ('tenant-a', 'tenant-a-handoff', 'tenant-a-conversation',
                        'tenant-a-agent', 'accepted')
                """
            )
        connection.rollback()


def test_empty_database_upgrade_has_one_named_tenant_fk_per_relationship(
    dispatch_postgres_db,
):
    connection = dispatch_postgres_db
    _run_dispatch_upgrade(connection)
    _run_dispatch_upgrade(connection)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'customer_service.handoffs'::regclass
              AND confrelid = 'customer_service.cs_agents'::regclass
            """
        )
        assert [row[0] for row in cursor.fetchall()] == [
            "fk_cs_handoff_tenant_agent"
        ]

        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'customer_service.assignments'::regclass
              AND confrelid IN (
                  'customer_service.handoffs'::regclass,
                  'customer_service.cs_agents'::regclass
              )
            ORDER BY conname
            """
        )
        assert [row[0] for row in cursor.fetchall()] == [
            "fk_cs_assignment_tenant_agent",
            "fk_cs_assignment_tenant_handoff",
        ]


def test_historical_assignment_fks_are_replaced_by_dispatch_upgrade(
    dispatch_postgres_db,
):
    """006 时代的存量结构（老 FK 命名）上重放原生迁移链，租户 FK 必须收敛。

    与真实初始化路径一致：按 init_db.py 的 memory 线顺序应用 028 之前的
    全部迁移（含 020 的列改名等存量兼容），再重放 028。
    """
    connection = dispatch_postgres_db
    with connection.cursor() as cursor:
        cursor.execute(_historical_006_schema_sql())
    connection.commit()

    for path in _memory_migrations_before_028():
        _run_dispatch_upgrade(connection, path)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO customer_service.cs_agents (agent_id, display_name)
            VALUES ('historical-agent', 'Historical Agent')
            """
        )
        cursor.execute(
            """
            INSERT INTO customer_service.assignments
                (conversation_id, agent_id)
            VALUES ('historical-conversation', 'historical-agent')
            """
        )
    connection.commit()

    _run_dispatch_upgrade(connection)
    _run_dispatch_upgrade(connection)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'customer_service.assignments'::regclass
              AND contype = 'f'
              AND array_length(conkey, 1) = 1
            ORDER BY conname
            """
        )
        assert cursor.fetchall() == []

        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'customer_service.assignments'::regclass
              AND contype = 'f'
            ORDER BY conname
            """
        )
        assert [row[0] for row in cursor.fetchall()] == [
            "fk_cs_assignment_tenant_agent",
            "fk_cs_assignment_tenant_conversation",
            "fk_cs_assignment_tenant_handoff",
        ]

        cursor.execute(
            """
            SELECT conversation_id, agent_id, tenant_id
            FROM customer_service.assignments
            """
        )
        assert cursor.fetchall() == [
            ("historical-conversation", "historical-agent", "default")
        ]

        cursor.execute(
            """
            INSERT INTO customer_service.handoffs
                (handoff_id, conversation_id, user_id, tenant_id,
                 assigned_agent_id)
            VALUES ('historical-handoff', 'historical-conversation',
                    'historical-user', 'default', 'historical-agent')
            """
        )

        cursor.execute(
            """
            DELETE FROM customer_service.cs_agents
            WHERE agent_id = 'historical-agent'
            """
        )
        cursor.execute(
            """
            SELECT agent_id, tenant_id
            FROM customer_service.assignments
            WHERE conversation_id = 'historical-conversation'
            """
        )
        assert cursor.fetchone() == (None, "default")
        cursor.execute(
            """
            SELECT assigned_agent_id
            FROM customer_service.handoffs
            WHERE handoff_id = 'historical-handoff'
            """
        )
        assert cursor.fetchone() == (None,)
    connection.commit()


def test_upgrade_rejects_non_null_active_assignment_duplicates(
    dispatch_postgres_db,
):
    psycopg2 = pytest.importorskip("psycopg2")
    connection = dispatch_postgres_db
    with connection.cursor() as cursor:
        cursor.execute(_legacy_schema_sql())
        cursor.execute(
            """
            ALTER TABLE customer_service.cs_agents
                ADD COLUMN tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant-a';
            ALTER TABLE customer_service.conversations
                ADD COLUMN tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant-a';
            ALTER TABLE customer_service.handoffs
                ADD COLUMN tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant-a';
            ALTER TABLE customer_service.assignments
                ADD COLUMN tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant-a',
                ADD COLUMN handoff_id VARCHAR(64),
                ADD COLUMN state VARCHAR(20) NOT NULL DEFAULT 'offered';
            UPDATE customer_service.assignments
            SET handoff_id = 'duplicate-handoff';
            UPDATE customer_service.handoffs
            SET handoff_id = 'duplicate-handoff';
            """
        )
    connection.commit()

    with connection.cursor() as cursor:
        with pytest.raises(psycopg2.errors.RaiseException, match="duplicate active"):
            _run_dispatch_upgrade(connection)
        connection.rollback()
        cursor.execute(
            "SELECT COUNT(*) FROM customer_service.assignments "
            "WHERE handoff_id = 'duplicate-handoff'"
        )
        assert cursor.fetchone()[0] == 2

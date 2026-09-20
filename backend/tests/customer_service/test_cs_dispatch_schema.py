"""客服自动派单数据模型与迁移契约测试。"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

from sqlalchemy import BigInteger, Integer, String

from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import ASSIGNMENT_STATES, CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import CSHandoff, HANDOFF_STATES


BACKEND_ROOT = Path(__file__).resolve().parents[2]
NATIVE_MIGRATION = BACKEND_ROOT / "sql" / "migrations" / "028_cs_dispatch.sql"
ALEMBIC_MIGRATION = (
    BACKEND_ROOT
    / "sql"
    / "alembic"
    / "memory"
    / "versions"
    / "0023_cs_dispatch.py"
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


def _load_alembic_migration():
    assert ALEMBIC_MIGRATION.exists(), f"缺少 Alembic 迁移: {ALEMBIC_MIGRATION}"
    spec = importlib.util.spec_from_file_location(
        "cs_dispatch_migration", ALEMBIC_MIGRATION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _literal_assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"迁移未声明 {name}")


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


def test_alembic_revision_is_0023_and_leaves_single_head():
    migration = _load_alembic_migration()
    assert migration.revision == "0023"
    assert migration.down_revision == "0022"

    revisions: dict[str, Path] = {}
    down_revisions: set[str] = set()
    for path in ALEMBIC_MIGRATION.parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        revision = _literal_assignment(tree, "revision")
        revisions[revision] = path
        down_revision = _literal_assignment(tree, "down_revision")
        if isinstance(down_revision, str):
            down_revisions.add(down_revision)
        elif isinstance(down_revision, (tuple, list)):
            down_revisions.update(down_revision)

    assert len([path for path in revisions.values() if path.name == "0023_cs_dispatch.py"]) == 1
    assert set(revisions) - down_revisions == {"0023"}


def test_native_migration_is_idempotent_and_contains_dispatch_contract():
    assert NATIVE_MIGRATION.exists(), f"缺少原生迁移: {NATIVE_MIGRATION}"
    sql = NATIVE_MIGRATION.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", sql).upper()

    assert "ADD COLUMN IF NOT EXISTS" in normalized
    assert "CREATE INDEX IF NOT EXISTS" in normalized
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in normalized
    assert not re.search(r"\bDROP\s+(TABLE|INDEX|COLUMN|CONSTRAINT)\b", normalized)

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
        table_sql = normalized[normalized.find(f"{table.upper()}") :]
        for column in columns:
            assert re.search(
                rf"ADD COLUMN IF NOT EXISTS\s+{column.upper()}\b", table_sql
            ), f"{table} 缺少幂等列迁移 {column}"

    for index_name in (
        "UQ_CS_HANDOFF_TENANT_CONVERSATION_ACTIVE",
        "IDX_CS_HANDOFF_DISPATCH_QUEUE",
        "IDX_CS_AGENT_TENANT_STATUS",
        "UQ_CS_AGENT_TENANT_AUTH_USER",
        "UQ_CS_ASSIGNMENT_TENANT_HANDOFF_ACTIVE",
        "IDX_CS_ASSIGNMENT_TENANT_AGENT_STATE",
        "UQ_CS_EVENT_TENANT_HANDOFF_SEQ",
        "IDX_CS_EVENT_OUTBOX_PENDING",
        "IDX_CS_CONV_TENANT_STATUS",
    ):
        assert index_name in normalized

    assert "HANDOFF_STATE <> 'CLOSED'" in normalized
    assert "STATE IN ('OFFERED', 'ACCEPTED')" in normalized
    assert "TENANT_ID, HANDOFF_ID, EVENT_SEQ" in normalized

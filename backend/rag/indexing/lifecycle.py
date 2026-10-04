"""知识生命周期状态机（2026-10-03，演进分析 C1/C2）。

状态语义复用 doc_registry 既有词表，不另造第二套（G2）：
    草稿期   = uploading / parsing / embedding（索引处理中，管线管辖）
    in_review = pending_review（近重复等触发人工审核）
    published = active（含 active_generation / BM25 PUBLISHED 代次指针）
    deprecated = deprecated（2026-10-03 新增：下线/到期，可恢复；区别于 deleted 软删）

fail-closed 铁律：
    - active 只能从 pending_review（=审核通过，必须走 review_service.approve，
      它负责向量/BM25/代次发布）或 deprecated（恢复上线）到达；
    - 本状态机不提供 processing / failed / deleted → active 的操作跳转；
    - 索引管线内部 register 的直 active（规则链免审）属管线写入路径，
      不经本模块，口径见 AGENTS.md。

审计：ai.knowledge_lifecycle_events 惰性建表（CREATE TABLE IF NOT EXISTS，
与 doc_registry_pg._ensure_columns / operation_log 同模式；纯新增表，可 drop 回滚）。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable

import psycopg2
import psycopg2.extras

from backend.rag.indexing.doc_registry import DOC_STATUSES

# 操作者可发起的状态迁移（键=源状态，值=允许目标集合）。
# 有意收窄到「有真实下游驱动」的迁移，防陷阱态：
#   - 不含 →deleted：删除必须走 DELETE /rag/documents/{doc_id}（级联清
#     向量/BM25/源文件）；裸切状态会留孤儿向量被 reconcile 误报；
#   - 不含 pending_review→active：审核通过语义重（代次发布），必须走
#     既有 POST /rag/pending/{doc_id}/approve，避免第二条发布路径；
#   - 不含 failed→pending_review：重处理必须走 reindex 端点（它驱动管线），
#     裸切回审核队列无人消费。
OPERATOR_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"pending_review", "deprecated"}),
    "deprecated": frozenset({"active"}),
}

# fail-closed：active 的唯一合法来源（审核通过后下线可恢复）。
# pending_review→active 不在本表——它必须经 approve（见上）。
ACTIVE_ALLOWED_FROM = frozenset({"deprecated"})


def can_transition(from_status: str, to_status: str) -> tuple[bool, str]:
    """纯函数判定一次操作者迁移是否合法，返回 (ok, 拒绝原因)。"""
    if to_status not in DOC_STATUSES:
        return False, f"未知目标状态: {to_status}，有效值: {DOC_STATUSES}"
    allowed = OPERATOR_TRANSITIONS.get(from_status)
    if allowed is None:
        return False, (
            f"源状态 {from_status} 不归生命周期操作管辖"
            f"（处理中状态由索引管线驱动，deleted 为终态）"
        )
    if to_status == "active" and from_status not in ACTIVE_ALLOWED_FROM:
        return False, (
            f"fail-closed：published(active) 只能经审核通过(approve)或下线恢复到达，"
            f"{from_status} → active 被拒绝"
        )
    if to_status not in allowed:
        return False, f"非法迁移: {from_status} → {to_status}，允许目标: {sorted(allowed)}"
    return True, ""


class LifecycleAuditLog:
    """生命周期流转审计（ai.knowledge_lifecycle_events，惰性建表）。

    conn_factory 可注入（测试只 mock 这一个外部边界——PG 连接）。
    """

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS ai.knowledge_lifecycle_events (
        id           BIGSERIAL PRIMARY KEY,
        doc_id       TEXT NOT NULL,
        from_status  TEXT NOT NULL,
        to_status    TEXT NOT NULL,
        action       TEXT NOT NULL DEFAULT '',
        actor        TEXT NOT NULL DEFAULT '',
        reason       TEXT NOT NULL DEFAULT '',
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )"""

    def __init__(self, conn_factory: Callable[[], Any], table_check: bool = True):
        self._conn_factory = conn_factory
        self._lock = threading.Lock()
        self._ensured = not table_check

    def _ensure_table(self) -> None:
        if self._ensured:
            return
        with self._lock:
            if self._ensured:
                return
            with self._conn_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(self._SCHEMA_SQL)
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_knowledge_lifecycle_events_doc "
                        "ON ai.knowledge_lifecycle_events (doc_id, created_at)"
                    )
                conn.commit()
            self._ensured = True

    def record(
        self, doc_id: str, from_status: str, to_status: str,
        action: str = "", actor: str = "", reason: str = "",
    ) -> None:
        self._ensure_table()
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai.knowledge_lifecycle_events "
                    "(doc_id, from_status, to_status, action, actor, reason) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (doc_id, from_status, to_status, action, actor, reason),
                )
            conn.commit()

    def list_events(self, doc_id: str, limit: int = 100) -> list[dict]:
        self._ensure_table()
        with self._conn_factory() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, doc_id, from_status, to_status, action, actor, reason, created_at "
                    "FROM ai.knowledge_lifecycle_events WHERE doc_id = %s "
                    "ORDER BY created_at DESC, id DESC LIMIT %s",
                    (doc_id, limit),
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]


@contextmanager
def default_audit_conn():
    """生产连接工厂：与 doc_registry 同库（agent_memory，ai schema）。

    psycopg2 连接的 with 语义只管事务不关连接，这里显式开关
    （防连接泄漏打满 max_connections，同 doc_registry_pg 纪律）。
    """
    from backend.config.database import DOC_REGISTRY_PG_CONFIG
    # connect_timeout：vpnkit 回环偶发 connect 挂死兜底（同 faq_conn）
    conn = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG, connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class KnowledgeLifecycleService:
    """生命周期操作入口：合法性裁决 → 条件写状态 → 审计落库。

    registry 仅需 get_by_doc_id / transition_status_by_doc_id / set_expire_at
    三个方法（测试用内存桩实现同一签名）。
    """

    def __init__(self, registry, audit: LifecycleAuditLog):
        self._registry = registry
        self._audit = audit

    def transition(
        self, doc_id: str, to_status: str,
        action: str = "", actor: str = "", reason: str = "",
    ) -> dict:
        doc = self._registry.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        from_status = doc.get("status", "")
        ok, reason_err = can_transition(from_status, to_status)
        if not ok:
            return {"ok": False, "error": reason_err}
        # CAS 条件迁移：源状态在 UPDATE WHERE 内复验，挡掉裁决后的并发变更
        changed = self._registry.transition_status_by_doc_id(
            doc_id, to_status, allowed_from=(from_status,),
        )
        if changed == 0:
            return {"ok": False, "error": f"文档状态已变化（期望 {from_status}），请重试"}
        self._audit.record(
            doc_id, from_status, to_status,
            action=action or f"{from_status}->{to_status}",
            actor=actor, reason=reason,
        )
        return {"ok": True, "doc_id": doc_id, "from": from_status, "to": to_status}

    def set_expire_at(
        self, doc_id: str, expire_at: str | None,
        actor: str = "", reason: str = "",
    ) -> dict:
        doc = self._registry.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        if doc.get("status") != "active":
            return {"ok": False, "error": f"仅 active 文档可设置有效期，当前 {doc.get('status')}"}
        changed = self._registry.set_expire_at(doc_id, expire_at or "")
        if changed == 0:
            return {"ok": False, "error": "设置有效期失败（文档状态已变化），请重试"}
        self._audit.record(
            doc_id, "active", "active",
            action="expire_at_set", actor=actor,
            reason=reason or f"expire_at={expire_at or '(清除)'}",
        )
        return {"ok": True, "doc_id": doc_id, "expire_at": expire_at or ""}

    def list_events(self, doc_id: str, limit: int = 100) -> list[dict]:
        return self._audit.list_events(doc_id, limit=limit)

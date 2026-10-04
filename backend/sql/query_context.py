"""SQL 查询的结构化会话上下文与追问解析。

只保存查询语义和安全元数据，不保存结果行；真正的隔离由
ConversationContextRepository 的 (tenant, user, conversation) 主键保证。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any


_FOLLOW_UP_MARKERS = (
    "上次", "刚才", "上一轮", "继续", "按月", "按周", "按天", "按年",
    "只看", "改成", "换成", "其中", "这些", "这个结果", "为什么",
    "环比", "同比", "趋势", "详细一点",
)


@dataclass(frozen=True)
class SQLQueryContext:
    """可跨轮复用的 SQL 查询摘要。"""

    question: str
    standalone_question: str
    tables: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    sql: str = ""
    status: str = ""
    permission_fingerprint: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """转为 Redis/Memory ConversationContext 可安全序列化的字典。"""
        return {
            "question": self.question[:2000],
            "standalone_question": self.standalone_question[:2000],
            "tables": list(self.tables)[:32],
            "columns": list(self.columns)[:64],
            "sql": self.sql[:8000],
            "status": self.status[:32],
            "permission_fingerprint": self.permission_fingerprint[:64],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "SQLQueryContext | None":
        if not isinstance(value, dict) or not value.get("question"):
            return None
        return cls(
            question=str(value.get("question") or "")[:2000],
            standalone_question=str(
                value.get("standalone_question")
                or value.get("question")
            )[:2000],
            tables=tuple(str(item) for item in (value.get("tables") or [])[:32]),
            columns=tuple(str(item) for item in (value.get("columns") or [])[:64]),
            sql=str(value.get("sql") or "")[:8000],
            status=str(value.get("status") or "")[:32],
            permission_fingerprint=str(
                value.get("permission_fingerprint") or ""
            )[:64],
            updated_at=float(value.get("updated_at") or time.time()),
        )


def permission_fingerprint(policy: Any) -> str:
    """根据当前授权结果生成低敏指纹，不把角色原文写入上下文。"""
    authz = getattr(policy, "authz", None)
    payload = {
        "tenant_id": str(getattr(policy, "tenant_id", "") or ""),
        "data_scope": str(getattr(policy, "data_scope", "") or ""),
        "permissions": sorted(
            str(code) for code in (getattr(authz, "permission_codes", ()) or ())
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def is_sql_context_compatible(
    context: SQLQueryContext | None, current_fingerprint: str,
) -> bool:
    return bool(
        context
        and context.permission_fingerprint
        and current_fingerprint
        and context.permission_fingerprint == current_fingerprint
    )


def resolve_sql_followup(
    question: str, previous: SQLQueryContext | None,
) -> dict[str, Any]:
    """把依赖上一轮的 SQL 追问改写成独立问题。

    规则不确定时保留原问题，不擅自猜测业务条件；后续可接入现有
    FollowUpResolver 的 LLM 兜底，但当前路径保持零额外模型调用。
    """
    raw = (question or "").strip()
    result = {
        "raw_question": raw,
        "standalone_question": raw,
        "follow_up_detected": False,
        "used_context": [],
        "need_clarification": False,
    }
    if not raw or previous is None:
        return result
    if not any(marker in raw for marker in _FOLLOW_UP_MARKERS):
        return result

    result.update(
        standalone_question=(
            f"基于上一轮查询：{previous.standalone_question}；"
            f"当前追问：{raw}"
        ),
        follow_up_detected=True,
        used_context=["previous_sql_query"],
    )
    return result


def build_sql_query_context(
    *,
    question: str,
    standalone_question: str,
    tables: list[str] | tuple[str, ...],
    columns: list[str] | tuple[str, ...],
    sql: str,
    status: str,
    permission_fingerprint_value: str,
) -> SQLQueryContext:
    """从一次查询的安全输出构造下一轮可复用摘要。"""
    return SQLQueryContext(
        question=question,
        standalone_question=standalone_question,
        tables=tuple(tables),
        columns=tuple(columns),
        sql=sql,
        status=status,
        permission_fingerprint=permission_fingerprint_value,
    )


def _safe_sql_summary(sql: str) -> str:
    """去除 SQL 中的字面量后再保存，避免会话摘要携带用户筛选值。"""
    value = str(sql or "")
    value = re.sub(r"'(?:''|[^'])*'", "'?'", value)
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "?", value)
    return value[:8000]


def _tables_from_sql(sql: str) -> list[str]:
    tables = []
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+((?:\"?[a-z_][a-z0-9_]*\"?\.)?"
        r"\"?[a-z_][a-z0-9_]*\"?)",
        re.IGNORECASE,
    )
    for raw in pattern.findall(str(sql or "")):
        table = raw.replace('"', "").lower()
        if table not in tables:
            tables.append(table)
    return tables[:32]


def load_sql_query_context(
    tenant_id: str, user_id: str, session_id: str,
) -> dict[str, Any] | None:
    """从既有 ConversationContext 仓库读取 SQL 摘要。"""
    if not session_id:
        return None
    from backend.orchestration.context.context_repository import (
        get_conversation_context_repository,
    )

    context = get_conversation_context_repository().peek(
        tenant_id or "", user_id or "", session_id)
    return dict(context.sql_query_context) if context and context.sql_query_context else None


def save_sql_query_context(
    tenant_id: str, user_id: str, session_id: str,
    context: SQLQueryContext,
) -> None:
    """写入 SQL 摘要；仓库负责 TTL、CAS 和跨 worker 共享。"""
    if not session_id:
        return
    from backend.orchestration.context.context_repository import (
        ContextMutation,
        MutationType,
        get_conversation_context_repository,
    )

    get_conversation_context_repository().mutate(
        tenant_id or "", user_id or "", session_id,
        ContextMutation(MutationType.SET_SQL_QUERY_CONTEXT, {
            "context": context.to_dict(),
        }),
    )


def clear_sql_query_context(tenant_id: str, user_id: str, session_id: str) -> None:
    if not session_id:
        return
    from backend.orchestration.context.context_repository import (
        ContextMutation,
        MutationType,
        get_conversation_context_repository,
    )

    get_conversation_context_repository().mutate(
        tenant_id or "", user_id or "", session_id,
        ContextMutation(MutationType.CLEAR_SQL_QUERY_CONTEXT),
    )


def persist_sql_query_result(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    raw_question: str,
    query_context: dict[str, Any] | None,
    policy: Any,
    result: Any,
) -> dict[str, Any]:
    """把成功查询写入会话，并返回给 UI 的追问元数据。"""
    previous = SQLQueryContext.from_dict(query_context)
    fingerprint = permission_fingerprint(policy)
    if not is_sql_context_compatible(previous, fingerprint):
        previous = None
    resolution = resolve_sql_followup(raw_question, previous)
    if getattr(result, "status", "") not in ("success", "no_data"):
        return resolution

    context = build_sql_query_context(
        question=raw_question,
        standalone_question=str(
            resolution.get("standalone_question") or raw_question
        ),
        tables=_tables_from_sql(getattr(result, "sql_text", "")),
        columns=list(getattr(result, "columns", None) or []),
        sql=_safe_sql_summary(getattr(result, "sql_text", "") or ""),
        status=str(getattr(result, "status", "") or ""),
        permission_fingerprint_value=fingerprint,
    )
    save_sql_query_context(tenant_id, user_id, session_id, context)
    resolution["memory_saved"] = True
    return resolution

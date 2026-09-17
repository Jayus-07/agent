"""
customer_service/context.py — CSContext 统一类型定义

Main Graph 中 cs_context 字段的 TypedDict 定义 + 构建/合并辅助函数。
Phase 1: 类型层 — 运行时无行为变更，仅类型安全 + 中央化构造。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §7.3
"""
from __future__ import annotations

from typing import TypedDict


class CSContext(TypedDict, total=False):
    """Main Graph cs_context 统一类型

    所有流经 Main Graph 的客服上下文字段。
    total=False — 所有字段可选（cs_context 在不同阶段部分填充）。

    字段来源:
      Router: cs_route, cs_target, authenticated_user_id, session_id, conversation_id
      Nodes: answer_meta, query_meta, pending_action, confirmation_state,
             action_result, handoff_state
      OutputGuard: known_other_user_ids
      Handoff triggers: consecutive_low_confidence, last_confidence, consecutive_failures
    """

    # ── Router 构建 ──
    cs_route: dict
    cs_target: str
    authenticated_user_id: str
    session_id: str
    conversation_id: str

    # ── CS Nodes 写入 ──
    answer_meta: dict
    query_meta: dict
    pending_action: dict | None
    confirmation_state: str
    action_result: dict
    handoff_state: str

    # ── Complaint 幂等防重入 ──
    complaint_ticket_id: str
    complaint_severity: str

    # ── OutputGuard 读取（生产代码不写入）──
    known_other_user_ids: list[str]

    # ── Handoff 自动触发（生产代码不写入）──
    consecutive_low_confidence: int
    last_confidence: float
    consecutive_failures: int


def build_cs_context(
    cs_route: dict,
    cs_target: str,
    authenticated_user_id: str,
    session_id: str,
    conversation_id: str | None = None,
) -> CSContext:
    """构建初始 CSContext（由 Router 调用）

    Router 节点在 CS 域命中后调用，构建完整的初始 cs_context。
    conversation_id 默认等于 session_id（当前实现）。
    """
    return CSContext(
        cs_route=cs_route,
        cs_target=cs_target,
        authenticated_user_id=authenticated_user_id,
        session_id=session_id,
        conversation_id=conversation_id or session_id,
    )


def copy_cs_context(cs_context: CSContext | dict) -> dict:
    """浅拷贝 cs_context（节点 copy-on-write 模式）

    所有 CS 节点在修改 cs_context 前必须调用此函数，
    避免修改原始 state 中的引用。
    """
    return dict(cs_context or {})


def merge_cs_context(
    original: CSContext | dict,
    result: dict,
) -> CSContext:
    """将 CSGraphResult 合并回 Main Graph cs_context

    合并策略:
    - 保留: authenticated_user_id, session_id, cs_target (来自原始)
    - 覆盖: conversation_id, handoff_state, confirmation_state (来自 CS Graph)
    - 保留: 原始中的其他字段（cs_route, answer_meta 等）
    """
    merged = dict(original or {})
    if result.get("conversation_id"):
        merged["conversation_id"] = result["conversation_id"]
    if result.get("handoff_state"):
        merged["handoff_state"] = result["handoff_state"]
    if result.get("confirmation_state"):
        merged["confirmation_state"] = result["confirmation_state"]
    return CSContext(**merged)


__all__ = [
    "CSContext",
    "build_cs_context",
    "copy_cs_context",
    "merge_cs_context",
]

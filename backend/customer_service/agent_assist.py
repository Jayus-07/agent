"""customer_service/agent_assist.py — 坐席辅助（AI 给人工坐席实时推荐回复）

2026-09-22 批次A（客服四能力补齐 MVP 方案 §三）：

触发（两个入口，单一收口）：
  - conversation.claimed            坐席认领成功 → 生成首轮推荐
  - message.created (sender=user)   人工会话中用户追问 → 刷新推荐
入口挂在 ``AgentHub._persist_and_broadcast``（所有 WS 事件的必经点，
含 outbox relay 的 persist=False 重放路径），命中后 ``asyncio.create_task``
异步生成，**绝不阻塞消息主链路**。

生成（run_in_executor 中执行同步 RAG，事件循环零阻塞）：
  1. 知识问答   CSKnowledgeService.answer（含置信度门控，REFUSE 不推荐）
  2. 知识库原文 检索 top1 片段（与答案不同时才推荐，标注来源）
  3. 订单速览   用户最新一笔订单摘要（query 域场景，Agent 可直接口播）

并发保护（高并发上线约束）：
  - 会话级 in-flight 去重：同一会话生成中再来新消息直接跳过（防雪崩堆积）
  - 全局 asyncio.Semaphore 背压：最多 CS_AGENT_ASSIST_MAX_CONCURRENCY 并发
  - 总超时 CS_AGENT_ASSIST_TIMEOUT_SECONDS：超时静默放弃
  - 全链路 try/except 失败静默：辅助功能永不影响业务

推送：``assist.suggestion``（persist=False 瞬态事件，不进 events 表，
断线补发不含推荐——推荐是易腐内容，重放旧推荐反而误导坐席）。

安全边界：推荐只读检索，不自动发送；EvidenceGate 精神落地 ——
低置信（CAUTIOUS）推荐带"仅供参考"标注。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from backend.shared.logger import logger

# 会话级 in-flight 集合（跨线程只被主 loop 触碰，仍加锁防御测试并发）
_inflight: set[str] = set()
_inflight_lock = threading.Lock()
_semaphore: asyncio.Semaphore | None = None


def _assist_enabled() -> bool:
    from backend.config.customer_service import CS_AGENT_ASSIST_ENABLED

    return CS_AGENT_ASSIST_ENABLED


def _get_semaphore() -> asyncio.Semaphore:
    """懒创建信号量（必须绑定主 loop；进程内单例）。"""
    global _semaphore
    if _semaphore is None:
        from backend.config.customer_service import (
            CS_AGENT_ASSIST_MAX_CONCURRENCY,
        )

        _semaphore = asyncio.Semaphore(CS_AGENT_ASSIST_MAX_CONCURRENCY)
    return _semaphore


# ────────────────────────────────────────────────────────────
# 触发入口（realtime.py 的 _persist_and_broadcast 调用，主 loop 上执行）
# ────────────────────────────────────────────────────────────

def maybe_schedule_assist(event_type: str, payload: dict[str, Any]) -> None:
    """命中触发条件则调度一次推荐生成；其余情况静默返回。

    必须运行在主 uvicorn loop（create_task 依赖 running loop）。
    异常全部吞掉——触发器挂了只损失推荐，不影响事件广播本身。
    """
    try:
        if not _assist_enabled():
            return

        if event_type == "message.created":
            msg = payload.get("message") or {}
            if msg.get("sender_type") != "user":
                return  # 只对用户消息刷新推荐（坐席自己发的消息无需响应）
            conversation_id = str(payload.get("conversation_id") or "")
        elif event_type == "conversation.claimed":
            conversation_id = str(payload.get("conversation_id") or "")
        else:
            return

        if not conversation_id:
            return

        # 会话级去重：同会话已有生成任务在跑则跳过（下一条消息会再触发）
        with _inflight_lock:
            if conversation_id in _inflight:
                return
            _inflight.add(conversation_id)

        asyncio.get_running_loop().create_task(
            _assist_task(conversation_id),
            name=f"cs-assist-{conversation_id[:12]}",
        )
    except Exception:
        logger.debug("[AgentAssist] schedule failed", exc_info=True)


async def _assist_task(conversation_id: str) -> None:
    """单次推荐生成任务：背压 → 校验 → 生成 → 推送。失败静默。"""
    try:
        async with _get_semaphore():
            context = await _load_conversation_context(conversation_id)
            if context is None:
                return  # 非 human_active 会话（AI 阶段不需要推荐）

            from backend.config.customer_service import (
                CS_AGENT_ASSIST_TIMEOUT_SECONDS,
            )

            suggestions = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, _generate_suggestions_sync, context,
                ),
                timeout=CS_AGENT_ASSIST_TIMEOUT_SECONDS,
            )
            if not suggestions:
                return

            from backend.customer_service.realtime import get_agent_hub

            get_agent_hub().publish(
                "assist.suggestion",
                persist=False,
                conversation_id=conversation_id,
                trigger=context["trigger"],
                suggestions=suggestions,
            )
    except asyncio.TimeoutError:
        logger.debug("[AgentAssist] timeout, dropped: conv=%s", conversation_id)
    except Exception:
        logger.debug(
            "[AgentAssist] generate failed: conv=%s", conversation_id,
            exc_info=True,
        )
    finally:
        with _inflight_lock:
            _inflight.discard(conversation_id)


# ────────────────────────────────────────────────────────────
# 会话上下文加载（async DB，主 loop 上执行）
# ────────────────────────────────────────────────────────────

async def _load_conversation_context(conversation_id: str) -> dict[str, Any] | None:
    """加载会话快照；非 human_active（或查无会话）返回 None。"""
    from sqlalchemy import select

    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.handoff import CSHandoff
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        conv = (
            await db.execute(
                select(CSConversation).where(
                    CSConversation.conversation_id == conversation_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if conv is None or conv.handling_mode != "human":
            return None

        handoff = (
            await db.execute(
                select(CSHandoff).where(
                    CSHandoff.conversation_id == conversation_id,
                    CSHandoff.handoff_state != "closed",
                ).limit(1)
            )
        ).scalar_one_or_none()
        if handoff is None or handoff.handoff_state != "human_active":
            return None

        limit = _history_limit()
        rows = (
            await db.execute(
                select(CSMessage)
                .where(CSMessage.conversation_id == conversation_id)
                .order_by(CSMessage.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        # 时间正序（旧 → 新）供拼装上下文
        messages = [
            {
                "sender_type": m.sender_type,
                "content": m.content or "",
            }
            for m in reversed(rows)
        ]
        latest_intent = rows[-1].intent_name if rows else None

    return {
        "conversation_id": conversation_id,
        "user_id": conv.user_id,
        "tenant_id": conv.tenant_id,
        "trigger": "claimed" if handoff.assigned_agent_id else "message",
        "messages": messages,
        "latest_intent": latest_intent,
    }


def _history_limit() -> int:
    from backend.config.customer_service import CS_AGENT_ASSIST_HISTORY_LIMIT

    return CS_AGENT_ASSIST_HISTORY_LIMIT


def _latest_user_question(messages: list[dict[str, str]]) -> str:
    """取最近一条用户消息（倒序找，兜底取最后一条）。"""
    for msg in reversed(messages):
        if msg.get("sender_type") == "user" and msg.get("content", "").strip():
            return msg["content"].strip()
    return messages[-1]["content"].strip() if messages else ""


# ────────────────────────────────────────────────────────────
# 推荐生成（同步函数，executor 线程中执行）
# ────────────────────────────────────────────────────────────

def _generate_suggestions_sync(context: dict[str, Any]) -> list[dict[str, Any]]:
    """组装 top-k 推荐。任何一路失败只影响那一路，不影响整体。"""
    from backend.config.customer_service import CS_AGENT_ASSIST_TOP_K

    question = _latest_user_question(context["messages"])
    if not question:
        return []

    suggestions: list[dict[str, Any]] = []

    rec = _knowledge_recommendation(question, context)
    if rec:
        suggestions.append(rec)

    rec = _snippet_recommendation(question, context, suggestions)
    if rec:
        suggestions.append(rec)

    rec = _order_recommendation(context)
    if rec:
        suggestions.append(rec)

    return suggestions[:CS_AGENT_ASSIST_TOP_K]


def _knowledge_recommendation(
    question: str, context: dict[str, Any],
) -> dict[str, Any] | None:
    """知识问答推荐：置信度门控后的答案（对齐客诉答复同源）。"""
    try:
        from backend.customer_service.knowledge.answer_decision import Decision
        from backend.customer_service.knowledge.service import (
            get_knowledge_service,
        )

        result = get_knowledge_service().answer(
            question,
            kb_ids=list(_knowledge_bases()),
            session_id=context["conversation_id"],
        )
        if result.decision == Decision.REFUSE or not result.answer.strip():
            return None
        text = result.answer.strip()
        if result.decision == Decision.CAUTIOUS:
            # EvidenceGate 精神：低置信推荐必须带标注，防坐席照搬
            text = f"{text}（置信度一般，建议核实后回复）"
        return {
            "kind": "knowledge_answer",
            "source": "知识库",
            "score": round(float(result.confidence), 3),
            "text": text[:600],
        }
    except Exception:
        logger.debug("[AgentAssist] knowledge path failed", exc_info=True)
        return None


def _knowledge_bases() -> tuple[str, ...]:
    from backend.config.customer_service import CS_KNOWLEDGE_BASES

    bases = CS_KNOWLEDGE_BASES.get("cs_faq")
    if isinstance(bases, (list, tuple)) and bases:
        return tuple(str(b) for b in bases)
    return ("cs_faq",)


def _snippet_recommendation(
    question: str,
    context: dict[str, Any],
    existing: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """知识库原文 top1 片段：答案不可用时给坐席参考原材料。"""
    if existing:
        return None  # 已有答案推荐时不再堆原文，保持 top-k 信息密度
    try:
        from backend.rag.pipeline import get_rag_pipeline

        pipeline = get_rag_pipeline()
        retriever = getattr(pipeline, "chunk_retriever", None)
        if not retriever:
            return None
        pipeline._prepare_context(
            "default", question, subject_type="customer",
        )
        try:
            docs = retriever.retrieve(question)
        finally:
            pipeline._cleanup()
        if not docs:
            return None
        content = (docs[0].page_content or "").strip()
        if len(content) < 20:
            return None
        meta = getattr(docs[0], "metadata", {}) or {}
        return {
            "kind": "knowledge_snippet",
            "source": f"知识库原文·{meta.get('source', meta.get('doc_id', '文档'))}",
            "score": None,
            "text": content[:400],
        }
    except Exception:
        logger.debug("[AgentAssist] snippet path failed", exc_info=True)
        return None


_QUERY_HINT_KEYWORDS = ("订单", "物流", "退货", "退款", "发货", "签收", "快递")


def _order_recommendation(context: dict[str, Any]) -> dict[str, Any] | None:
    """订单速览：query 域场景给坐席一句可直接口播的订单状态。"""
    messages = context["messages"]
    hit_query = (context.get("latest_intent") or "") in ("order", "logistics", "refund")
    if not hit_query:
        recent = " ".join(m.get("content", "") for m in messages[-3:])
        hit_query = any(k in recent for k in _QUERY_HINT_KEYWORDS)
    if not hit_query:
        return None
    try:
        from backend.customer_service.service.demo_mode import resolve_user_id
        from backend.customer_service.service.order_service import (
            get_order_service,
        )

        result = get_order_service().query_orders(
            user_id=resolve_user_id(context["user_id"]),
        )
        orders = getattr(result, "orders", None) or []
        if not orders:
            return None
        top = orders[0]
        oid = top.get("order_no", "")
        status = top.get("status", "")
        amount = top.get("total_amount", "")
        text = f"您好，查询到您最近的订单 {oid} 当前状态为「{status}」"
        if amount:
            text += f"，金额 ¥{amount}"
        text += "。请问需要我为您进一步处理什么？"
        return {
            "kind": "order_summary",
            "source": "订单系统",
            "score": None,
            "text": text,
        }
    except Exception:
        logger.debug("[AgentAssist] order path failed", exc_info=True)
        return None

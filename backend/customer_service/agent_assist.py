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
# 触发入口：主 loop（_persist_and_broadcast 路径）+
#           Redis 订阅线程（outbox relay 路径）双通道
# ────────────────────────────────────────────────────────────

def _acquire_trigger(
    event_type: str, payload: dict[str, Any],
) -> tuple[str, str] | None:
    """判定是否命中触发条件；命中则登记 in-flight 并返回 (会话 ID, 触发类型)。

    供主 loop（maybe_schedule_assist）与 Redis 订阅线程
    （maybe_schedule_assist_from_envelope）两条路径共用。

    触发类型（前端据此标记「新」跟进流）：
      - claimed    坐席认领成功 → 首轮推荐
      - message    用户追问 → 刷新推荐
      - followup   坐席发送后 → 跟进建议（P4：sender=human_agent）
    """
    if not _assist_enabled():
        return None

    if event_type == "message.created":
        msg = payload.get("message") or {}
        sender_type = msg.get("sender_type")
        if sender_type == "user":
            trigger = "message"
        elif sender_type == "human_agent":
            trigger = "followup"  # 坐席刚回复 → 给下一步跟进建议
        else:
            return None  # bot/系统消息无需响应
        conversation_id = str(payload.get("conversation_id") or "")
    elif event_type == "conversation.claimed":
        trigger = "claimed"
        conversation_id = str(payload.get("conversation_id") or "")
    else:
        return None

    if not conversation_id:
        return None

    # 会话级去重：同会话已有生成任务在跑则跳过（下一条消息会再触发）
    with _inflight_lock:
        if conversation_id in _inflight:
            return None
        _inflight.add(conversation_id)
    return conversation_id, trigger


def maybe_schedule_assist(event_type: str, payload: dict[str, Any]) -> None:
    """主 loop 入口（hub._persist_and_broadcast 调用）：
    本进程直接发布的事件（AI 阶段消息、typing 等）。

    必须运行在主 uvicorn loop（create_task 依赖 running loop）。
    异常全部吞掉——触发器挂了只损失推荐，不影响事件广播本身。
    """
    try:
        acquired = _acquire_trigger(event_type, payload)
        if not acquired:
            return
        conversation_id, trigger = acquired
        asyncio.get_running_loop().create_task(
            _assist_task(conversation_id, trigger),
            name=f"cs-assist-{conversation_id[:12]}",
        )
    except Exception:
        logger.debug("[AgentAssist] schedule failed", exc_info=True)


def maybe_schedule_assist_from_envelope(envelope: dict[str, Any], loop) -> None:
    """Redis 订阅线程入口（批次A 修正）：relay 转发路径的必经点。

    outbox relay（cs-dispatcher）经 ``publish_envelope`` 直投 Redis，
    不经过 ``_persist_and_broadcast`` —— 认领/坐席消息等 outbox 事件的
    坐席辅助触发只能挂在这里。跨线程经 ``run_coroutine_threadsafe``
    跳回主 loop；in-flight 去重防止与本地发布路径重复调度。
    """
    try:
        if not isinstance(envelope, dict) or loop is None:
            return
        event_type = str(envelope.get("type") or "")
        payload = {
            k: v for k, v in envelope.items()
            if k not in ("type", "event_id", "seq", "ts")
        }
        acquired = _acquire_trigger(event_type, payload)
        if not acquired:
            return
        conversation_id, trigger = acquired
        asyncio.run_coroutine_threadsafe(
            _assist_task(conversation_id, trigger), loop,
        )
    except Exception:
        logger.debug("[AgentAssist] schedule from envelope failed", exc_info=True)


async def _assist_task(conversation_id: str, trigger: str) -> None:
    """单次推荐生成任务：背压 → 校验 → 生成 → 推送。失败静默。"""
    try:
        async with _get_semaphore():
            context = await _load_conversation_context(conversation_id)
            if context is None:
                return  # 非 human_active 会话（AI 阶段不需要推荐）
            context["trigger"] = trigger

            from backend.config.customer_service import (
                CS_AGENT_ASSIST_TIMEOUT_SECONDS,
            )

            suggestions = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, _generate_suggestions_sync, context,
                ),
                timeout=CS_AGENT_ASSIST_TIMEOUT_SECONDS,
            )
            intent = _panel_intent(context)
            emotion = _detect_emotion(context["messages"])
            if emotion and emotion.get("level") == "alert":
                suggestions = suggestions + [{
                    "kind": "emotion_alert",
                    "source": "系统 · 情绪预警",
                    "score": None,
                    "text": "检测到强烈不满情绪，建议通知主管关注该会话。",
                }]
            if not suggestions and intent is None:
                return

            from backend.customer_service.realtime import get_agent_hub

            get_agent_hub().publish(
                "assist.suggestion",
                persist=False,
                conversation_id=conversation_id,
                trigger=trigger,
                intent=intent,
                emotion=emotion,
                sop=_sop_for(intent.get("name") if intent else None),
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


def _assist_top_k() -> int:
    from backend.config.customer_service import CS_AGENT_ASSIST_TOP_K

    return CS_AGENT_ASSIST_TOP_K


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
        # 意图徽章取「最近一条带意图的消息」：AI 阶段识别结果对人工坐席
        # 依然有效；人工直发消息不带意图，不应覆盖已识别的会话意图。
        latest_intent = None
        latest_intent_confidence = None
        for m in reversed(rows):
            if m.intent_name:
                latest_intent = m.intent_name
                latest_intent_confidence = m.confidence
                break

    return {
        "conversation_id": conversation_id,
        "user_id": conv.user_id,
        "tenant_id": conv.tenant_id,
        "trigger": "claimed" if handoff.assigned_agent_id else "message",
        "messages": messages,
        "latest_intent": latest_intent,
        "latest_intent_confidence": latest_intent_confidence,
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


# ────────────────────────────────────────────────────────────
# 面板字段（P4）：意图徽章 / 情绪检测 / SOP 下一步
# ────────────────────────────────────────────────────────────

def _panel_intent(context: dict[str, Any]) -> dict[str, Any] | None:
    """意图徽章：最新一轮识别结果的名称（中文）+ 置信度。"""
    intent_name = context.get("latest_intent")
    if not intent_name:
        return None
    label = _intent_label(intent_name)
    payload: dict[str, Any] = {"name": intent_name, "label": label}
    confidence = context.get("latest_intent_confidence")
    if confidence is not None:
        try:
            payload["confidence"] = round(float(confidence), 3)
        except (TypeError, ValueError):
            pass
    return payload


def _intent_label(intent_name: str) -> str:
    """细粒度意图名 → 中文展示名（FINE_INTENTS 之外原样返回）。"""
    try:
        from backend.customer_service.router.intents import FINE_INTENTS

        return FINE_INTENTS.get(intent_name, intent_name)
    except Exception:
        return intent_name


# 极简规则情绪检测（P4 MVP）：透明可测、零 LLM 依赖；只做「是否不满」
# 的粗判，供坐席预警，不作为用户画像落库。
_NEGATIVE_STRONG_KEYWORDS = (
    "气死", "骗子", "垃圾", "恶心", "受够了", "忍无可忍", "投诉到底",
)
_NEGATIVE_MILD_KEYWORDS = (
    "不满", "投诉", "太慢", "怎么回事", "敷衍", "拖延", "一直没", "太久",
)


def _detect_emotion(messages: list[dict[str, str]]) -> dict[str, Any] | None:
    """情绪检测：扫描最新用户消息的负面关键词；无用户消息返回 None。"""
    latest_user = ""
    for msg in reversed(messages):
        if msg.get("sender_type") == "user" and msg.get("content", "").strip():
            latest_user = msg["content"]
            break
    if not latest_user:
        return None
    if any(k in latest_user for k in _NEGATIVE_STRONG_KEYWORDS):
        return {"label": "愤怒", "level": "alert"}
    if any(k in latest_user for k in _NEGATIVE_MILD_KEYWORDS):
        return {"label": "不满", "level": "warn"}
    return {"label": "平和", "level": "ok"}


# SOP 下一步（P4 MVP）：意图 → 静态运营模板映射；未覆盖意图走通用模板。
# 步骤状态：done（已完成）/ cur（当前建议）/ todo。后续可迁 sys_config 配置化。
_SOP_TEMPLATES: dict[str, list[list[str]]] = {
    "c_complaint": [["安抚情绪", "done"], ["登记工单", "cur"], ["同步处理进度", "todo"]],
    "c_feedback": [["感谢反馈", "done"], ["记录要点", "cur"], ["同步产品侧", "todo"]],
    "as_refund": [["确认订单与原因", "done"], ["核对退款政策", "cur"], ["提交退款申请", "todo"]],
    "as_refund_reason_update": [["定位退款申请", "done"], ["修改原因", "cur"], ["同步审核进度", "todo"]],
    "as_return": [["确认退货条件", "done"], ["提供寄回地址", "cur"], ["登记退货工单", "todo"]],
    "as_exchange": [["确认换货需求", "done"], ["核对库存", "cur"], ["发起换货", "todo"]],
    "as_repair": [["确认故障描述", "done"], ["登记维修工单", "cur"], ["同步进度", "todo"]],
    "as_quality_issue": [["致歉并记录", "done"], ["收集凭证", "cur"], ["升级质检", "todo"]],
    "t_order_status": [["定位订单", "done"], ["同步物流状态", "cur"], ["确认是否催单", "todo"]],
    "t_logistics": [["定位运单", "done"], ["查询轨迹", "cur"], ["给出时效预期", "todo"]],
    "t_order_cancel": [["确认订单信息", "done"], ["说明取消政策", "cur"], ["发起取消提案", "todo"]],
    "t_refund_status": [["定位退款单", "done"], ["同步处理进度", "cur"], ["告知到账时效", "todo"]],
    "t_ticket_status": [["定位工单", "done"], ["同步处理进度", "cur"], ["确认是否升级", "todo"]],
    "t_balance_query": [["核实账户余额", "done"], ["同步流水", "cur"], ["确认是否充值", "todo"]],
    "t_delivery_estimate": [["确认收货地址", "done"], ["给出时效区间", "cur"], ["标记跟踪", "todo"]],
    "a_password": [["核实身份", "done"], ["引导自助重置", "cur"], ["确认恢复登录", "todo"]],
    "a_address": [["确认新地址", "done"], ["评估改址窗口", "cur"], ["提交修改", "todo"]],
    "a_login_issue": [["收集报错信息", "done"], ["引导排查", "cur"], ["确认恢复", "todo"]],
    "h_handoff": [["确认问题范围", "done"], ["人工接入", "cur"], ["跟进解决", "todo"]],
    "h_supervisor": [["安抚情绪", "done"], ["升级主管", "cur"], ["同步进展", "todo"]],
}
_DEFAULT_SOP: list[list[str]] = [
    ["理解问题", "done"], ["给出处理方案", "cur"], ["确认是否解决", "todo"],
]


def _sop_for(intent_name: str | None) -> list[dict[str, str]]:
    """SOP 下一步：意图命中模板，否则通用三步。"""
    steps = _SOP_TEMPLATES.get(intent_name or "", _DEFAULT_SOP)
    return [{"label": label, "state": state} for label, state in steps]

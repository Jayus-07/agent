"""context_resolver.py — 客服域多轮业务实体承接（缺陷9，2026-09-23）

「那它到哪了」此前被当成独立 query 路由：fine router 落 KNOWLEDGE 默认
意图 → RAG/EvidenceGate 拒答。正确行为是在 Router 判域**之前**用上一轮
结构化业务上下文完成实体继承。

设计边界：
- 只维护 ``last_order_id`` 一个权威业务实体字段，不建 last_order/recent_
  order/context_order 等重复字段，不引入长期 Memory 系统。
- 存储绑定 (tenant_id, user_id, session_id) 三元组，跨用户/跨会话不可见。
- 会话级 TTL（Redis TwoTier + L1），新会话天然从空上下文开始。
- Resolution 优先级：pending_action 补槽（既有链路，优先级更高且不经本
  模块）> 当前轮显式实体（本模块直接跳过）> structured recent context
  （唯一 referent）> unresolved → 原始 query。零 LLM：纯正则规则，
  模糊场景宁可放弃继承，不猜。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.shared.logger import logger

_CONTEXT_CACHE_NAME = "cs_business_context"
# 会话级生命周期：新会话从空上下文开始；同一会话 2 小时无后续则自然失效
_CONTEXT_TTL_SECONDS = 7200

# ── 回指判定（规则式，高置信才继承）──────────────────────────

# 指代词：它/这个订单/那单/刚才那个/上一单…
_REFERENCE_PRONOUN = re.compile(
    r"(它|他|这[个单笔张份]|那[个单笔张份]|刚才|上次|上一?[个单笔张份]|该订单|此单|之前那?个)"
)

# 业务谓词：回指必须落在订单业务语境里（物流/状态/进度/售后处置）
_REFERENCE_PREDICATE = re.compile(
    r"(到哪|物流|发货|寄出|运输|配送|到货|签收|收货|什么时候到|多久到|多久发货"
    r"|状态|进度|怎么样|咋样|处理得|查一下|查询|查查|退了吗|到账|发货了吗)"
)

# 负向信号：命中即放弃继承（知识/投诉/账号/非客服语境，不得被旧订单吞掉）
_REFERENCE_NEGATIVE = re.compile(
    r"(保修|政策|条款|发票|投诉|举报|维权|周报|优惠券|优惠|活动|密码|登录|"
    r"地址修改|写一篇|帮我写|新闻|天气)"
)

# 订单号显式形态（与 understanding.entities 同族语义）：当前轮有显式订单
# 号时回指解析必须让位（显式实体 > 继承实体）
_EXPLICIT_ORDER = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*[A-Za-z])"
    r"([A-Za-z0-9]{2,10}(?:-[A-Za-z0-9]{2,12})+)(?![A-Za-z0-9])"
)

_MAX_REFERENCE_LEN = 40  # 回指句是短句，超长视为普通查询

# 物流类谓词 → resolved_query 措辞区分（都足以路由到 order 查询）
_LOGISTICS_HINT = re.compile(
    r"(到哪|物流|发货|寄出|运输|配送|到货|签收|收货|什么时候到|多久到|多久发货|发货了吗|到账)"
)

_ORDINAL_ORDER = re.compile(r"第([一二三四五六七八九十百0-9]+)(?:个)?订单")
_CN_ORDINAL = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


@dataclass
class ContextResolution:
    """一次成功的回指解析结果。"""

    order_id: str
    resolved_query: str
    resolution_type: str = "inherited_entity"
    source_intent: str = ""
    original_query: str = ""
    inherited_entities: dict = field(default_factory=dict)

    def trace_fields(self) -> dict:
        """轻量 trace 字段（不含完整历史/隐私，订单号按现有口径记录）。"""
        return {
            "original_query": self.original_query,
            "resolved_query": self.resolved_query,
            "resolution_type": self.resolution_type,
            "entity_type": "order_id",
            "entity_source": "recent_business_context",
        }


def context_key(tenant_id: str, user_id: str, session_id: str) -> str:
    """上下文隔离键：tenant/user/session 三元组，跨会话跨用户不可见。"""
    return f"{tenant_id or 'default'}|{user_id or 'anonymous'}|{session_id or 'default'}"


def _cache():
    from backend.infra.cache import get_cache

    return get_cache(_CONTEXT_CACHE_NAME, ttl=_CONTEXT_TTL_SECONDS)


def get_recent_business_context(
    tenant_id: str, user_id: str, session_id: str
) -> dict | None:
    """读取当前会话的结构化业务上下文；无/过期返回 None。"""
    try:
        return _cache().get_json(context_key(tenant_id, user_id, session_id))
    except Exception as exc:
        logger.warning("[ContextResolver] read context failed: %s", exc)
        return None


def record_recent_order(
    tenant_id: str,
    user_id: str,
    session_id: str,
    order_id: str,
    source_intent: str = "",
) -> None:
    """记录本轮真实解析出的唯一订单实体。

    调用准则：只有「精确查单返回唯一订单 / 业务流程选中单个订单」才写；
    列表类查询（多订单、无唯一 referent）禁止写入，避免下轮回指猜订单。
    """
    if not order_id or not user_id or not session_id:
        return
    try:
        _cache().set_json(
            context_key(tenant_id, user_id, session_id),
            {
                "last_order_id": str(order_id),
                "recent_order_ids": [str(order_id)],
                "source_intent": source_intent,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ttl=_CONTEXT_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("[ContextResolver] record context failed: %s", exc)


def record_recent_orders(
    tenant_id: str,
    user_id: str,
    session_id: str,
    order_ids: list[str],
    source_intent: str = "",
) -> None:
    """记录本轮列表结果，供用户明确选择“第几个订单”时承接。

    列表本身不产生 ``last_order_id``，因此不会把多订单误当成唯一
    referent；只有下一轮带明确序号且序号在列表范围内时才会解析。
    """
    normalized = [str(item).strip() for item in order_ids if str(item).strip()]
    if not normalized or not user_id or not session_id:
        return
    try:
        _cache().set_json(
            context_key(tenant_id, user_id, session_id),
            {
                "recent_order_ids": normalized[:20],
                "source_intent": source_intent,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ttl=_CONTEXT_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("[ContextResolver] record order list failed: %s", exc)


def clear_recent_context(tenant_id: str, user_id: str, session_id: str) -> None:
    """会话结束/需要失效时清除（供显式调用；TTL 是兜底）。"""
    try:
        _cache().set_json(context_key(tenant_id, user_id, session_id), None, ttl=1)
    except Exception as exc:
        # 失效失败 ≠ 没有要失效的东西（结构病审查 P3-3）：静默 pass 会让上一轮
        # 的上下文在 TTL 到期前一直生效，表现为「新会话答了旧会话的事」，
        # 而且没有任何线索可查。写不清楚为什么失败 = 排查从零开始。
        logger.warning(
            "[ContextResolver] 清除会话上下文失败（旧上下文会保留到 TTL 到期）: %s",
            exc,
        )


def extract_explicit_order(query: str) -> str | None:
    """提取当前轮显式订单号（无则 None）。显式实体优先级最高。"""
    if not query:
        return None
    m = _EXPLICIT_ORDER.search(query)
    return m.group(1) if m else None


def resolve_turn_reference(
    query: str,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> ContextResolution | None:
    """在 CS Router 判域之前解析回指表达。

    返回 None 表示本轮不做继承（显式订单号 / 无回指 / 无上下文 / 负向
    语境），调用方用原始 query 照常路由 —— 绝不因此产生 order_id。
    显式订单号场景由 ``resolve_explicit_order_route`` 单独处理（路由
    纠偏，非继承）。
    """
    if not query or len(query) > _MAX_REFERENCE_LEN:
        return None

    # 当前轮显式订单号优先：让位（显式实体 > 继承实体，锁死规则）
    if _EXPLICIT_ORDER.search(query):
        return None

    recent = get_recent_business_context(tenant_id, user_id, session_id)
    order_id = (recent or {}).get("last_order_id") or ""
    ordinal = _ORDINAL_ORDER.search(query)
    if ordinal:
        token = ordinal.group(1)
        index = int(token) if token.isdigit() else _CN_ORDINAL.get(token, 0)
        order_ids = list((recent or {}).get("recent_order_ids") or [])
        if index <= 0 or index > len(order_ids):
            return None
        order_id = str(order_ids[index - 1]).strip()
        if not order_id:
            return None
    elif not order_id:
        return None  # 无上下文：不得凭空产生 order_id

    # 负向语境（知识/投诉/非客服）优先于回指判定
    if _REFERENCE_NEGATIVE.search(query):
        return None

    if not ordinal and not (
        _REFERENCE_PRONOUN.search(query) and _REFERENCE_PREDICATE.search(query)
    ):
        return None  # 不是订单回指表达：照常路由，不强行注入

    if _LOGISTICS_HINT.search(query):
        resolved_query = f"查询订单 {order_id} 的物流状态"
    else:
        resolved_query = f"查询订单 {order_id} 的最新状态"

    resolution = ContextResolution(
        order_id=order_id,
        resolved_query=resolved_query,
        original_query=query,
        source_intent=str(recent.get("source_intent") or ""),
        inherited_entities={"order_id": order_id},
    )
    logger.info(
        "[ContextResolver] inherited order=%s source_intent=%s query=%s",
        order_id, resolution.source_intent, query[:30],
    )
    return resolution


def resolve_explicit_order_route(query: str) -> ContextResolution | None:
    """显式订单号的路由纠偏（缺陷9 第一半：「查 MO-1001」落 knowledge）。

    「查 MO-3C052B3A」这类含显式订单号的消息无客服域中文关键词时，
    coarse 落 UNKNOWN → knowledge 拒答。提取订单号后以规范化 query
    「查询订单 X」路由（实测 TRANSACTION/t_order_status 0.8 → query
    expert 精确查单），original_query 仍原样落库/展示。
    """
    order_id = extract_explicit_order(query)
    if not order_id:
        return None
    return ContextResolution(
        order_id=order_id,
        resolved_query=f"查询订单 {order_id}",
        resolution_type="explicit_entity",
        original_query=query,
        inherited_entities={"order_id": order_id},
    )

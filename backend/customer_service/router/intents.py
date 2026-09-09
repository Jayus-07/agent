"""customer_service/router/intents.py — 意图定义、KB 映射、路由路径解析"""
from __future__ import annotations

from backend.customer_service.router.types import CSDomain, CSRoutePath, IntentProfile

# ── 细粒度意图定义（~20 个）──
FINE_INTENTS: dict[str, str] = {
    # KNOWLEDGE
    "k_policy": "政策咨询",
    "k_product": "产品咨询",
    "k_faq": "常见问题",
    # TRANSACTION
    "t_order_status": "订单状态查询",
    "t_logistics": "物流查询",
    "t_delivery_estimate": "配送时效",
    # AFTER_SALES
    "as_refund": "退款",
    "as_return": "退货",
    "as_exchange": "换货",
    "as_repair": "维修",
    "as_quality_issue": "质量问题",
    # ACCOUNT
    "a_password": "密码问题",
    "a_address": "地址修改",
    "a_login_issue": "登录问题",
    # COMPLAINT
    "c_complaint": "投诉",
    "c_feedback": "意见反馈",
    # HUMAN
    "h_handoff": "转人工",
    "h_supervisor": "找主管",
    # PROMOTION (mapped to KNOWLEDGE path)
    "k_promotion": "促销活动",
    "k_warranty": "保修政策",
}

# ── 意图画像（执行需求）──
INTENT_PROFILES: dict[str, IntentProfile] = {
    # KNOWLEDGE → knowledge_query
    "k_policy": IntentProfile(
        intent="k_policy", domain=CSDomain.KNOWLEDGE,
        route_path=CSRoutePath.KNOWLEDGE_QUERY,
        kb_ids=["cs_faq", "cs_policy"],
    ),
    "k_product": IntentProfile(
        intent="k_product", domain=CSDomain.KNOWLEDGE,
        route_path=CSRoutePath.KNOWLEDGE_QUERY,
        kb_ids=["cs_product", "cs_faq"],
    ),
    "k_faq": IntentProfile(
        intent="k_faq", domain=CSDomain.KNOWLEDGE,
        route_path=CSRoutePath.KNOWLEDGE_QUERY,
        kb_ids=["cs_faq"],
    ),
    "k_promotion": IntentProfile(
        intent="k_promotion", domain=CSDomain.KNOWLEDGE,
        route_path=CSRoutePath.KNOWLEDGE_QUERY,
        kb_ids=["cs_policy", "cs_faq"],
    ),
    "k_warranty": IntentProfile(
        intent="k_warranty", domain=CSDomain.KNOWLEDGE,
        route_path=CSRoutePath.KNOWLEDGE_QUERY,
        kb_ids=["cs_policy", "cs_product"],
    ),
    # TRANSACTION → business_query (Phase 3)
    "t_order_status": IntentProfile(
        intent="t_order_status", domain=CSDomain.TRANSACTION,
        requires_auth=True, route_path=CSRoutePath.BUSINESS_QUERY,
        kb_ids=["cs_faq"],
    ),
    "t_logistics": IntentProfile(
        intent="t_logistics", domain=CSDomain.TRANSACTION,
        requires_auth=True, route_path=CSRoutePath.BUSINESS_QUERY,
        kb_ids=["cs_faq"],
    ),
    "t_delivery_estimate": IntentProfile(
        intent="t_delivery_estimate", domain=CSDomain.TRANSACTION,
        route_path=CSRoutePath.KNOWLEDGE_QUERY,
        kb_ids=["cs_policy", "cs_faq"],
    ),
    # AFTER_SALES → business_action (Phase 4)
    "as_refund": IntentProfile(
        intent="as_refund", domain=CSDomain.AFTER_SALES,
        requires_auth=True, requires_action=True, risk_level="high",
        route_path=CSRoutePath.BUSINESS_ACTION,
        kb_ids=["cs_policy", "cs_aftersales"],
    ),
    "as_return": IntentProfile(
        intent="as_return", domain=CSDomain.AFTER_SALES,
        requires_auth=True, requires_action=True, risk_level="high",
        route_path=CSRoutePath.BUSINESS_ACTION,
        kb_ids=["cs_policy", "cs_aftersales"],
    ),
    "as_exchange": IntentProfile(
        intent="as_exchange", domain=CSDomain.AFTER_SALES,
        requires_auth=True, requires_action=True, risk_level="medium",
        route_path=CSRoutePath.BUSINESS_ACTION,
        kb_ids=["cs_policy", "cs_aftersales"],
    ),
    "as_repair": IntentProfile(
        intent="as_repair", domain=CSDomain.AFTER_SALES,
        requires_auth=True, route_path=CSRoutePath.BUSINESS_QUERY,
        kb_ids=["cs_aftersales", "cs_product"],
    ),
    "as_quality_issue": IntentProfile(
        intent="as_quality_issue", domain=CSDomain.AFTER_SALES,
        requires_auth=True, route_path=CSRoutePath.BUSINESS_QUERY,
        kb_ids=["cs_aftersales", "cs_policy"],
    ),
    # ACCOUNT → business_action (Phase 4)
    "a_password": IntentProfile(
        intent="a_password", domain=CSDomain.ACCOUNT,
        requires_auth=True, requires_action=True, risk_level="medium",
        route_path=CSRoutePath.BUSINESS_ACTION,
        kb_ids=["cs_faq"],
    ),
    "a_address": IntentProfile(
        intent="a_address", domain=CSDomain.ACCOUNT,
        requires_auth=True, requires_action=True, risk_level="medium",
        route_path=CSRoutePath.BUSINESS_ACTION,
        kb_ids=["cs_faq"],
    ),
    "a_login_issue": IntentProfile(
        intent="a_login_issue", domain=CSDomain.ACCOUNT,
        route_path=CSRoutePath.KNOWLEDGE_QUERY,
        kb_ids=["cs_faq"],
    ),
    # COMPLAINT → complaint_flow (Phase 5)
    "c_complaint": IntentProfile(
        intent="c_complaint", domain=CSDomain.COMPLAINT,
        requires_auth=True, risk_level="high",
        route_path=CSRoutePath.COMPLAINT_FLOW,
        kb_ids=["cs_complaint", "cs_scripts"],
    ),
    "c_feedback": IntentProfile(
        intent="c_feedback", domain=CSDomain.COMPLAINT,
        route_path=CSRoutePath.KNOWLEDGE_QUERY,
        kb_ids=["cs_scripts"],
    ),
    # HUMAN → human_handoff
    "h_handoff": IntentProfile(
        intent="h_handoff", domain=CSDomain.HUMAN,
        route_path=CSRoutePath.HUMAN_HANDOFF,
        kb_ids=[],
    ),
    "h_supervisor": IntentProfile(
        intent="h_supervisor", domain=CSDomain.HUMAN,
        risk_level="medium",
        route_path=CSRoutePath.HUMAN_HANDOFF,
        kb_ids=[],
    ),
}

# ── 意图 → KB 快捷映射（kb_ids_for 使用）──
INTENT_KB_MAP: dict[str, list[str]] = {
    k: v.kb_ids for k, v in INTENT_PROFILES.items()
}

# ── 域 → 默认意图（fallback）──
DOMAIN_DEFAULT_INTENT: dict[str, str] = {
    CSDomain.KNOWLEDGE.value: "k_faq",
    CSDomain.TRANSACTION.value: "t_order_status",
    CSDomain.AFTER_SALES.value: "as_refund",
    CSDomain.ACCOUNT.value: "a_login_issue",
    CSDomain.COMPLAINT.value: "c_complaint",
    CSDomain.HUMAN.value: "h_handoff",
}


def resolve_route_path(intent: str) -> CSRoutePath:
    """根据意图解析路由路径。未知意图回退 knowledge_query。"""
    profile = INTENT_PROFILES.get(intent)
    if profile:
        return profile.route_path
    return CSRoutePath.KNOWLEDGE_QUERY


def kb_ids_for(intent: str, domain: str | CSDomain | None = None) -> list[str]:
    """获取意图对应的 KB 列表。未知意图回退 cs_faq。"""
    kbs = INTENT_KB_MAP.get(intent)
    if kbs:
        return kbs
    if domain is not None:
        domain_val = domain.value if isinstance(domain, CSDomain) else domain
        default_intent = DOMAIN_DEFAULT_INTENT.get(domain_val, "k_faq")
        return INTENT_KB_MAP.get(default_intent, ["cs_faq"])
    return ["cs_faq"]

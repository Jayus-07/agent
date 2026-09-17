"""customer_service/router/fine_router.py — 客服细分类路由

按域分流（2026-09-17 删除 Chroma 向量层，全站存储收口 pgvector）:
  - KNOWLEDGE / COMPLAINT / HUMAN: 规则直接映射（关键词 → 意图）
  - 其余域: 规则未命中时落 DOMAIN_DEFAULT_INTENT 默认意图
"""
from __future__ import annotations

from backend.customer_service.router.types import CSDomain

# ── 规则意图映射（K / C / H 域用）──
_RULE_INTENT_MAP: dict[str, dict[str, list[str]]] = {
    "KNOWLEDGE": {
        "k_policy": ["政策", "退货政策", "保修", "条款", "退换条件"],
        "k_product": ["产品", "规格", "参数", "怎么用", "使用说明"],
        "k_promotion": ["活动", "优惠", "促销", "满减", "优惠券", "折扣"],
        "k_warranty": ["保修期", "延保", "保修卡", "保修范围"],
        "k_faq": ["营业时间", "支付方式", "发票", "配送范围"],
    },
    "COMPLAINT": {
        "c_complaint": ["投诉", "举报", "不满意", "差评", "说法", "维权"],
        "c_feedback": ["建议", "反馈", "改进", "希望"],
    },
    "HUMAN": {
        "h_handoff": ["转人工", "真人", "人工客服", "人工服务", "不要机器人"],
        "h_supervisor": ["领导", "经理", "主管", "负责人"],
    },
}


class CSFineRouter:
    """客服细分类 — 输出 intent 字符串。"""

    def classify(self, query: str, domain: CSDomain) -> tuple[str, float, str]:
        """细分类入口。

        Returns:
            (intent, confidence, reason)
        """
        domain_val = domain.value

        if domain_val in _RULE_INTENT_MAP:
            intent, conf, reason = self._rule_classify(query, domain_val)
            if conf > 0:
                return intent, conf, f"rule: {reason}"

        from backend.customer_service.router.intents import DOMAIN_DEFAULT_INTENT
        default = DOMAIN_DEFAULT_INTENT.get(domain_val, "k_faq")
        # P2.1（audit #156）：领域默认意图是「有依据的猜测」，其置信度应对齐
        # Supervisor 门槛（CS_CONFIDENCE_CAUTIOUS）——此前硬编码 0.3 被
        # cs_router 平均后打穿 0.6 门槛，正常 FAQ 被 Layer 1c 直接 finish，
        # 知识库明明可答却拿到泛化兜底。
        from backend.config.customer_service import CS_CONFIDENCE_CAUTIOUS
        return default, CS_CONFIDENCE_CAUTIOUS, f"default_fallback: {default}"

    def _rule_classify(self, query: str, domain: str) -> tuple[str, float, str]:
        intent_map = _RULE_INTENT_MAP.get(domain, {})
        best_intent, best_count = "k_faq", 0
        for intent, keywords in intent_map.items():
            count = sum(1 for kw in keywords if kw in query)
            if count > best_count:
                best_intent, best_count = intent, count

        if best_count >= 1:
            conf = min(best_count / 2.0, 1.0)
            # P3.5：规则关键词命中（退款/物流/发票等域专属词）是比
            # default_fallback 更强的证据，置信度下限对齐 Supervisor
            # 闸门——否则 coarse hint 0.5 + fine 0.5 平均后 0.55 打穿
            # 0.6，明确的退款诉求被 Layer1 直接 finish 成兜底ack
            from backend.config.customer_service import CS_CONFIDENCE_CAUTIOUS
            conf = max(conf, CS_CONFIDENCE_CAUTIOUS)
            return best_intent, conf, f"{best_intent}({best_count}hits)"
        return "k_faq", 0.0, ""

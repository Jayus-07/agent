"""customer_service/router/fine_router.py — 客服细分类路由

按域分流（2026-09-17 删除 Chroma 向量层，全站存储收口 pgvector）:
  - KNOWLEDGE / COMPLAINT / HUMAN: 规则直接映射（关键词 → 意图）
  - 其余域: 规则未命中时落 DOMAIN_DEFAULT_INTENT 默认意图
"""
from __future__ import annotations

from backend.customer_service.router.types import CSDomain

# ── 规则意图映射（K / C / H 域用；TRANSACTION/AFTER_SALES/ACCOUNT 批次补齐）──
_RULE_INTENT_MAP: dict[str, dict[str, list[str]]] = {
    "KNOWLEDGE": {
        # P3（cs-evaluation-upgrade 2026-11-02）：去裸「保修」——与 k_warranty
        # 的「保修期」单命中时按 dict 序被 k_policy 抢走（「商品保修期是多久」
        # 误判 k_policy）；显式「保修政策」类问句仍由「政策」命中。
        "k_policy": ["政策", "退货政策", "条款", "退换条件"],
        "k_product": ["产品", "规格", "参数", "怎么用", "使用说明"],
        "k_promotion": ["活动", "优惠", "促销", "满减", "优惠券", "折扣"],
        "k_warranty": ["保修期", "延保", "保修卡", "保修范围"],
        "k_faq": ["营业时间", "支付方式", "发票", "配送范围"],
        # P3：配送时效问句（coarse delivery_policy 通道送 KNOWLEDGE 域，
        # 此前 fine 无细分词表落 k_faq 兜底）
        "t_delivery_estimate": [
            "什么时候能发货", "多久能到", "几天到", "多久发货",
            "几天能到", "多久能送到", "几号能送到",
        ],
    },
    "COMPLAINT": {
        "c_complaint": ["投诉", "举报", "不满意", "差评", "说法", "维权"],
        "c_feedback": ["建议", "反馈", "改进", "希望"],
    },
    "HUMAN": {
        "h_handoff": ["转人工", "真人", "人工客服", "人工服务", "不要机器人"],
        "h_supervisor": ["领导", "经理", "主管", "负责人"],
    },
    # 批次C：TRANSACTION 域规则直查（工单进度）
    "TRANSACTION": {
        "t_ticket_status": ["工单", "投诉单", "报修单", "投诉进度"],
        "t_refund_status": [
            "申请审核", "退款申请进度", "退款申请状态", "审核到哪",
        ],
        # P2 商城域：余额/到账查询（vocab 出域豁免词表已含「余额」，域内命中）
        "t_balance_query": ["余额", "钱包", "到账", "退回哪里", "账户还有多少钱"],
        # P3（cs-evaluation-upgrade 2026-11-02）：物流/时效细分——此前
        # TRANSACTION 只有工单/退款进度/余额词表，「订单 X 的物流到哪了」
        # 全部落域默认 t_order_status
        "t_logistics": ["物流", "快递", "包裹", "配送"],
        "t_delivery_estimate": ["多久能到", "几天能到", "几号能送到", "多久送到"],
    },
    # 缺陷6（2026-09-23）：AFTER_SALES 域此前只能落 DOMAIN_DEFAULT_INTENT
    # （as_refund），「我要退货」被当成退款 —— 动作类型错了，确认卡与后续
    # 执行全偏。按域专属关键词精化动作意图（退货/退款/换货互斥）。
    "AFTER_SALES": {
        "as_refund_reason_update": [
            "退款原因", "原因写错", "修改申请原因", "更正退款原因",
        ],
        "t_refund_status": [
            "申请审核", "退款申请进度", "退款申请状态", "审核到哪",
        ],
        # P2：取消订单（显式短语，避免「取消」误伤确认卡的取消语义）
        "t_order_cancel": [
            "取消订单", "我要取消", "退掉订单", "把订单取消", "订单不要了",
        ],
        "as_return": ["退货", "退换", "return"],
        "as_exchange": ["换货", "exchange", "换个", "换成"],
        "as_repair": ["维修", "报修", "送修", "返修", "修一下"],
        "as_quality_issue": [
            "质量问题", "破损", "瑕疵", "裂痕", "进水",
            # P3：自然表达质量诉求（「屏幕有点划痕」「质量有问题」「收到是坏的」）
            "划痕", "质量有问题", "是坏的",
        ],
        "as_refund": ["退款", "退钱", "refund"],
    },
    # P3（cs-evaluation-upgrade 2026-11-02）：ACCOUNT 域此前无细分规则，
    # 地址/密码诉求全部落域默认 a_login_issue（动作类型错，确认卡与后续
    # 执行全偏——同缺陷 6 的 AFTER_SALES 教训）
    "ACCOUNT": {
        "a_address": ["收货地址", "地址", "手机号", "换绑"],
        "a_password": ["密码", "重置"],
        "a_login_issue": ["登录", "验证码"],
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

"""config/customer_service.py — 客服系统配置"""
import os
import re

from dotenv import load_dotenv

load_dotenv()

# =============================================
# 客服系统总开关
# =============================================
CS_ENABLED = os.getenv("CS_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# =============================================
# 确认状态机
# =============================================
CS_CONFIRMATION_TTL_SECONDS = int(os.getenv("CS_CONFIRMATION_TTL_SECONDS", "300"))
CS_MAX_CONFIRMATION_RETRIES = int(os.getenv("CS_MAX_CONFIRMATION_RETRIES", "3"))

# =============================================
# 人工转接
# =============================================
CS_HANDOFF_TIMEOUT_SECONDS = int(os.getenv("CS_HANDOFF_TIMEOUT_SECONDS", "600"))
CS_HANDOFF_LOW_CONF_THRESHOLD = float(os.getenv("CS_HANDOFF_LOW_CONF_THRESHOLD", "0.4"))
CS_HANDOFF_CONSECUTIVE_FAIL_LIMIT = int(os.getenv("CS_HANDOFF_CONSECUTIVE_FAIL_LIMIT", "3"))

# =============================================
# 风险等级定义
# =============================================
CS_HIGH_RISK_ACTIONS = frozenset({
    "refund_execute",
    "return_execute",
    "order_cancel",
})

CS_CRITICAL_ACTIONS = frozenset({
    "account_delete",
    "bulk_refund",
})

# =============================================
# 客服知识库 ID
# =============================================
CS_KNOWLEDGE_BASES = {
    "cs_faq": {
        "name": "客服FAQ",
        "description": "常见问题与标准回答",
    },
    "cs_product": {
        "name": "产品知识库",
        "description": "产品规格、参数、使用说明",
    },
    "cs_policy": {
        "name": "政策知识库",
        "description": "退换货政策、保修条款、服务承诺",
    },
    "cs_aftersales": {
        "name": "售后知识库",
        "description": "售后流程、维修指南",
    },
    "cs_complaint": {
        "name": "投诉处理知识库",
        "description": "投诉处理流程、补偿标准",
    },
    "cs_scripts": {
        "name": "话术知识库",
        "description": "标准话术模板、场景应对",
    },
}

# =============================================
# 投诉检测正则
# =============================================
COMPLAINT_PATTERNS = [
    re.compile(p) for p in [
        r"投诉", r"举报", r"太差了", r"服务差", r"不满意",
        r"要.*说法", r"找.*领导", r"12315", r"消协",
        r"差评", r"曝光", r"维权",
    ]
]

# =============================================
# 客服域意图关键词（粗分类用）
# =============================================
CS_DOMAIN_KEYWORDS = {
    "KNOWLEDGE": [
        "怎么", "如何", "什么是", "请问", "告诉我", "介绍",
        "说明", "解释", "有没有", "能不能",
    ],
    "TRANSACTION": [
        "订单", "物流", "快递", "发货", "收货", "签收",
        " tracking", "配送", "运输",
    ],
    "AFTER_SALES": [
        "退款", "退货", "换货", "退换", "售后", "维修",
        "保修", "质量问题", "破损",
    ],
    "ACCOUNT": [
        "账户", "账号", "密码", "登录", "注册", "修改信息",
        "地址", "收货地址",
    ],
    "COMPLAINT": [
        "投诉", "举报", "不满意", "差评", "态度差",
    ],
    "HUMAN": [
        "人工", "客服", "真人", "转接", "经理", "主管",
    ],
}

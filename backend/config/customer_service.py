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
CS_CONFIRMATION_TTL_SECONDS = int(os.getenv("CS_CONFIRMATION_TTL_SECONDS", "900"))
CS_MAX_CONFIRMATION_RETRIES = int(os.getenv("CS_MAX_CONFIRMATION_RETRIES", "3"))

# =============================================
# 确认 / 取消关键词
# =============================================
CS_CONFIRM_KEYWORDS = frozenset({
    "确认", "确定", "好的", "同意", "可以", "没问题", "是的",
    "嗯", "对", "ok", "yes", "confirm",
})
CS_CANCEL_KEYWORDS = frozenset({
    "取消", "算了", "不要", "不", "否", "放弃",
    "cancel", "no",
})

# =============================================
# 大额退款阈值（达到此金额自动升级为 CRITICAL）
# =============================================
CS_CRITICAL_REFUND_AMOUNT = float(os.getenv("CS_CRITICAL_REFUND_AMOUNT", "1000"))

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

# =============================================
# 客服域检测正则（Domain Detector 规则通道）
# =============================================
CS_DOMAIN_PATTERNS: dict[str, list] = {
    "KNOWLEDGE": [
        re.compile(p) for p in [
            r"怎么(办|样|弄|退|换|修)", r"如何(操|退|换|查)",
            r"什么(是|时候|原因|条件)", r"请问", r"告[诉我]",
            r"介绍(一下)?", r"解释(一下)?", r"有没有",
            r"能不能", r"可以吗", r"是否",
        ]
    ],
    "TRANSACTION": [
        re.compile(p) for p in [
            r"(查|看|跟).*(订单|物流|快递|发货|收货|签收)",
            r"(订单|物流|快递).*(状态|进度|到哪|在哪)",
            r"(发货|收货|签收).*(了没|没有|了吗)",
            r"配送", r"运输", r"tracking",
        ]
    ],
    "AFTER_SALES": [
        re.compile(p) for p in [
            r"(退|换|修).*(款|货|一下|怎么)",
            r"售后", r"(质量|产品).*(问题|坏了|坏了|破损)",
            r"保修", r"维修", r"不好用", r"坏了",
        ]
    ],
    "ACCOUNT": [
        re.compile(p) for p in [
            r"(修改|更改|换).*(密码|地址|手机|邮箱)",
            r"(登录|注册).*(不了|不上|失败|问题)",
            r"账户.*问题", r"账号.*异常",
        ]
    ],
    "COMPLAINT": [
        re.compile(p) for p in [
            r"投诉", r"举报", r"(态度|服务).*(差|烂|垃圾)",
            r"不满意", r"要.*说法", r"找.*领导",
            r"12315", r"消协", r"差评", r"曝光", r"维权",
        ]
    ],
    "HUMAN": [
        re.compile(p) for p in [
            r"(转|找).*(人工|客服|真人|经理)", r"人工服务",
            r"不要机器人", r"你是.*机器人.*吗",
        ]
    ],
}

# =============================================
# 客服 Router 阈值（env 可覆盖：容器云端 embedding 与种子调参时的本地 BGE
# 分数分布不同，固定值易漏判；默认对齐 coarse_router 的 0.60 采纳线）
# =============================================
CS_VECTOR_THRESHOLD = float(os.getenv("CS_VECTOR_THRESHOLD", "0.60"))
# 向量强匹配单独决定线（与 coarse_router 的"≥0.85 决定"语义对齐）：
# 域检测原本要求 rule≥2 且 vec≥0.70 同时成立，导致"申请退款"(vec=0.94, rule=1hit)
# 这类明显客服问法被漏判，客服链路几乎无法触发
CS_VECTOR_DECIDE = float(os.getenv("CS_VECTOR_DECIDE", "0.85"))
CS_RULE_MIN_HITS = int(os.getenv("CS_RULE_MIN_HITS", "2"))
CS_CONFIDENCE_ANSWER = 0.85
CS_CONFIDENCE_CAUTIOUS = 0.60
CS_ROUTER_INDEX_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "cs_router_index"
)

# =============================================
# 独立 CS Graph（Phase 0 新增）
# =============================================
CS_GRAPH_ENABLED = os.getenv("CS_GRAPH_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CS_EXPERT_MAX_LOOPS = int(os.getenv("CS_EXPERT_MAX_LOOPS", "5"))
CS_SUPERVISOR_LLM_ENABLED = os.getenv("CS_SUPERVISOR_LLM_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CS_SUPERVISOR_LLM_TIMEOUT_MS = int(os.getenv("CS_SUPERVISOR_LLM_TIMEOUT_MS", "800"))

# =============================================
# 投诉严重度 LLM 兜底（规则 0 命中时的级联第二层）
# =============================================
CS_COMPLAINT_LLM_ENABLED = os.getenv("CS_COMPLAINT_LLM_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CS_COMPLAINT_LLM_TIMEOUT_MS = int(os.getenv("CS_COMPLAINT_LLM_TIMEOUT_MS", "3000"))

# =============================================
# Query 专家复合问题 LLM 意图分解
# =============================================
CS_QUERY_LLM_DECOMPOSE_ENABLED = os.getenv("CS_QUERY_LLM_DECOMPOSE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CS_QUERY_LLM_TIMEOUT_MS = int(os.getenv("CS_QUERY_LLM_TIMEOUT_MS", "3000"))
CS_CHECKPOINTER_ENABLED = os.getenv("CS_CHECKPOINTER_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# checkpointer 后端：postgres（生产，跨进程/重启持久）| memory（本地调试降级）
CS_CHECKPOINTER_BACKEND = os.getenv("CS_CHECKPOINTER_BACKEND", "postgres").strip().lower()
CS_GRAPH_RECURSION_LIMIT = int(os.getenv("CS_GRAPH_RECURSION_LIMIT", "20"))

# ── CS 流量灰度（CS_ENABLED=true 时的放量控制）────────────────
# percent: 0-100，按 session_id 稳定哈希放量（同一会话永远同一组，避免体验分裂）
CS_ROLLOUT_PERCENT = max(0, min(100, int(os.getenv("CS_ROLLOUT_PERCENT", "100"))))
# 白名单：逗号分隔的 session_id，始终命中 treatment 组（调试/内部账号用）
CS_ROLLOUT_WHITELIST = {
    s.strip() for s in os.getenv("CS_ROLLOUT_WHITELIST", "").split(",") if s.strip()
}

# ── CS 质量报告告警阈值（超过即触发 alerts）────────────────
CS_QUALITY_ALERT_FALLBACK_RATE = float(os.getenv("CS_QUALITY_ALERT_FALLBACK_RATE", "0.02"))
CS_QUALITY_ALERT_ROUTE_CONSISTENCY = float(os.getenv("CS_QUALITY_ALERT_ROUTE_CONSISTENCY", "0.85"))
CS_QUALITY_ALERT_CAPABILITY_HANDOFF_RATE = float(os.getenv("CS_QUALITY_ALERT_CAPABILITY_HANDOFF_RATE", "0.15"))

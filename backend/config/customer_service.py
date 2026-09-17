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
# 客服域规则 — 单一配置块（P2.2 映射统一：两套词表成对维护）
# =============================================
# 每域同时定义 keywords（字面计数 → coarse_router 规则通道）与
# patterns（语序正则 → domain_detector 的 is_cs 判定 + 域 hint）。
# 两层语义不同但词表必须成对维护，防止各自漂移（audit P2-22）。
_CS_DOMAIN_RULES = {
    "KNOWLEDGE": {
        "keywords": [
            "怎么", "如何", "什么是", "请问", "告诉我", "介绍",
            "说明", "解释", "有没有", "能不能",
        ],
        "patterns": [
            r"怎么(办|样|弄|退|换|修)", r"如何(操|退|换|查)",
            r"什么(是|时候|原因|条件)", r"请问", r"告[诉我]",
            r"介绍(一下)?", r"解释(一下)?", r"有没有",
            r"能不能", r"可以吗", r"是否",
        ],
    },
    "TRANSACTION": {
        "keywords": [
            "订单", "物流", "快递", "发货", "收货", "签收",
            "tracking", "配送", "运输",
        ],
        "patterns": [
            r"(查|看|跟).*(订单|物流|快递|发货|收货|签收)",
            r"(订单|物流|快递).*(状态|进度|到哪|在哪)",
            r"(发货|收货|签收).*(了没|没有|了吗)",
            # 陈述式抱怨语序（2026-09-17 补召回）："没"在动词前的表述
            # （"一直没发货/还没到"）此前一条正则都不中
            r"(订单|快递|包裹|物流).*(没|未).*(发货|发出|到货|收到|动静|更新)",
            r"(一直没|迟迟没|迟迟不|还没|还没有)(发货|到货|送到|收到|更新|动静)",
            r"没(发货|到货|动静)",
            r"配送", r"运输", r"tracking",
        ],
    },
    "AFTER_SALES": {
        "keywords": [
            "退款", "退货", "换货", "退换", "售后", "维修",
            "保修", "质量问题", "破损",
        ],
        "patterns": [
            r"(退|换|修).*(款|货|一下|怎么)",
            r"售后", r"(质量|产品).*(问题|坏了|坏了|破损)",
            r"保修", r"维修", r"不好用", r"坏了",
        ],
    },
    "ACCOUNT": {
        "keywords": [
            "账户", "账号", "密码", "登录", "注册", "修改信息",
            "地址", "收货地址",
        ],
        "patterns": [
            r"(修改|更改|换).*(密码|地址|手机|邮箱)",
            r"(登录|注册).*(不了|不上|失败|问题)",
            r"账户.*问题", r"账号.*异常",
        ],
    },
    "COMPLAINT": {
        "keywords": ["投诉", "举报", "不满意", "差评", "态度差"],
        "patterns": [
            r"投诉", r"举报", r"(态度|服务).*(差|烂|垃圾)",
            r"不满意", r"要.*说法", r"找.*领导",
            r"12315", r"消协", r"差评", r"曝光", r"维权",
        ],
    },
    "HUMAN": {
        "keywords": ["人工", "客服", "真人", "转接", "经理", "主管"],
        "patterns": [
            r"(转|找).*(人工|客服|真人|经理)", r"人工服务",
            r"不要机器人", r"你是.*机器人.*吗",
        ],
    },
}

# 下游消费方常量（名字保持不变）：coarse_router 用 KEYWORDS，domain_detector 用 PATTERNS
CS_DOMAIN_KEYWORDS = {
    domain: rules["keywords"] for domain, rules in _CS_DOMAIN_RULES.items()
}
CS_DOMAIN_PATTERNS: dict[str, list] = {
    domain: [re.compile(p) for p in rules["patterns"]]
    for domain, rules in _CS_DOMAIN_RULES.items()
}

# =============================================
# 客服 Router 阈值
# （2026-09-17 删除向量通道及其阈值 CS_VECTOR_THRESHOLD/CS_VECTOR_DECIDE
#   与索引目录 CS_ROUTER_INDEX_DIR；域检测为纯规则判定。若进线率异常，
#   调低 CS_RULE_MIN_HITS 或扩充 CS_DOMAIN_PATTERNS。）
# =============================================
CS_RULE_MIN_HITS = int(os.getenv("CS_RULE_MIN_HITS", "2"))
CS_CONFIDENCE_ANSWER = 0.85
CS_CONFIDENCE_CAUTIOUS = 0.60

# 专家显式超时（P2.3）：knowledge（RAG 检索+LLM 生成实测 1~30s）、
# action（DB 写+状态机）节点级限时；0/负值 = 不限时。query expert 内部
# 已有自己的 RAG ask 线程级超时，不经此参数。
CS_EXPERT_TIMEOUT_S = float(os.getenv("CS_EXPERT_TIMEOUT_S", "60"))

# =============================================
# 独立 CS Graph（Phase 0 新增）
# =============================================
CS_GRAPH_ENABLED = os.getenv("CS_GRAPH_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CS_EXPERT_MAX_LOOPS = int(os.getenv("CS_EXPERT_MAX_LOOPS", "5"))

# ── 状态写入严格模式（P1 重构 2026-09-17）────────────────────
# true（生产默认）：HandoffStore/ConfirmationStore 的 DB 写失败 → error 级日志
#   + 记录指标 + 抛 StoreWriteError（PostgreSQL 是唯一事实源，禁止静默降级
#   成内存态——此前 cache-only 降级导致内存与 DB 永久分叉，见
#   docs/customer-service/audit-report.md §P0-5）。
# false：写失败仅告警并继续用内存态（仅限单元测试/无 DB 的本地调试）。
# 测试进程（pytest）默认 false，除非显式设置该环境变量。
import sys as _sys

CS_STORE_STRICT_WRITES = os.getenv("CS_STORE_STRICT_WRITES", "").strip().lower()
if CS_STORE_STRICT_WRITES in ("", "unset"):
    CS_STORE_STRICT_WRITES = not ("pytest" in _sys.modules)
else:
    CS_STORE_STRICT_WRITES = CS_STORE_STRICT_WRITES in ("1", "true", "yes")
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
# PostgresSaver checkpoint 保留天数：每轮对话都累积 checkpoint 行/blob，
# 无清理会无限膨胀。每日守护线程清理超过 TTL 的 checkpoint 及孤儿 blob/writes
CS_CHECKPOINT_TTL_DAYS = int(os.getenv("CS_CHECKPOINT_TTL_DAYS", "7"))
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

# =============================================
# 演示业务沙盒（Demo Business Sandbox）
# 方案: docs/customer-service/演示沙盒方案-2026-09-17.md
# =============================================
# 演示模式总开关：开启后客服业务查询将 user_id 映射为 CS_DEMO_CUSTOMER_ID，
# 使无真实业务数据的注册账号也能走完整客服流程。默认关闭，不影响生产链路。
CS_DEMO_MODE = os.getenv("CS_DEMO_MODE", "false").strip().lower() in ("1", "true", "yes")
# 演示数据归属的客户 ID（须与 backend/sql/seeds/demo_sandbox.sql 播种的 customer_id 一致）
CS_DEMO_CUSTOMER_ID = os.getenv("CS_DEMO_CUSTOMER_ID", "99001").strip()
# 物流轨迹 Provider：mock（演示）| 空=不启用（回退订单状态推导，供日后接入真实 API）
CS_DEMO_TRACE_PROVIDER = os.getenv("CS_DEMO_TRACE_PROVIDER", "mock").strip().lower()
# 演示故障注入（JSON，仅 demo 模式生效）：
#   {"logistics.timeout": true}            轨迹 Provider 超时
#   {"logistics.error": true}              轨迹 Provider 报错
#   {"logistics.empty": true}              轨迹返回空结果
#   {"logistics.delay_seconds": 2.0}       轨迹响应人为延迟（演示加载态）
CS_DEMO_FAULTS: dict = {}
_faults_raw = os.getenv("CS_DEMO_FAULTS", "").strip()
if _faults_raw:
    import json as _json
    try:
        CS_DEMO_FAULTS = _json.loads(_faults_raw)
    except ValueError:
        CS_DEMO_FAULTS = {}

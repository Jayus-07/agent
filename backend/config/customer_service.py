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
# 确认 / 取消关键词 —— 已收敛至 customer_service/vocab.py 单一事实源
# （迁移 B8）。旧版此处副本含裸「不」「对」，与 P1 修正版口径漂移且
# 无消费方，已删除；消费方统一走 vocab.get_confirm/cancel_keywords()。
# =============================================

# =============================================
# 大额退款阈值（达到此金额自动升级为 CRITICAL）
# =============================================
CS_CRITICAL_REFUND_AMOUNT = float(os.getenv("CS_CRITICAL_REFUND_AMOUNT", "1000"))

# =============================================
# 人工转接
# =============================================
# 总等待上限（2026-10-08 拍板：120→30s + 超时降级建留言工单）。语义不变：
# 工单入池与主管重派时盖 total_deadline_at 章，超期由 reaper 关单——关单时
# 压缩诉求登记 tickets 留言工单（GD- 单号）并通知用户 24h 回访（B 案）。
# 该值同时是「池空兜底」的等待窗口——可派池持续为空时工单最多悬挂这么久。
# 用户侧展示：messages 轮询接口的 handoff_meta.total_deadline_at 支撑倒计时。
CS_HANDOFF_TIMEOUT_SECONDS = int(os.getenv("CS_HANDOFF_TIMEOUT_SECONDS", "30"))
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
# 投诉检测正则 / 客服域规则词表 —— 已收敛至 customer_service/vocab.py
# 单一事实源（迁移 B8，词表值逐字搬移零漂移）；此处 re-export 保持
# 存量 import 兼容（coarse_router/domain_detector/测试）。
# =============================================
from backend.customer_service.vocab import (  # noqa: E402
    COMPLAINT_PATTERNS,
    CS_DOMAIN_KEYWORDS,
    CS_DOMAIN_PATTERNS,
    VOCAB_VERSION,
)

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
# Supervisor understanding 信号门（迁移 B5，2026-09-29）
# 风险兜底拦截（越权/注入信号拒答）+ P0 投诉直通（监管舆情信号强制派
# complaint）。信号源为纯规则零 LLM；关闭 = 回到接线前行为（只影响这两
# 个兜底分支，Input Guard 图前拦截不受影响）。
# =============================================
CS_SIGNAL_GATE_ENABLED = os.getenv("CS_SIGNAL_GATE_ENABLED", "true").strip().lower() in ("1", "true", "yes")

# =============================================
# Supervisor 决策 v2——七层固定优先级（迁移 B6，2026-09-29）
# 顺序即语义：1 handoff → 2 pending → 3 风险 → 4 循环预算 →
# 5 意图路由 → 6 低置信处理 → 7 LLM 兜底（设计方案 §4.2）。
# 与 v1 只在多条件并存时的裁决不同（单条件行为等价）。
# 默认启用；置 false 一键回退 v1 存量顺序（本机回退开关）。
# =============================================
CS_DECISION_V2 = os.getenv("CS_DECISION_V2", "true").strip().lower() in ("1", "true", "yes")

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

# ── 对话体验改造（2026-10-04 任务卡 T3/T5）────────────────────
# 寒暄分支：非业务对话走一次 LLM 人设生成（false=回旧漏斗路径）
CS_CHAT_FALLBACK_ENABLED = os.getenv("CS_CHAT_FALLBACK_ENABLED", "true").strip().lower() in ("1", "true", "yes")
# 寒暄 LLM 单次限时（线程级，sync_call_with_timeout 同款口径）
CS_CHAT_LLM_TIMEOUT_MS = int(os.getenv("CS_CHAT_LLM_TIMEOUT_MS", "8000"))
# 独立客服窗口：true=非客服域消息不出域，走出域固定话术（false=旧 redirect_main）
# 消费方 router_node（任务卡 T5，混线解锁后接线；先落配置保持口径单一源）
CS_WINDOW_STANDALONE = os.getenv("CS_WINDOW_STANDALONE", "true").strip().lower() in ("1", "true", "yes")
# 分诊阶梯（2026-10-08 服务器验收拍板 A 案）：出域消息先判引导去向——
# 旅游/选品话题引导去对口域入口而非固定话术挡回；平台外话题仍固定话术。
# false = 出域一律固定话术（现行为）
CS_TRIAGE_GUIDE_ENABLED = os.getenv("CS_TRIAGE_GUIDE_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# 排队态消息分流（C 案）：waiting_human 期间非客服诉求不再被排队话术吞掉
# ——寒暄/出域正常直答；客服诉求保持排队话术。human_active（人工已接入）
# 不分流，AI 零抢答铁律不动。false = 排队态一律排队话术（现行为）
CS_WAITING_HUMAN_DIVERT_ENABLED = os.getenv("CS_WAITING_HUMAN_DIVERT_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# ── 坐席辅助（agent assist，AI 给人工坐席实时推荐回复）────────
# 总开关：关闭后 hub 不再调度生成任务，前端无推荐事件（默认开启，失败静默）
CS_AGENT_ASSIST_ENABLED = os.getenv("CS_AGENT_ASSIST_ENABLED", "true").strip().lower() in ("1", "true", "yes")# 单次推荐最多条数
CS_AGENT_ASSIST_TOP_K = max(1, min(5, int(os.getenv("CS_AGENT_ASSIST_TOP_K", "3"))))
# 单次生成总超时（秒）：超时静默放弃，绝不阻塞消息主链路。
# 默认 15s：实测 RAG+LLM 一次生成 10-50s（首次调用含模型加载更久），
# 8s 会把正常推荐全判超时；15s 在"晚到推荐"与"体验"间取平衡。
CS_AGENT_ASSIST_TIMEOUT_SECONDS = float(os.getenv("CS_AGENT_ASSIST_TIMEOUT_SECONDS", "15"))
# 全局并发上限：同时生成的推荐任务数（背压保护，防 LLM/RAG 被打爆）
CS_AGENT_ASSIST_MAX_CONCURRENCY = max(1, int(os.getenv("CS_AGENT_ASSIST_MAX_CONCURRENCY", "4")))
# 参与推荐的最近消息条数
CS_AGENT_ASSIST_HISTORY_LIMIT = max(2, int(os.getenv("CS_AGENT_ASSIST_HISTORY_LIMIT", "10")))

# ── LLM 语义理解层 + Response Composer（2026-10-08 客服域 LLM 收口）──
# 意图补判（Rule First：规则明确时 LLM=0 次；失败回规则路由软降级）
CS_UNDERSTANDING_LLM_ENABLED = os.getenv("CS_UNDERSTANDING_LLM_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CS_UNDERSTANDING_LLM_TIMEOUT_MS = int(os.getenv("CS_UNDERSTANDING_LLM_TIMEOUT_MS", "2500"))
# 语义槽位补全（LLM 只出语义引用候选，真实 ID 由业务数据解析）
CS_SLOT_LLM_ENABLED = os.getenv("CS_SLOT_LLM_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CS_SLOT_LLM_TIMEOUT_MS = int(os.getenv("CS_SLOT_LLM_TIMEOUT_MS", "2500"))
# LLM 意图候选采纳后的路由置信度（固定保守值 > CS_CONFIDENCE_CAUTIOUS；
# 不采信 LLM 自报分数驱动路由，自报值只进 trace）
CS_LLM_INTENT_CONFIDENCE = float(os.getenv("CS_LLM_INTENT_CONFIDENCE", "0.65"))
# Response Composer（query/complaint 回复 LLM 转写；action/handoff 恒模板）
CS_RESPONSE_COMPOSER_ENABLED = os.getenv("CS_RESPONSE_COMPOSER_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CS_RESPONSE_COMPOSER_TIMEOUT_MS = int(os.getenv("CS_RESPONSE_COMPOSER_TIMEOUT_MS", "6000"))
# 确认语义 LLM 候选（词表 NONE 时的补判；词表已覆盖 P0 模糊确认案例，默认关）
CS_CONFIRM_LLM_CANDIDATE_ENABLED = os.getenv("CS_CONFIRM_LLM_CANDIDATE_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# ── 业务网关（批次B：客服业务服务经 HTTP 调真实业务系统）────────
# sandbox（默认）：直查内部演示库 + demo 身份映射，行为与历史版本一致
# http：经 infra/http/business_client 调 business-service（Java/mock），
#       仅收口的只读查询先切；失败返回友好话术，绝不降级回 sandbox 假数据
CS_BUSINESS_GATEWAY_MODE = os.getenv("CS_BUSINESS_GATEWAY_MODE", "sandbox").strip().lower()

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

# =============================================
# 非客服检测 · LLM 仲裁（customer_service/analyzer/non_cs_detector.py）
# =============================================
# env 名收口在本模块，业务侧禁止直接 os.getenv。用 getter（调用时求值）
# 而非模块常量：仲裁默认 OFF 是灰度开关，测试 monkeypatch setenv 后
# 必须立即生效（无重导依赖）。
ENV_CS_REDIRECT_MAIN_LLM_ENABLED = "CS_REDIRECT_MAIN_LLM_ENABLED"
ENV_CS_NON_CS_REDIRECT_THRESHOLD = "CS_NON_CS_REDIRECT_THRESHOLD"


def cs_redirect_main_llm_enabled() -> bool:
    """LLM 语义仲裁总开关（默认 OFF，正则漏判时才放开）。"""
    return os.getenv(ENV_CS_REDIRECT_MAIN_LLM_ENABLED, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def cs_non_cs_redirect_threshold() -> float:
    """转出置信阈值；非法/越界值回落 0.75（与非客服检测既有口径一致）。"""
    try:
        v = float(os.getenv(ENV_CS_NON_CS_REDIRECT_THRESHOLD, "0.75"))
        return v if 0.0 <= v <= 1.0 else 0.75
    except (TypeError, ValueError):
        return 0.75

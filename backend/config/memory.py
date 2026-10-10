"""config/memory.py — 记忆系统配置（L1/L2/L3）

短期记忆（L1）+ 会话记忆（L2）+ 长期记忆（L3）+ PostgreSQL 连接池。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 记忆模块开关（默认开启；设置 ENABLE_MEMORY=false 关闭）
# PostgreSQL 不可用时自动降级为内存模式，不影响核心功能
ENABLE_MEMORY = os.getenv("ENABLE_MEMORY", "true").lower() == "true"

# 历史感知检索
ENABLE_HISTORY_AWARE_RETRIEVAL = os.getenv("ENABLE_HISTORY_AWARE_RETRIEVAL", "true").lower() == "true"

# 短期记忆 (L1)
SHORT_TERM_MAX_MESSAGES = int(os.getenv("SHORT_TERM_MAX_MESSAGES", "20"))

# 对话历史 token 预算：L1 组装（含 L2 摘要 / L3 记忆注入）后整体按 token 裁剪，
# 弥补"按条数"截断对长消息失效的问题（0 = 关闭预算，仅条数控制）
HISTORY_TOKEN_BUDGET = int(os.getenv("HISTORY_TOKEN_BUDGET", "2048"))

# 会话记忆 (L2)
SESSION_MAX_MESSAGES = int(os.getenv("SESSION_MAX_MESSAGES", "50"))

# L2 增量摘要主触发线；默认复用现有会话历史 Token 预算。
CONTEXT_L2_SUMMARY_TRIGGER_TOKENS = int(os.getenv(
    "CONTEXT_L2_SUMMARY_TRIGGER_TOKENS", str(max(1, HISTORY_TOKEN_BUDGET))))

# L2 摘要滞后门（STOP E E2）：触发条件为条数制（SESSION_MAX_MESSAGES），
# 稳态每轮新增 2 条消息都会满足"水位线后有新增量"，若无最小增量门会导致
# 每轮一次摘要 LLM 调用（每轮重复 summary）。攒批到 ≥K 条增量才摘要。
CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES = int(
    os.getenv("CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES", "10"))

# L3 durable extraction jobs：worker 崩溃后 beat 以锁租期重新派发，普通模型
# 或存储故障有界重试；队列只传 job ID，源消息仍由 PostgreSQL 读取。
MEMORY_EXTRACTION_MAX_ATTEMPTS = int(os.getenv("MEMORY_EXTRACTION_MAX_ATTEMPTS", "3"))
MEMORY_EXTRACTION_STALE_SECONDS = int(os.getenv("MEMORY_EXTRACTION_STALE_SECONDS", "600"))
MEMORY_EXTRACTION_RECOVERY_BATCH_SIZE = int(
    os.getenv("MEMORY_EXTRACTION_RECOVERY_BATCH_SIZE", "100"))
MEMORY_EXTRACTION_DISPATCH_COOLDOWN_SECONDS = int(
    os.getenv("MEMORY_EXTRACTION_DISPATCH_COOLDOWN_SECONDS", "15"))

# 长期记忆 (L3)
ENABLE_LONG_TERM_MEMORY = os.getenv("ENABLE_LONG_TERM_MEMORY", "true").lower() == "true"
# PII 过滤器
L3_PII_FILTER_ENABLED = os.getenv("L3_PII_FILTER_ENABLED", "true").lower() == "true"
# 去重阈值
L3_DEDUP_COSINE_THRESHOLD = float(os.getenv("L3_DEDUP_COSINE_THRESHOLD", "0.85"))
L3_SUPERSEDE_THRESHOLD = float(os.getenv("L3_SUPERSEDE_THRESHOLD", "0.92"))

# ── 记忆 provenance（047 迁移，STOP B：Memory Production Closure）──
# origin 通道语义：explicit=用户显式写入（memory_store_tool）｜
# inferred=后台自动提取｜legacy=047 之前的历史记录（代码不再新写）。
MEMORY_ORIGIN_EXPLICIT = "explicit"
MEMORY_ORIGIN_INFERRED = "inferred"
MEMORY_ORIGIN_LEGACY = "legacy"
# explicit 通道默认可信度：由代码指定（不写死在业务深处）；inferred 用
# extractor 返回值，非法/缺失回退到 MEMORY_INFERRED_DEFAULT_CONFIDENCE
MEMORY_EXPLICIT_DEFAULT_CONFIDENCE = float(os.getenv("MEMORY_EXPLICIT_DEFAULT_CONFIDENCE", "0.98"))
MEMORY_INFERRED_DEFAULT_CONFIDENCE = float(os.getenv("MEMORY_INFERRED_DEFAULT_CONFIDENCE", "0.7"))
# 证据含不确定性措辞（"可能/考虑/看看"…）时的 confidence 上限：
# 防 assistant 复述把模糊意图强化成高置信"决定"（Case B4），纯规则非 LLM
MEMORY_HEDGED_CONFIDENCE_CAP = float(os.getenv("MEMORY_HEDGED_CONFIDENCE_CAP", "0.55"))

# ── L3 读取管线（048+，STOP D：Safe Injection + Relevance Gate）──
# 候选召回数（SQL eligibility 后的 top-N；沿用旧 top20 口径，不扩大召回）
MEMORY_RETRIEVAL_CANDIDATES = int(os.getenv("MEMORY_RETRIEVAL_CANDIDATES", "20"))
# 最终注入上限（0~K，0 条合法；不再强凑 top5）
MEMORY_MAX_INJECTED = int(os.getenv("MEMORY_MAX_INJECTED", "5"))
# 语义相关性硬门（cosine similarity，1.0-cosine_distance 口径，越高越相关）。
# 默认阈值 0.35 已由 Memory Golden 集评测锁定；变更前重跑
# backend/scripts/eval_memory_golden.py 并复核 backend/tests/memory/ 中的契约断言。
MEMORY_MIN_RELEVANCE_SCORE = float(os.getenv("MEMORY_MIN_RELEVANCE_SCORE", "0.35"))
# 全局响应偏好：memory_key 命中这些前缀的 active 记忆免 semantic gate
# （"回答用中文"与问题主题无关但任何轮次都适用）；白名单为确定性策略，
# 禁止 LLM 决定 global（§90 防 key 扩权）。普通主题型偏好不在白名单，
# 仍需过 semantic gate。
MEMORY_GLOBAL_KEY_PREFIXES = tuple(
    p.strip().lower()
    for p in os.getenv("MEMORY_GLOBAL_KEY_PREFIXES", "response.").split(",")
    if p.strip()
)
# 全局偏好最大注入数（与 semantic 命中共享 MEMORY_MAX_INJECTED 总上限）
MEMORY_MAX_GLOBAL_PREFERENCES = int(os.getenv("MEMORY_MAX_GLOBAL_PREFERENCES", "3"))

# ── 上下文预算管理（Context Budget Management，2026-09-22）──
# 统一管理 active context（发给模型的上下文）的 token 预算；原始 chat_messages 不受影响。
# 完整设计见 docs/architecture/ai-runtime.md#上下文预算与跨请求状态
# LLM_CONTEXT_LENGTH 复用 config/llm.py，不在此复制第二份窗口配置。
CONTEXT_BUDGET_ENABLED = os.getenv("CONTEXT_BUDGET_ENABLED", "true").lower() == "true"

# 输出预留 + 安全余量：input_budget = LLM_CONTEXT_LENGTH - 这两者
CONTEXT_OUTPUT_RESERVE_TOKENS = int(os.getenv("CONTEXT_OUTPUT_RESERVE_TOKENS", "768"))
CONTEXT_SAFETY_RESERVE_TOKENS = int(os.getenv("CONTEXT_SAFETY_RESERVE_TOKENS", "256"))

# L1 工具结果预算：单条工具结果超过 TOOL_INLINE_MAX_TOKENS 则降级为预览
TOOL_INLINE_MAX_TOKENS = int(os.getenv("TOOL_INLINE_MAX_TOKENS", "768"))
# 预览上限（按 token 截取，非字符）
TOOL_PREVIEW_MAX_TOKENS = int(os.getenv("TOOL_PREVIEW_MAX_TOKENS", "256"))

# L3 previous_outputs 总预算（注入下一 Skill prompt 的全部前置输出）
PREVIOUS_OUTPUTS_MAX_TOKENS = int(os.getenv("PREVIOUS_OUTPUTS_MAX_TOKENS", "1024"))

# L4 Context Collapse / L5 AutoCompact 触发阈值
# L5 是最后一道防线：只在 L1/L2/L3/L4 全部执行后仍达阈值才触发（有 LLM 成本）
CONTEXT_L4_TRIGGER_RATIO = float(os.getenv("CONTEXT_L4_TRIGGER_RATIO", "0.80"))
CONTEXT_L5_TRIGGER_RATIO = float(os.getenv("CONTEXT_L5_TRIGGER_RATIO", "0.90"))

# 滞回目标（2026-09-23 STOP D）：触发一次后压到目标比例以下的安全区，
# 而不是刚好低于触发线导致下一轮立即重新触发
CONTEXT_L4_TARGET_RATIO = float(os.getenv("CONTEXT_L4_TARGET_RATIO", "0.65"))

# L5 折叠时保留的最近对话轮数：与 L4 语义一致（最近 N 个完整 turn 保持原文），
# 直接复用 CONTEXT_L4_KEEP_RECENT_TURNS，不为配置而配置

# L5 AutoCompact（LLM 摘要，2026-09-22 Phase 3）
# 总开关（false = 完全禁用 L5，超预算时安全降级为确定性裁剪）
CONTEXT_L5_ENABLED = os.getenv("CONTEXT_L5_ENABLED", "true").lower() == "true"
# 触发后把 active context 压到的目标比例（input_budget 的 ~70%）
CONTEXT_L5_TARGET_RATIO = float(os.getenv("CONTEXT_L5_TARGET_RATIO", "0.70"))
# 摘要 LLM 调用超时（秒）：超时按摘要失败处理，安全回退到裁剪后上下文。
# 校准依据（Phase 5）：摘要模型切 non-thinking 后 P95 实测 ~5s，timeout=P95×2
# 取整 30s（§十二：正常摘要不误超时，provider 卡死又能及时 fallback）
CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS = float(os.getenv("CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS", "30"))
# 在线等待同轮摘要的时限（秒，STOP B 2026-10-01）：异步在线路径 await 摘要
# 的上限。校准依据：non-thinking 摘要 P95 ~5s（§十二）——在线等待取其量级，
# 不是 30s 的 provider 死等上限；超时即放弃本轮采用（摘要线程继续跑完落库，
# 下一轮生效），绝不把 30s 变成每次聊天的等待时间。
CONTEXT_L5_ONLINE_WAIT_SECONDS = float(os.getenv("CONTEXT_L5_ONLINE_WAIT_SECONDS", "8"))
# 摘要 LLM 模型：正式走模型角色控制面（context_compactor 角色，2026-09-22 Phase 5）。
# 解析链 = DB role binding（管理端「模型角色绑定」）→ config fallback（本常量）→
# provider adapter。此常量仅作 DB 未绑定时的代码默认值；运行时经
# model_roles.resolve_effective("context_compactor") 统一解析，业务代码禁止直读。
CONTEXT_L5_SUMMARY_MODEL = os.getenv("CONTEXT_L5_SUMMARY_MODEL", "")
# 摘要输出上限（tokens）：摘要任务只需短/准/结构化，超出即截断（防 thinking 泄漏膨胀）
CONTEXT_L5_SUMMARY_MAX_TOKENS = int(os.getenv("CONTEXT_L5_SUMMARY_MAX_TOKENS", "512"))
# 摘要温度：低温保证结构化稳定复现
CONTEXT_L5_SUMMARY_TEMPERATURE = float(os.getenv("CONTEXT_L5_SUMMARY_TEMPERATURE", "0.2"))
# 增量摘要最小新消息数：少于该条数不值得一次 LLM 调用（跳过本轮 L5）
CONTEXT_L5_MIN_DELTA_MESSAGES = int(os.getenv("CONTEXT_L5_MIN_DELTA_MESSAGES", "2"))
# 单次增量摘要最多带多少条新消息进 prompt（防 delta 巨大时 prompt 爆炸）
CONTEXT_L5_MAX_DELTA_MESSAGES = int(os.getenv("CONTEXT_L5_MAX_DELTA_MESSAGES", "200"))
# ProtectedFacts 最大条数（超出按发现顺序截断，保护摘要 prompt 体积）
CONTEXT_L5_MAX_PROTECTED_FACTS = int(os.getenv("CONTEXT_L5_MAX_PROTECTED_FACTS", "40"))

# L5 跨进程单飞 Redis 锁 TTL（秒）：必须 > 摘要超时（30s），锁过期自动释放
CONTEXT_L5_LOCK_TTL = int(os.getenv("CONTEXT_L5_LOCK_TTL", "60"))

# L4 折叠时保留的最近对话轮数（一组 user+assistant 算一轮），
# 折叠只作用于更早的普通 user/assistant 历史（SystemMessage/当前消息永不折叠）
CONTEXT_L4_KEEP_RECENT_TURNS = int(os.getenv("CONTEXT_L4_KEEP_RECENT_TURNS", "4"))

# ── Token 计数口径（2026-09-23 P0-1，模型感知计数）──
# estimated 计数（calibrated/fallback 策略）的安全系数：宁可高估不可低估
CONTEXT_TOKEN_ESTIMATION_MARGIN = float(os.getenv("CONTEXT_TOKEN_ESTIMATION_MARGIN", "1.10"))
# 多模态图片 part 的 token 估算值（各 provider 口径差异大，只防"计 0"）
CONTEXT_IMAGE_TOKEN_ESTIMATE = int(os.getenv("CONTEXT_IMAGE_TOKEN_ESTIMATE", "1024"))
# 每条消息的对话模板包裹开销（chat template / role 标记）
CONTEXT_MESSAGE_OVERHEAD_TOKENS = int(os.getenv("CONTEXT_MESSAGE_OVERHEAD_TOKENS", "4"))

# PostgreSQL 连接池
MEMORY_ASYNC_POOL_SIZE = int(os.getenv("MEMORY_ASYNC_POOL_SIZE", "20"))
MEMORY_ASYNC_MAX_OVERFLOW = int(os.getenv("MEMORY_ASYNC_MAX_OVERFLOW", "10"))

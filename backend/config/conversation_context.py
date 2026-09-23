"""会话级跨轮上下文（P2/D1）配置。

ConversationContext / FollowUpResolver 的阈值与开关集中在此，
业务代码禁止直接 os.getenv（项目 Code Rules）。
"""
import os

# 上下文存活时间（秒）：超时未更新的上下文视为过期丢弃。
# 主链每轮更新 updated_at，因此该 TTL 即「用户停止对话多久后不再续接」。
CONVERSATION_CONTEXT_TTL_SECONDS = int(
    os.getenv("CONVERSATION_CONTEXT_TTL_SECONDS", "1800")
)

# 单进程最大上下文条数（防泄漏式增长；超过按 LRU 淘汰）
CONVERSATION_CONTEXT_MAX_ENTRIES = int(
    os.getenv("CONVERSATION_CONTEXT_MAX_ENTRIES", "5000")
)

# ── Distributed backend（STOP G1，2026-09-24）──
# redis = shared hot state（多 worker 一致）；memory = 进程内（单测/本地）。
# 默认 redis：生效前置 = REDIS_ENABLED=true 且可连；不可用走 REQUIRE_SHARED
# 策略（见下），不出现不可观测的静默切换。
CONVERSATION_CONTEXT_BACKEND = os.getenv(
    "CONVERSATION_CONTEXT_BACKEND", "redis"
).strip().lower()

# true = 生产 fail-closed：Redis 不可用时 startup fail-fast、runtime 拒绝
# 读写（确定性 miss / 不落库），绝不悄悄退回进程内 memory 造成跨 worker
# 假一致；false = 开发兜底：降级进程内 memory（warning + degraded 可观测）。
CONVERSATION_CONTEXT_REQUIRE_SHARED = (
    os.getenv("CONVERSATION_CONTEXT_REQUIRE_SHARED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# 规则不可靠时是否启用轻量 LLM rewrite（P2.6）。
# 默认关闭：规则层已确定性覆盖高频 follow-up；开启后仅在
# follow_up detected 且规则无法可靠解决时调用一次 LLM（max_tokens≈200）。
FOLLOWUP_LLM_REWRITE_ENABLED = (
    os.getenv("FOLLOWUP_LLM_REWRITE_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# LLM rewrite 超时（秒）
FOLLOWUP_LLM_REWRITE_TIMEOUT_SECONDS = float(
    os.getenv("FOLLOWUP_LLM_REWRITE_TIMEOUT_SECONDS", "6")
)

# ContinuationResolver 开关（路由入口重构 2026-09-22）：
# 命中延续信号且会话有活跃任务时，直接回上一任务域（如旅游域跨轮改单）。
# 关闭即回滚为原路由行为（短指令交给粗分类/Gate 判定）。
CONTINUATION_RESOLVER_ENABLED = (
    os.getenv("CONTINUATION_RESOLVER_ENABLED", "true").strip().lower()
    in ("1", "true", "yes", "on")
)

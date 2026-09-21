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

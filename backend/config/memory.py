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

# 长期记忆 (L3)
ENABLE_LONG_TERM_MEMORY = os.getenv("ENABLE_LONG_TERM_MEMORY", "true").lower() == "true"
# PII 过滤器
L3_PII_FILTER_ENABLED = os.getenv("L3_PII_FILTER_ENABLED", "true").lower() == "true"
# 去重阈值
L3_DEDUP_COSINE_THRESHOLD = float(os.getenv("L3_DEDUP_COSINE_THRESHOLD", "0.85"))
L3_SUPERSEDE_THRESHOLD = float(os.getenv("L3_SUPERSEDE_THRESHOLD", "0.92"))

# ── 上下文预算管理（Context Budget Management，2026-09-22）──
# 统一管理 active context（发给模型的上下文）的 token 预算；原始 chat_messages 不受影响。
# 完整设计见 docs/2026-09-22-context-budget-management-实施规格.md
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

# L4 Context Collapse / L5 AutoCompact 触发阈值（基础版只预留，不实现）
CONTEXT_L4_TRIGGER_RATIO = float(os.getenv("CONTEXT_L4_TRIGGER_RATIO", "0.80"))
CONTEXT_L5_TRIGGER_RATIO = float(os.getenv("CONTEXT_L5_TRIGGER_RATIO", "0.90"))

# L4 折叠时保留的最近对话轮数（一组 user+assistant 算一轮），
# 折叠只作用于更早的普通 user/assistant 历史（SystemMessage/当前消息永不折叠）
CONTEXT_L4_KEEP_RECENT_TURNS = int(os.getenv("CONTEXT_L4_KEEP_RECENT_TURNS", "4"))

# PostgreSQL 连接池
MEMORY_ASYNC_POOL_SIZE = int(os.getenv("MEMORY_ASYNC_POOL_SIZE", "20"))
MEMORY_ASYNC_MAX_OVERFLOW = int(os.getenv("MEMORY_ASYNC_MAX_OVERFLOW", "10"))
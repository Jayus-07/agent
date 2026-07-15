"""config/memory.py — 记忆系统配置（L1/L2/L3）

短期记忆（L1）+ 会话记忆（L2）+ 长期记忆（L3）+ PostgreSQL 连接池。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 记忆模块开关（默认关闭，PostgreSQL 事件循环待修复后开启）
ENABLE_MEMORY = os.getenv("ENABLE_MEMORY", "false").lower() == "true"

# 历史感知检索
ENABLE_HISTORY_AWARE_RETRIEVAL = os.getenv("ENABLE_HISTORY_AWARE_RETRIEVAL", "true").lower() == "true"

# 短期记忆 (L1)
SHORT_TERM_MAX_MESSAGES = int(os.getenv("SHORT_TERM_MAX_MESSAGES", "20"))

# 会话记忆 (L2)
SESSION_MAX_MESSAGES = int(os.getenv("SESSION_MAX_MESSAGES", "50"))

# 长期记忆 (L3)
ENABLE_LONG_TERM_MEMORY = os.getenv("ENABLE_LONG_TERM_MEMORY", "true").lower() == "true"
# PII 过滤器
L3_PII_FILTER_ENABLED = os.getenv("L3_PII_FILTER_ENABLED", "true").lower() == "true"
# 去重阈值
L3_DEDUP_COSINE_THRESHOLD = float(os.getenv("L3_DEDUP_COSINE_THRESHOLD", "0.85"))
L3_SUPERSEDE_THRESHOLD = float(os.getenv("L3_SUPERSEDE_THRESHOLD", "0.92"))

# PostgreSQL 连接池
MEMORY_ASYNC_POOL_SIZE = int(os.getenv("MEMORY_ASYNC_POOL_SIZE", "20"))
MEMORY_ASYNC_MAX_OVERFLOW = int(os.getenv("MEMORY_ASYNC_MAX_OVERFLOW", "10"))
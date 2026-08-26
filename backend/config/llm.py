"""config/llm.py — LLM 配置

模型路径、API Key、超时、并发控制。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 模型路径
EMBEDDING_MODEL_PATH = os.getenv(
    "EMBEDDING_MODEL_PATH",
    "BAAI/bge-small-zh-v1.5"  # HuggingFace model name，自动走缓存
)
RERANKER_MODEL_PATH = os.getenv(
    "RERANKER_MODEL_PATH",
    "BAAI/bge-reranker-base"  # HuggingFace model name，自动走缓存
)

# 模型参数
LLM_MODEL = os.getenv("LLM_MODEL", "MiniMax-M3")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_CONTEXT_LENGTH = int(os.getenv("LLM_CONTEXT_LENGTH", "4096"))

# LLM 请求超时（秒）
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "30"))
RERANK_TIMEOUT = int(os.getenv("RERANK_TIMEOUT", "15"))

# 异步并发控制
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "4"))

# LLM 限流（PR-0.4 P0 限流 — 仅日志，不实际 429）
# 100 QPS 全局、1000 burst（允许短时尖峰）
LLM_RATE_LIMIT_QPS = float(os.getenv("LLM_RATE_LIMIT_QPS", "100"))
LLM_RATE_LIMIT_BURST = float(os.getenv("LLM_RATE_LIMIT_BURST", "1000"))

# DeepSeek 配置（用于多 LLM provider 切换）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

# MiniMax 配置（OpenAI 兼容协议）
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_API_BASE = os.getenv("MINIMAX_API_BASE", "https://api.minimax.chat/v1")

# ── P1-7: LLM 韧性（重试 + 熔断 fallback）────────────────────
# 瞬时错误（超时/连接/限流）的显式重试次数（0 = 不重试）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
# 重试退避基数（秒），第 n 次重试等待 base**n
LLM_RETRY_BACKOFF_BASE = float(os.getenv("LLM_RETRY_BACKOFF_BASE", "1.5"))
# 熔断开路/重试耗尽后的备用模型（须是 AVAILABLE_MODELS 中的模型名；
# 留空 = 不切备用模型，直接返回降级话术）
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "")
# 是否允许最终降级为固定话术（False 时把原始异常抛给调用方）
LLM_ALLOW_DEGRADED_ANSWER = os.getenv(
    "LLM_ALLOW_DEGRADED_ANSWER", "true"
).strip().lower() in ("1", "true", "yes")
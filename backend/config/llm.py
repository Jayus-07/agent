"""config/llm.py — LLM 配置

模型路径、API Key、超时、并发控制。

Runtime Mode: ENV_MODE = cloud | local
  - ENV_MODE=cloud   → Cloud Embedding + Cloud Reranker
  - ENV_MODE=local   → Local Embedding + Local Reranker
"""
import os

from dotenv import load_dotenv

load_dotenv()

# =====================================================
# Runtime Mode (P0 - 双模式控制)
# =====================================================

ENV_MODE = os.getenv("ENV_MODE", "cloud").strip().lower()
if ENV_MODE not in ("cloud", "local"):
    raw_value = os.getenv("ENV_MODE")
    raise ValueError(
        f"ENV_MODE 必须是 'cloud' 或 'local', 当前值为：'{raw_value}'"
    )

# =====================================================
# Ollama 本地推理开关（跟随 ENV_MODE）
# 仅 ENV_MODE=local 时启用本地 Ollama（评测生成/chunk 关键词/RAGAS local 后端）；
# ENV_MODE=cloud 时所有链路一律走云端 API，不再尝试连接本地 Ollama
# =====================================================
OLLAMA_ENABLED = ENV_MODE == "local"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
# 模型驻留时长（避免空闲卸载后重载权重的冷启动 TTFT 飙升）；"30m" / "-1"（常驻）
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")

# =====================================================
# Embedding Configuration (P0 - 动态配置)
# =====================================================

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "text-embedding-v3",  # Cloud 模式默认模型
)

EMBEDDING_API_BASE = os.getenv(
    "EMBEDDING_API_BASE",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)

EMBEDDING_API_KEY = os.getenv(
    "EMBEDDING_API_KEY",
    os.getenv("DASHSCOPE_API_KEY", ""),  # 回退到 DASHSCOPE_API_KEY
)

# Local Embedding 模型路径（仅在 ENV_MODE=local 时使用）
EMBEDDING_MODEL_PATH = os.getenv(
    "EMBEDDING_MODEL_PATH",
    "BAAI/bge-small-zh-v1.5"  # HuggingFace model name，自动走缓存
)
# =====================================================
# Rerank Configuration (P0 - 动态配置)
# =====================================================

RERANK_MODEL = os.getenv(
    "RERANK_MODEL",
    "qwen3-rerank",  # Cloud 模式默认模型
)

# Local Reranker 模型路径（仅在 ENV_MODE=local 时使用）
RERANKER_MODEL_PATH = os.getenv(
    "RERANKER_MODEL_PATH",
    "BAAI/bge-reranker-base"  # HuggingFace model name，自动走缓存
)

# =====================================================
# Inference Device (评测 / RAG 共享)
# =====================================================
# "auto" = 有 CUDA 则用 GPU，否则 CPU；也可显式指定 "cuda" / "cpu"
_EVAL_DEVICE_RAW = os.getenv("EVAL_DEVICE", "auto").strip().lower()
if _EVAL_DEVICE_RAW == "auto":
    try:
        import torch
        EVAL_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        EVAL_DEVICE = "cpu"
else:
    EVAL_DEVICE = _EVAL_DEVICE_RAW

# 模型参数
LLM_MODEL = os.getenv("LLM_MODEL", "MiniMax-M3")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_CONTEXT_LENGTH = int(os.getenv("LLM_CONTEXT_LENGTH", "4096"))

# LLM 请求超时（秒）
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "30"))
# Rerank API 超时：超时后 RerankCompressor 降级透传原文档（排序是增强组件），
# 收紧到 4s 避免检索段被慢 rerank 拖住尾延迟
RERANK_TIMEOUT = int(os.getenv("RERANK_TIMEOUT", "4"))

# ── TTFT 优化：定向节点输出上限与超时 ──
# Router LLM 兜底：输出只是路由 JSON，限制 max_tokens 缩短生成时间；
# 超时收紧到 6s（原 12s），超时落默认路由（plan + rag.search）
ROUTER_LLM_TIMEOUT = int(os.getenv("ROUTER_LLM_TIMEOUT", "6"))
ROUTER_LLM_MAX_TOKENS = int(os.getenv("ROUTER_LLM_MAX_TOKENS", "256"))
# Planner 输出是计划 JSON（通常 <500 token），限制上限防推理模型发散
PLANNER_LLM_MAX_TOKENS = int(os.getenv("PLANNER_LLM_MAX_TOKENS", "1024"))

# 异步并发控制
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "4"))

# LLM 限流（PR-0.4 P0 限流 — 仅日志，不实际 429）
# 100 QPS 全局、1000 burst（允许短时尖峰）
LLM_RATE_LIMIT_QPS = float(os.getenv("LLM_RATE_LIMIT_QPS", "100"))
LLM_RATE_LIMIT_BURST = float(os.getenv("LLM_RATE_LIMIT_BURST", "1000"))

# 限流执行模式：off=仅日志（默认）| wait=阻塞等待 | reject=抛异常拒绝
LLM_RATE_LIMIT_ENFORCE = os.getenv("LLM_RATE_LIMIT_ENFORCE", "off").strip().lower()

# ── P1 真 token 级流式（TTFT 优化）────────────────────────────
# true: 生成节点（RAG 链/Reporter）走 llm.stream，增量经 sink 推给 SSE；
# false: 回退旧行为（整图跑完 + 假打字机 emit_delta_events）
ENABLE_TOKEN_STREAMING = os.getenv(
    "ENABLE_TOKEN_STREAMING", "true"
).strip().lower() in ("1", "true", "yes")

# 流式请求附带 stream_options.include_usage，让尾 chunk 携带 token 用量
# （否则流式路径的 token 看板只能走 fallback 记 unavailable）。
# 仅加给已确认支持的 provider：DeepSeek / Qwen-DashScope 官方文档明确支持；
# MiniMax 兼容性未验证，暂不加（设 LLM_STREAM_USAGE=false 可整体关闭）
LLM_STREAM_USAGE = os.getenv(
    "LLM_STREAM_USAGE", "true"
).strip().lower() in ("1", "true", "yes")

# =====================================================
# Token Usage Tracking (P0 - 审计日志)
# =====================================================
TOKEN_USAGE_LOG_PATH = os.getenv(
    "TOKEN_USAGE_LOG_PATH",
    "data/token_usage.jsonl",  # 相对路径，从项目根目录计算
)

# Evaluation Dataset Path (P0 - 动态配置)
EVAL_DATASET_PATH = os.getenv(
    "EVAL_DATASET_PATH",
    "",  # 空字符串表示使用默认路径 backend/evaluation/datasets
)

# DeepSeek 配置（用于多 LLM provider 切换）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

# MiniMax 配置（OpenAI 兼容协议）
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_API_BASE = os.getenv("MINIMAX_API_BASE", "https://api.minimax.chat/v1")

# Qwen 在线配置（阿里云百炼 DashScope OpenAI 兼容端点）
# 注意：与 Reranker 用的 DASHSCOPE_API_KEY 独立，问答模型单独用 QWEN_API_KEY
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_API_BASE = os.getenv(
    "QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# ── P1-7: LLM 韧性（重试 + 熔断 fallback）────────────────────
# 瞬时错误（超时/连接/限流）的显式重试次数（0 = 不重试）
# 交互链路 TTFT 考量：默认收紧为 1 次（批处理/评测脚本可 env 调回 2）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))
# 重试退避基数（秒），第 n 次重试等待 base**n；1.5 时 2 次重试累计等 3.75s，
# 交互场景改 0.5 把尾延迟压到 0.75s
LLM_RETRY_BACKOFF_BASE = float(os.getenv("LLM_RETRY_BACKOFF_BASE", "0.5"))
# 熔断开路/重试耗尽后的备用模型（须是 AVAILABLE_MODELS 中的模型名；
# 留空 = 不切备用模型，直接返回降级话术）
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "")
# 是否允许最终降级为固定话术（False 时把原始异常抛给调用方）
LLM_ALLOW_DEGRADED_ANSWER = os.getenv(
    "LLM_ALLOW_DEGRADED_ANSWER", "true"
).strip().lower() in ("1", "true", "yes")
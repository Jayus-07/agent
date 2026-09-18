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
# 嵌入 / 重排独立开关（P0 - 与 ENV_MODE 解耦）
# =====================================================
# 空 = 跟随 ENV_MODE（向后兼容，行为与历史版本一致）。
# 显式设置后可与 LLM 链路混搭，例如本地开发时：
#   ENV_MODE=cloud（LLM 走 vLLM/在线 API）
#   EMBEDDING_PROVIDER=local + RERANK_PROVIDER=local（嵌入/重排留本地，索引在本地）
# ⚠️ 嵌入模型与向量索引强绑定：切换 EMBEDDING_PROVIDER / EMBEDDING_MODEL 后
#    必须全量重建向量索引（语义空间不同，维度相同也不兼容）。
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "").strip().lower() or ENV_MODE
RERANK_PROVIDER = os.getenv("RERANK_PROVIDER", "").strip().lower() or ENV_MODE
if EMBEDDING_PROVIDER not in ("local", "cloud"):
    raise ValueError(
        f"EMBEDDING_PROVIDER 必须是 'cloud' 或 'local'（留空跟随 ENV_MODE），"
        f"当前值为：'{os.getenv('EMBEDDING_PROVIDER')}'"
    )
if RERANK_PROVIDER not in ("local", "cloud"):
    raise ValueError(
        f"RERANK_PROVIDER 必须是 'cloud' 或 'local'（留空跟随 ENV_MODE），"
        f"当前值为：'{os.getenv('RERANK_PROVIDER')}'"
    )

# =====================================================
# Ollama 本地推理开关（跟随 ENV_MODE）
# 仅 ENV_MODE=local 时启用本地 Ollama（评测生成/chunk 关键词/RAGAS local 后端）；
# ENV_MODE=cloud 时所有链路一律走云端 API，不再尝试连接本地 Ollama
# =====================================================
# Ollama 本地推理开关
# 留空 = 跟随 ENV_MODE（ENV_MODE=local 启用，向后兼容）；
# 显式 1/0 可覆盖 —— 混搭场景：ENV_MODE=cloud（LLM 走 vLLM/在线 API）时
# 仍可用本机 Ollama 跑 chunk 关键词/评测生成/文档元数据抽取等免费小任务
_OLLAMA_ENABLED_RAW = os.getenv("OLLAMA_ENABLED", "").strip().lower()
if _OLLAMA_ENABLED_RAW in ("1", "true", "yes"):
    OLLAMA_ENABLED = True
elif _OLLAMA_ENABLED_RAW in ("0", "false", "no"):
    OLLAMA_ENABLED = False
else:
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

# 嵌入批量大小（每次 API 请求携带的文本条数）
# DashScope text-embedding-v3 上限 10；SiliconFlow / TEI 可调到 32+ 提速。
# 换嵌入供应商时同步调整此值。
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "10"))
# 云端 embedding 的单次 HTTP 请求上限。必须小于客服专家总超时，
# 使底层 I/O 可自行结束，避免外层线程超时后留下孤儿请求。
EMBEDDING_REQUEST_TIMEOUT = float(
    os.getenv("EMBEDDING_REQUEST_TIMEOUT", "20")
)
# =====================================================
# Rerank Configuration (P0 - 动态配置)
# =====================================================

RERANK_MODEL = os.getenv(
    "RERANK_MODEL",
    "qwen3-rerank",  # Cloud 模式默认模型
)

# Rerank API 协议格式：
#   dashscope → 阿里云百炼（endpoint = base + /services/rerank/text-rerank/text-rerank）
#   jina      → Jina 兼容格式（SiliconFlow 等，endpoint = base + /rerank）
RERANK_API_FORMAT = os.getenv("RERANK_API_FORMAT", "dashscope").strip().lower()
# Rerank 服务 base URL（不含路径）。默认 DashScope 原生 API；
# SiliconFlow 示例: https://api.siliconflow.cn/v1
RERANK_BASE_URL = os.getenv(
    "RERANK_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"
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
#
# ⚠️ 延迟解析：模块导入期不做 torch 探测 —— torch import 本身 ~6s，
#   而 backend.config 处在几乎所有模块的导入链上，每次进程启动/每测试
#   worker 都白付这笔钱（-X importtime 实测：单模块导入 10.9s 里 torch 占 6.3s）。
#   torch 只在真正要加载模型（如 embedding_singleton._get_local_embedding）
#   时才经 resolve_eval_device() 引入。
_EVAL_DEVICE_RAW = os.getenv("EVAL_DEVICE", "auto").strip().lower()
_EVAL_DEVICE_CACHE: str | None = None


def resolve_eval_device() -> str:
    """解析推理设备；auto 模式首次调用时才 import torch 探测 CUDA。"""
    global _EVAL_DEVICE_CACHE
    if _EVAL_DEVICE_CACHE is None:
        if _EVAL_DEVICE_RAW == "auto":
            try:
                import torch
                _EVAL_DEVICE_CACHE = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                _EVAL_DEVICE_CACHE = "cpu"
        else:
            _EVAL_DEVICE_CACHE = _EVAL_DEVICE_RAW
    return _EVAL_DEVICE_CACHE


def __getattr__(name: str):
    # PEP 562：保持 `from backend.config.llm import EVAL_DEVICE` 兼容，
    # 但解析推迟到首次属性访问。
    if name == "EVAL_DEVICE":
        return resolve_eval_device()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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

# ── FC 工具选择（tool RAG / 动态工具暴露）────────────────────
# true: direct 模式门控走 function calling 选工具+填参（tool_selector 节点，
#       路由缩候选 → 模型在候选内选择）；false 一键回退旧行为
#       （candidates[0] 直取 + question 透传，零 LLM）
ENABLE_FC_TOOL_SELECTION = os.getenv(
    "ENABLE_FC_TOOL_SELECTION", "true"
).strip().lower() in ("1", "true", "yes")
# tool_selector 的 LLM 调用约束：选择+填参输出很小，收紧超时与上限，
# 失败/超时直接回退直通路径（等价旧行为），不拖尾延迟。
# max_tokens 不宜低于 512：qwen3 系会先写一段普通 CoT 再发 tool_calls，
# 截断会吞掉 tool_calls（评测实测 256 时 no_match 率飙升）
TOOL_SELECTOR_LLM_TIMEOUT = int(os.getenv("TOOL_SELECTOR_LLM_TIMEOUT", "8"))
TOOL_SELECTOR_LLM_MAX_TOKENS = int(os.getenv("TOOL_SELECTOR_LLM_MAX_TOKENS", "512"))
# 选择+填参是小任务，可指定低延迟模型（推荐已注册的 deepseek-v4-flash）；
# 空 = 跟随全局默认模型。未注册/构建失败自动回退全局模型
TOOL_SELECTOR_MODEL = os.getenv("TOOL_SELECTOR_MODEL", "").strip()
# 灰度放量（照 cs_prefilter 模式）：白名单 session 优先，其余按
# md5(session_id) 稳定哈希百分比。默认 100 = 全量；0 = 全部直通（回旧行为）
FC_TOOL_SELECTION_ROLLOUT_PERCENT = int(
    os.getenv("FC_TOOL_SELECTION_ROLLOUT_PERCENT", "100"))
FC_TOOL_SELECTION_ALLOWLIST = [
    s.strip() for s in os.getenv("FC_TOOL_SELECTION_ALLOWLIST", "").split(",")
    if s.strip()
]
# 门控阈值（评测校准后可调）：fast set 高置信直通阈值 / FC 候选截断上限
TOOL_SELECTOR_FAST_PATH_SCORE = float(
    os.getenv("TOOL_SELECTOR_FAST_PATH_SCORE", "0.85"))
TOOL_SELECTOR_MAX_CANDIDATES = int(
    os.getenv("TOOL_SELECTOR_MAX_CANDIDATES", "3"))

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

# Qwen Token Plan（模型包/折扣计划，sk-sp- key）独立配置
# 与标准 API 的差异：端点不同；不支持 rerank 端点（rerank 走 DASHSCOPE_API_KEY）；
# 余额无查询接口。注册名带 @tp 后缀（如 qwen3.7-plus@tp），构建时剥掉再发 API。
QWEN_TP_API_KEY = os.getenv("QWEN_TP_API_KEY", "")
QWEN_TP_API_BASE = os.getenv(
    "QWEN_TP_API_BASE",
    "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)

# vLLM 自托管配置（OpenAI 兼容协议）
# 服务器部署：vLLM 起 LLM 服务（:8000/v1），TEI 起嵌入/重排服务（:8080）
# 本地开发经 SSH 隧道连接；API Key 来自 deploy.sh 生成的 VLLM_API_KEY
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "")
VLLM_API_BASE = os.getenv("VLLM_API_BASE", "http://localhost:8000/v1")

# ── P1-7: LLM 韧性（重试 + 熔断 fallback）────────────────────
# 瞬时错误（超时/连接/限流）的显式重试次数（0 = 不重试）
# 交互链路 TTFT 考量：默认收紧为 1 次（批处理/评测脚本可 env 调回 2）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))
# 重试退避基数（秒），第 n 次重试等待 base**n；1.5 时 2 次重试累计等 3.75s，
# 交互场景改 0.5 把尾延迟压到 0.75s
LLM_RETRY_BACKOFF_BASE = float(os.getenv("LLM_RETRY_BACKOFF_BASE", "0.5"))
# 熔断开路/重试耗尽后的备用模型（须是 AVAILABLE_MODELS 中的模型名；
# 留空 = 不切备用模型，直接按 LLM_ALLOW_DEGRADED_ANSWER 处理）
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "")
# 是否允许最终降级为固定话术（默认 False = fail-fast，把原始异常抛给调用方）。
#
# 2026-09-15 默认值由 true 改为 false（线上实测教训）：
#   LLM 不可用时返回的"AI 服务暂时不可用…"话术会**冒充模型输出**流向下游，
#   而下游是结构化消费者——SQL 生成让它当 SQL 解析（sqlglot 把中文解析成
#   Alias 节点，校验器报出"只允许 SELECT 查询，检测到 Alias"）、Planner 让
#   它当 DAG JSON 解析（PLAN_JSON_INVALID）……真实原因（401/超时/熔断）被
#   埋在 N 层语义错误之下，排查成本极高。
#   fail-fast 后：结构化消费者拿到真实异常（走各自的 error 分支，错误分类
#   正确）；用户可见的最终回答由 Reporter 的 except → _fallback_summary 兜底，
#   体验不降级。
# 仅在确有"必须拿到字符串、调用方自行判断"的场景，用 env 显式开启 true。
LLM_ALLOW_DEGRADED_ANSWER = os.getenv(
    "LLM_ALLOW_DEGRADED_ANSWER", "false"
).strip().lower() in ("1", "true", "yes")

# ── WP4：请求级预算（金额配额另行配置）────────────────────────────
# off      = 不计数、不阻断（默认，兼容现有生产行为）
# observe  = 计数并记录超限，不阻断（上线前校准）
# enforce  = 每次模型调用前硬阻断
LLM_BUDGET_MODE = os.getenv("LLM_BUDGET_MODE", "off").strip().lower()
if LLM_BUDGET_MODE not in ("off", "observe", "enforce"):
    LLM_BUDGET_MODE = "off"
LLM_REQUEST_MAX_CALLS = int(os.getenv("LLM_REQUEST_MAX_CALLS", "8"))
LLM_REQUEST_MAX_TOKENS = int(os.getenv("LLM_REQUEST_MAX_TOKENS", "32000"))
LLM_REQUEST_MAX_RETRIES = int(os.getenv("LLM_REQUEST_MAX_RETRIES", "2"))
LLM_REQUEST_MAX_FALLBACKS = int(os.getenv("LLM_REQUEST_MAX_FALLBACKS", "1"))

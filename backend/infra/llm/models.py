"""
models.py — Provider 注册表 + 可用模型清单

新增 Provider 只需:
  1. 在 PROVIDERS 注册
  2. 在 AVAILABLE_MODELS 添加模型条目
  3. 在 providers/ 目录实现 build_xxx() 和 get_xxx_balance() 函数
"""

from __future__ import annotations

from typing import Any

# 注：不要在这里顶层 import langchain_ollama —— 实测它连带 torch/transformers
# （~8s），而本模块处在 backend.infra.llm 的高频导入链上。registry 的 class
# 字段无任何消费方（工厂走 build_xxx()），置 None 即可。
# Provider 注册表：provider_name → {class, default_model, needs_api_key}
PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "class": None,  # 懒加载（langchain_ollama.ChatOllama，见 providers/ollama.py）
        "default_model": "qwen2.5:3b",
        "needs_api_key": False,
    },
    "deepseek": {
        "class": None,  # 懒加载（兼容 OpenAI 协议的 ChatOpenAI）
        "default_model": "deepseek-v4-flash",
        "needs_api_key": True,
    },
    "minimax": {
        "class": None,  # OpenAI 兼容协议
        "default_model": "MiniMax-M3",
        "needs_api_key": True,
    },
    "qwen": {
        "class": None,  # DashScope OpenAI 兼容协议
        "default_model": "qwen3.7-plus",
        "needs_api_key": True,
    },
    "qwen_tp": {
        "class": None,  # Qwen Token Plan（模型包端点，注册名带 @tp 后缀）
        "default_model": "qwen3.7-plus@tp",
        "needs_api_key": True,
    },
    "vllm": {
        "class": None,  # 自托管 vLLM（OpenAI 兼容协议），见 providers/vllm.py
        "default_model": "Qwen/Qwen3-32B-AWQ",
        "needs_api_key": True,
    },
    "siliconflow": {
        "class": None,  # 硅基流动（OpenAI 兼容协议），见 providers/siliconflow.py
        "default_model": "Qwen/Qwen3-8B",
        "needs_api_key": True,
    },
}


# 可用模型清单（前端展示 + set_current 校验用 + cost 估算）
# input_price_per_1m / output_price_per_1m: USD per 1M tokens（cost 估算用）
AVAILABLE_MODELS = [
    {
        "provider": "qwen",
        "name": "qwen3.7-plus",
        "display": "Qwen 3.7 Plus - 在线",
        "description": "阿里云百炼 Qwen3.7-Plus，OpenAI 兼容协议，需要 API Key",
        "input_price_per_1m": 0.4,
        "output_price_per_1m": 1.2,
    },
    {
        "provider": "qwen_tp",
        "name": "qwen3.7-plus@tp",
        "display": "Qwen 3.7 Plus - Token Plan",
        "description": "阿里云百炼模型包端点（sk-sp- Key），需配置 QWEN_TP_API_KEY",
        "input_price_per_1m": 0.0,   # 模型包按购买量计费，不走 token 计价
        "output_price_per_1m": 0.0,
    },
    {
        "provider": "ollama",
        "name": "qwen2.5:3b",
        "display": "Qwen 2.5 (3B) - 本地",
        "description": "本地 Ollama，免费，无需 API Key",
        "input_price_per_1m": 0.0,
        "output_price_per_1m": 0.0,
    },
    {
        "provider": "deepseek",
        "name": "deepseek-v4-flash",
        "display": "DeepSeek V4-Flash - 云端",
        "description": "DeepSeek V4-Flash，高并发低延迟，需要 API Key",
        "input_price_per_1m": 0.14,
        "output_price_per_1m": 0.28,
    },
    {
        "provider": "minimax",
        "name": "MiniMax-M3",
        "display": "MiniMax M3 - 云端",
        "description": "MiniMax-M3，OpenAI 兼容协议，需要 API Key",
        "input_price_per_1m": 3.0,
        "output_price_per_1m": 15.0,
    },
    {
        "provider": "vllm",
        "name": "Qwen/Qwen3-32B-AWQ",
        "display": "Qwen3 32B (AWQ) - 自托管",
        "description": "自托管 vLLM（OpenAI 兼容），需配置 VLLM_API_BASE/VLLM_API_KEY",
        "input_price_per_1m": 0.0,
        "output_price_per_1m": 0.0,
    },
    {
        # 硅基流动价格未核（0 会使成本估算低估），接入后按账单回填
        "provider": "siliconflow",
        "name": "Qwen/Qwen3-8B",
        "display": "Qwen3 8B - 硅基流动",
        "description": "硅基流动 Qwen3-8B，OpenAI 兼容协议，需配置 SILICONFLOW_API_KEY",
        "input_price_per_1m": 0.0,
        "output_price_per_1m": 0.0,
    },
]


def get_model_pricing(model_name: str) -> tuple[float, float]:
    """返回 (input_price_per_1m, output_price_per_1m) USD。未匹配返回 (0, 0)。"""
    for m in AVAILABLE_MODELS:
        if m["name"] == model_name:
            return (
                float(m.get("input_price_per_1m", 0.0)),
                float(m.get("output_price_per_1m", 0.0)),
            )
    return 0.0, 0.0


# provider → 启用该 provider 模型所需的环境变量名（None = 无需 key）。
# provider→key 的单一事实来源：startup 校验、专用模型配置校验共用
PROVIDER_API_KEY_ENV = {
    "ollama": None,
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "qwen_tp": "QWEN_TP_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "vllm": "VLLM_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
}


def is_registered_model(model_name: str) -> bool:
    """模型名是否在 AVAILABLE_MODELS 注册。"""
    return any(m["name"] == model_name for m in AVAILABLE_MODELS)


def get_provider_api_key_env(model_name: str) -> str | None:
    """模型名 → 启用所需 API key 的环境变量名。

    未注册的模型返回 None（校验方应先过 is_registered_model）；
    ollama 等本地 provider 返回 None（无需 key）。
    """
    for m in AVAILABLE_MODELS:
        if m["name"] == model_name:
            return PROVIDER_API_KEY_ENV.get(m["provider"])
    return None


def compute_cost_usd(model_name: str,
                     prompt_tokens: int, completion_tokens: int) -> float:
    """按 model pricing 表估算单次调用 cost (USD)。"""
    in_p, out_p = get_model_pricing(model_name)
    return round(
        (prompt_tokens / 1_000_000) * in_p +
        (completion_tokens / 1_000_000) * out_p,
        6,
    )


# =====================================================
# Embedding / Reranker 定价（DashScope 官方 CNY → USD 换算）
# =====================================================
# 汇率：1 CNY ≈ 0.138 USD（2026-09 近似值，可按需调整）
_CNY_TO_USD = 0.138

# DashScope Embedding/Reranker 定价表（USD per 1M tokens）
# 来源：阿里云百炼官方定价（CNY/M tokens）× 汇率换算
# - qwen3-rerank: 0.5 元/M → 0.069 USD/M
# - text-embedding-v3: 0.125 元/M → 0.01725 USD/M
# - text-embedding-v4: 0.125 元/M → 0.01725 USD/M
# - qwen-vl-embedding text: 0.7 元/M → 0.0966 USD/M
# - qwen-vl-embedding image: 1.8 元/M → 0.2484 USD/M
EMBEDDING_RERANK_PRICING: dict[str, dict] = {
    "qwen3-rerank": {
        "component": "rerank",
        "input_per_1m_usd": round(0.5 * _CNY_TO_USD, 6),   # 0.069
    },
    "text-embedding-v3": {
        "component": "embedding",
        "input_per_1m_usd": round(0.125 * _CNY_TO_USD, 6), # 0.01725
    },
    "text-embedding-v4": {
        "component": "embedding",
        "input_per_1m_usd": round(0.125 * _CNY_TO_USD, 6), # 0.01725
    },
    "qwen-vl-embedding": {
        "component": "embedding",
        "input_per_1m_usd": round(0.7 * _CNY_TO_USD, 6),   # 0.0966 (text)
        "image_per_1m_usd": round(1.8 * _CNY_TO_USD, 6),   # 0.2484 (image)
    },
}


def compute_embedding_cost(model_name: str, total_tokens: int) -> float:
    """按 Embedding/Reranker 定价表估算单次调用 cost (USD)。

    Embedding/Reranker 只有输入 token，无输出 token。
    未匹配的模型返回 0.0（Local 模式无 API 费用）。
    """
    pricing = EMBEDDING_RERANK_PRICING.get(model_name)
    if not pricing:
        return 0.0
    price_per_1m = pricing.get("input_per_1m_usd", 0.0)
    return round((total_tokens / 1_000_000) * price_per_1m, 6)

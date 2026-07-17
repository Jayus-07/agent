"""
models.py — Provider 注册表 + 可用模型清单

新增 Provider 只需:
  1. 在 PROVIDERS 注册
  2. 在 AVAILABLE_MODELS 添加模型条目
  3. 在 providers/ 目录实现 build_xxx() 和 get_xxx_balance() 函数
"""

from langchain_ollama import ChatOllama


# Provider 注册表：provider_name → {class, default_model, needs_api_key}
PROVIDERS = {
    "ollama": {
        "class": ChatOllama,
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
}


# 可用模型清单（前端展示 + set_current 校验用 + cost 估算）
# input_price_per_1m / output_price_per_1m: USD per 1M tokens（cost 估算用）
AVAILABLE_MODELS = [
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


def compute_cost_usd(model_name: str,
                     prompt_tokens: int, completion_tokens: int) -> float:
    """按 model pricing 表估算单次调用 cost (USD)。"""
    in_p, out_p = get_model_pricing(model_name)
    return round(
        (prompt_tokens / 1_000_000) * in_p +
        (completion_tokens / 1_000_000) * out_p,
        6,
    )

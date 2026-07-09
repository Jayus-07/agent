"""
models.py — Provider 注册表 + 可用模型清单

新增 Provider 只需:
  1. 在 PROVIDERS 注册
  2. 在 AVAILABLE_MODELS 添加模型条目
  3. 在 providers/ 目录实现 _build() 和 _get_balance() 函数
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
        "default_model": "deepseek-chat",
        "needs_api_key": True,
    },
}


# 可用模型清单（前端展示 + set_current 校验用）
AVAILABLE_MODELS = [
    {
        "provider": "ollama",
        "name": "qwen2.5:3b",
        "display": "Qwen 2.5 (3B) - 本地",
        "description": "本地 Ollama，免费，无需 API Key",
    },
    {
        "provider": "ollama",
        "name": "qwen2.5:4b",
        "display": "Qwen 2.5 (4B) - 本地",
        "description": "本地 Ollama，免费",
    },
    {
        "provider": "deepseek",
        "name": "deepseek-chat",
        "display": "DeepSeek Chat - 云端",
        "description": "DeepSeek-V3，需要 API Key（.env 中配置 DEEPSEEK_API_KEY）",
    },
    {
        "provider": "deepseek",
        "name": "deepseek-reasoner",
        "display": "DeepSeek Reasoner - 云端",
        "description": "DeepSeek-R1 推理模型，需要 API Key",
    },
]

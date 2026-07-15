"""
minimax.py — MiniMax Provider（Anthropic Messages API，官方推荐）

提供:
  - build_minimax(): 构建 ChatAnthropic 实例（MiniMax Anthropic 兼容端点）
  - get_minimax_balance(): MiniMax 余额查询
"""

from config import (
    LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT,
    MINIMAX_API_KEY, MINIMAX_API_BASE,
)
from utils.logger import logger


def build_minimax(model_name: str) -> object:
    """构建 MiniMax 模型实例（Anthropic Messages API，官方推荐路径）

    MiniMax 文档推荐使用 Anthropic 兼容 API:
      - 支持 thinking: {"type": "disabled"} 关闭强制思考
      - 支持 interleaved thinking 高级特性
      - Chat Completions 仅作为 OpenAI SDK 用户迁移备选
    """
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as e:
        raise ImportError(
            "minimax provider 需要 langchain_anthropic 包，请 pip install langchain-anthropic"
        ) from e

    # MiniMax Anthropic 端点
    return ChatAnthropic(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        timeout=LLM_REQUEST_TIMEOUT,
        anthropic_api_key=MINIMAX_API_KEY,
        anthropic_api_url="https://api.minimaxi.com/anthropic",
        default_headers={"x-api-key": MINIMAX_API_KEY},
    )


def get_minimax_balance() -> dict:
    """MiniMax 余额（官网查询，此处返回固定值）"""
    return {
        "ok": True,
        "provider": "minimax",
        "balance": "—",
        "currency": "CNY",
        "note": "MiniMax 余额请前往官网查询",
    }

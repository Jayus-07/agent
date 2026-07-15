"""
minimax.py — MiniMax Provider（云端，兼容 OpenAI 协议）

提供:
  - build_minimax(): 构建 ChatOpenAI 实例（用 MiniMax API base）
  - get_minimax_balance(): MiniMax 余额查询（返回固定值，MiniMax 不提供余额 API）
"""

from config import (
    LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT,
    MINIMAX_API_KEY, MINIMAX_API_BASE,
)
from utils.logger import logger


def build_minimax(model_name: str) -> object:
    """构建 MiniMax 模型实例（通过 OpenAI 兼容协议）"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "minimax provider 需要 langchain_openai 包，请 pip install langchain-openai"
        ) from e

    return ChatOpenAI(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
        api_key=MINIMAX_API_KEY,
        base_url=MINIMAX_API_BASE,
        model_kwargs={"thinking": {"type": "disabled"}},  # 关闭 MiniMax-M3 强制思考
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

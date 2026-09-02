"""
qwen.py — Qwen Provider（阿里云百炼在线模型，OpenAI 兼容协议）

提供:
  - build_qwen(): 构建 ChatOpenAI 实例（DashScope OpenAI 兼容端点）
  - get_qwen_balance(): Qwen 余额查询（无公开 API，引导官网查询）
"""

from backend.config import (
    LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT,
    QWEN_API_KEY, QWEN_API_BASE,
)
from backend.shared.logger import logger


def build_qwen(model_name: str) -> object:
    """构建 Qwen 在线模型实例（通过 DashScope OpenAI 兼容协议）"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "qwen provider 需要 langchain_openai 包，请 pip install langchain-openai"
        ) from e

    return ChatOpenAI(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
        api_key=QWEN_API_KEY,
        base_url=QWEN_API_BASE,
    )


def get_qwen_balance() -> dict:
    """Qwen 余额（无公开余额 API，返回固定值引导官网查询）"""
    return {
        "ok": True,
        "provider": "qwen",
        "balance": "—",
        "currency": "CNY",
        "note": "Qwen 余额请前往阿里云百炼控制台查询",
    }

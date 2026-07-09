"""
ollama.py — Ollama Provider（本地部署）

提供:
  - build_ollama(): 构建 ChatOllama 实例
  - get_ollama_balance(): 返回本地免费状态
"""

from langchain_ollama import ChatOllama

from config import LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT
from utils.logger import logger


def build_ollama(model_name: str) -> ChatOllama:
    """构建 Ollama 模型实例"""
    return ChatOllama(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        num_ctx=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
    )


def get_ollama_balance() -> dict:
    """Ollama 本地部署，不消耗云端余额"""
    logger.debug("[Ollama] 余额查询（本地免费）")
    return {
        "ok": True,
        "provider": "ollama",
        "balance": "∞",
        "currency": "本地",
        "note": "Ollama 本地部署，不消耗云端余额",
    }

"""
ollama.py — Ollama Provider（本地部署）

提供:
  - build_ollama(): 构建 ChatOllama 实例
  - get_ollama_balance(): 返回本地免费状态
"""
import os
from typing import TYPE_CHECKING

from backend.config import (
    LLM_CONTEXT_LENGTH,
    LLM_REQUEST_TIMEOUT,
    LLM_TEMPERATURE,
    OLLAMA_BASE_URL,
    OLLAMA_KEEP_ALIVE,
)
from backend.shared.logger import logger

if TYPE_CHECKING:
    from langchain_ollama import ChatOllama


def build_ollama(model_name: str) -> "ChatOllama":
    """构建 Ollama 模型实例。

    base_url 优先级：环境变量 OLLAMA_BASE_URL > backend/config/llm.py 默认值。
    keep_alive 控制模型驻留时长，避免空闲卸载后重载权重的冷启动 TTFT 飙升；
    同时 Ollama（llama.cpp）对相同 prompt 前缀自动复用 KV Cache（prefix caching），
    多轮同会话请求 TTFT 进一步下降。
    """
    # 延迟导入：langchain_ollama 顶层导入连带 torch/transformers（实测 ~8s），
    # 而本模块经 factory 被 backend.infra.llm 的高频导入链引用。
    from langchain_ollama import ChatOllama

    base_url = os.getenv("OLLAMA_BASE_URL") or OLLAMA_BASE_URL
    return ChatOllama(
        model=model_name,
        base_url=base_url,
        temperature=LLM_TEMPERATURE,
        num_ctx=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
        keep_alive=OLLAMA_KEEP_ALIVE,
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

# llm_factory.py
"""
LLM 和 Embedding 模型工厂
统一管理模型实例化，支持单例模式
"""

from langchain_ollama import ChatOllama

from config import LLM_MODEL, LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT
from utils.logger import logger

# =====================================================
# LLM 初始化
# =====================================================

logger.info(f"正在初始化 LLM: {LLM_MODEL}")
try:
    llm = ChatOllama(
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
        num_ctx=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
    )
    logger.info(f"LLM init OK: {LLM_MODEL} (temperature={LLM_TEMPERATURE}, context={LLM_CONTEXT_LENGTH}, timeout={LLM_REQUEST_TIMEOUT}s)")
except Exception as e:
    logger.error(f"❌ LLM 初始化失败: {e}")
    raise


def get_llm():
    """返回模块级 LLM 单例。用于需要惰性获取 LLM 的场景（如 evaluation judge）。"""
    return llm


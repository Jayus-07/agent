# llm_factory.py
"""
LLM 和 Embedding 模型工厂
统一管理模型实例化，支持单例模式
"""

from langchain_ollama import ChatOllama
from sentence_transformers import SentenceTransformer

from config import LLM_MODEL, LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, EMBEDDING_MODEL_PATH, LLM_REQUEST_TIMEOUT
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
        request_timeout=LLM_REQUEST_TIMEOUT  # 添加超时保护
    )
    logger.info(f"✅ LLM 初始化成功: {LLM_MODEL} (temperature={LLM_TEMPERATURE}, context={LLM_CONTEXT_LENGTH}, timeout={LLM_REQUEST_TIMEOUT}s)")
except Exception as e:
    logger.error(f"❌ LLM 初始化失败: {e}")
    raise

# =====================================================
# Embedding Model 初始化
# =====================================================

logger.info(f"正在加载 Embedding 模型: {EMBEDDING_MODEL_PATH}")
try:
    embedding_model = SentenceTransformer(EMBEDDING_MODEL_PATH)
    logger.info(f"✅ Embedding 模型加载成功: {EMBEDDING_MODEL_PATH}")
except Exception as e:
    logger.error(f"❌ Embedding 模型加载失败: {e}")
    raise
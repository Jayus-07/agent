"""依赖注入 — Agent 单例，惰性初始化，避免启动时加载所有模型"""
import threading
from typing import Optional

from backend.shared.logger import logger

# 惰性加载单例
_lock = threading.Lock()
_multi_agent = None
_sql_agent = None
_rag_pipeline = None
_rag_init_error = None
_report_generator = None


def get_multi_agent():
    global _multi_agent
    if _multi_agent is None:
        with _lock:
            if _multi_agent is None:
                from backend.orchestration.graph import MultiAgentSystem
                _multi_agent = MultiAgentSystem()
    return _multi_agent


def get_sql_agent():
    global _sql_agent
    if _sql_agent is None:
        with _lock:
            if _sql_agent is None:
                from backend.sql.sql_agent import SQLAgent
                from backend.config import DB_CONFIG
                _sql_agent = SQLAgent(db_config=DB_CONFIG, max_retries=2)
    return _sql_agent


def get_rag_pipeline():
    global _rag_pipeline, _rag_init_error
    if _rag_pipeline is None and _rag_init_error is None:
        with _lock:
            if _rag_pipeline is None and _rag_init_error is None:
                try:
                    from backend.rag.pipeline import RAGPipeline
                    _rag_pipeline = RAGPipeline()
                    logger.info("[API] RAG 管道初始化成功")
                except Exception as e:
                    _rag_init_error = str(e)
                    logger.error(f"[API] RAG 管道初始化失败: {e}")
    if _rag_init_error is not None:
        raise RuntimeError(f"RAG 服务不可用: {_rag_init_error}")
    return _rag_pipeline


def get_rag_status() -> dict:
    """返回 RAG 模块状态（供 health check 使用）"""
    return {
        "available": _rag_pipeline is not None,
        "error": _rag_init_error,
    }


def get_report_generator():
    global _report_generator
    if _report_generator is None:
        with _lock:
            if _report_generator is None:
                from backend.report import ReportGenerator
                _report_generator = ReportGenerator(output_dir="data/reports")
    return _report_generator

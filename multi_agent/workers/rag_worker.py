"""
workers/rag_worker.py — RAG Worker 节点

调用 search_knowledge_tool 从知识库检索文档内容。
"""

from multi_agent.tools import search_knowledge_tool
from multi_agent.workers.base import execute_with_retry
from utils.logger import logger


async def rag_worker_node(state: dict) -> dict:
    """
    RAG Worker: 从知识库检索文档。

    从 state.current_step_id 定位当前步骤，
    调用 search_knowledge_tool，结果写回 state.step_results。
    """
    logger.info(f"[RAG Worker] 开始执行 step={state.get('current_step_id')}")
    return await execute_with_retry(state, search_knowledge_tool)

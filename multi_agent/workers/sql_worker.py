"""
workers/sql_worker.py — SQL Worker 节点

调用 sql_query_tool 执行数据库查询。
"""

from multi_agent.tools import sql_query_tool
from multi_agent.workers.base import execute_with_retry
from utils.logger import logger


def sql_worker_node(state: dict) -> dict:
    """
    SQL Worker: 执行数据库查询。

    从 state.current_step_id 定位当前步骤，
    调用 sql_query_tool，结果写回 state.step_results。
    """
    logger.info(f"[SQL Worker] 开始执行 step={state.get('current_step_id')}")
    return execute_with_retry(state, sql_query_tool)

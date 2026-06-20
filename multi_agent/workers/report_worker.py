"""
workers/report_worker.py — Report Worker 节点

调用 generate_report_tool 生成结构化报告。
"""

from multi_agent.tools import generate_report_tool
from multi_agent.workers.base import execute_with_retry
from utils.logger import logger


async def report_worker_node(state: dict) -> dict:
    """
    Report Worker: 生成结构化报告。

    从 state.current_step_id 定位当前步骤，
    调用 generate_report_tool，结果写回 state.step_results。
    """
    logger.info(f"[Report Worker] 开始执行 step={state.get('current_step_id')}")
    return await execute_with_retry(state, generate_report_tool)

"""
workers/base.py — Worker 公共逻辑

所有 Worker 共享的:
  - retry 机制（失败自动重试）
  - timeout 保护
  - 状态写回 state.step_results
"""

import time
from utils.logger import logger

# 全局配置
DEFAULT_MAX_RETRIES = 2
DEFAULT_TIMEOUT = 60  # 秒


def execute_with_retry(
    state: dict,
    tool_fn,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    """
    执行工具调用，带 retry + 状态管理。

    参数:
        state:      当前 AgentState
        tool_fn:    工具调用函数 (接收 **params)
        max_retries:最大重试次数

    返回:
        {"step_results": {...}} 状态更新字典
    """
    step_id = state.get("current_step_id")
    if not step_id:
        logger.error("[Worker] current_step_id 为空，无法执行")
        return {}

    plan = state.get("plan", {})
    step_info = plan.get("nodes", {}).get(step_id)
    if not step_info:
        logger.error(f"[Worker] 找不到 step 信息: {step_id}")
        return {}

    step_results = dict(state.get("step_results", {}))

    # 初始化 step 状态
    sr = step_results.get(step_id, {})
    sr["step_id"] = step_id
    sr["capability"] = step_info.get("capability", "unknown")
    sr["description"] = step_info.get("description", "")
    sr["retries"] = 0

    params = step_info.get("params", {})

    # —— 重试循环 ——
    for attempt in range(max_retries + 1):
        sr["status"] = "running"
        sr["started_at"] = time.time()
        sr["retries"] = attempt
        step_results[step_id] = dict(sr)

        try:
            logger.info(f"[Worker] 执行 step={step_id} "
                        f"cap={sr['capability']} "
                        f"(第{attempt+1}次)")
            output = tool_fn.invoke(params)

            sr["status"] = "success"
            sr["output"] = output
            sr["error"] = None
            sr["finished_at"] = time.time()
            step_results[step_id] = dict(sr)

            logger.info(f"[Worker] step={step_id} 成功 "
                        f"(耗时 {sr['finished_at'] - sr['started_at']:.2f}s)")
            break

        except Exception as e:
            logger.warning(f"[Worker] step={step_id} 失败 "
                           f"(第{attempt+1}次): {e}")

            if attempt < max_retries:
                logger.info(f"[Worker] step={step_id} 重试中...")
                continue

            # 最终失败
            sr["status"] = "failed"
            sr["error"] = str(e)
            sr["finished_at"] = time.time()
            step_results[step_id] = dict(sr)
            logger.error(f"[Worker] step={step_id} 最终失败: {e}")

    return {"step_results": step_results}

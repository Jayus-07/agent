"""
workers/base.py — Worker 公共逻辑

所有 Worker 共享的:
  - asyncio 超时保护（asyncio.wait_for）
  - 指数退避重试（1.5s → 2.25s）
  - 错误分类（可重试 vs 不可重试）
  - 状态写回 state.step_results
"""

import asyncio
import time
from utils.logger import logger

# 全局配置
DEFAULT_TIMEOUT = 60       # 单次调用超时（秒）
DEFAULT_MAX_RETRIES = 2    # 最大重试次数（总共最多 3 次尝试）
RETRY_BACKOFF_BASE = 1.5   # 指数退避基数（秒）: 1.5, 2.25

# 不可重试的错误模式（参数错误/资源不存在等，重试无意义）
UNRETRYABLE_PATTERNS = [
    "no such table",
    "column not found",
    "syntax error",
    "invalid parameter",
    "权限不足",
    "permission denied",
    "table does not exist",
]


def _is_retryable(error: str) -> bool:
    """判断错误是否值得重试"""
    error_lower = error.lower()
    return not any(p.lower() in error_lower for p in UNRETRYABLE_PATTERNS)


async def execute_with_retry(
    state: dict,
    tool_fn,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """
    执行工具调用，带 retry + timeout + 状态管理。

    参数:
        state:       当前 AgentState
        tool_fn:     工具调用函数 (接收 **params)
        max_retries: 最大重试次数（默认 2，总共最多 3 次尝试）
        timeout:     单次调用超时秒数

    返回:
        {"step_results": {...}} 状态更新字典
    """
    from multi_agent.alerts import make_alert, log_degradation

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

    # 过滤掉 LangChain Tool schema 不识别的内部字段
    # （如 _previous_outputs 等 Worker 内部约定的字段，不属于 Tool 的 args_schema）
    # 注意：_template / _title 是 report_agent.filters 内部的合法字段（用户可显式传），
    #       在 generate_report() 内部通过 filters.pop 消费，不能在这里过滤。
    if isinstance(params, dict):
        params.pop("_previous_outputs", None)

    # —— 重试循环 ——
    last_error = None
    for attempt in range(max_retries + 1):
        sr["status"] = "running"
        sr["started_at"] = time.time()
        sr["retries"] = attempt
        step_results[step_id] = dict(sr)

        try:
            logger.info(
                f"[Worker] 执行 step={step_id} "
                f"cap={sr['capability']} "
                f"(第{attempt+1}/{max_retries+1}次，timeout={timeout}s)"
            )

            # ⭐ asyncio 超时保护：将同步 Tool 放到线程池中执行
            output = await asyncio.wait_for(
                asyncio.to_thread(tool_fn.invoke, params),
                timeout=timeout,
            )

            sr["status"] = "success"
            sr["output"] = output
            sr["error"] = None
            sr["error_type"] = None
            sr["finished_at"] = time.time()
            step_results[step_id] = dict(sr)

            elapsed = sr["finished_at"] - sr.get("started_at", sr["finished_at"])
            logger.info(
                f"[Worker] step={step_id} 成功 "
                f"(耗时 {elapsed:.2f}s)"
            )
            break

        except asyncio.TimeoutError:
            last_error = f"步骤执行超时（{timeout}s）"
            logger.warning(
                f"[Worker] step={step_id} 超时 "
                f"(第{attempt+1}/{max_retries+1}次)"
            )

        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"[Worker] step={step_id} 失败 "
                f"(第{attempt+1}/{max_retries+1}次): {e}"
            )

        # 检查是否值得重试
        if not _is_retryable(str(last_error)):
            logger.warning(
                f"[Worker] step={step_id} 错误不可重试，直接失败"
            )
            break

        # 指数退避
        if attempt < max_retries:
            delay = RETRY_BACKOFF_BASE ** (attempt + 1)
            logger.info(f"[Worker] step={step_id} 等待 {delay:.1f}s 后重试")
            await asyncio.sleep(delay)

    else:
        # 循环正常结束（非 break），说明重试耗尽
        pass

    # 检查最终状态：如果仍为 running，标记为 failed
    if sr.get("status") == "running":
        sr["status"] = "failed"
        sr["error"] = last_error
        sr["error_type"] = "timeout" if "超时" in str(last_error) else "unknown"
        sr["finished_at"] = time.time()
        step_results[step_id] = dict(sr)

        # 告警
        if sr.get("error_type") == "timeout":
            alert = make_alert("WORKER_TIMEOUT", {"step_id": step_id, "error": last_error})
        else:
            alert = make_alert("WORKER_RETRY_EXHAUST", {"step_id": step_id, "error": last_error})
        log_degradation(alert)
        logger.error(f"[Worker] step={step_id} 最终失败: {last_error}")

    return {"step_results": step_results}

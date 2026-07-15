"""
skills/base.py — BaseSkill 抽象类

每个 Skill 表示一种或多种业务 Capability。
Skill 通过 Tool 完成具体工作，不感知 Planner/调度逻辑。

子类只需声明:
  - capabilities: list[str]  — 注册的 Capability（如 ["sql.query"]）
  - _tool_fn                 — 关联的 LangChain Tool
"""

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from backend.shared.logger import logger

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 1.5

UNRETRYABLE_PATTERNS = [
    "no such table", "column not found", "syntax error",
    "invalid parameter", "权限不足", "permission denied",
    "table does not exist",
]


def _is_retryable(error: str) -> bool:
    error_lower = error.lower()
    return not any(p.lower() in error_lower for p in UNRETRYABLE_PATTERNS)


class BaseSkill(ABC):
    """Skill 抽象基类。每个 Skill 封装一组 Capability。

    子类需声明:
      - capabilities: ClassVar[list[str]]  — 如 ["sql.query", "sql.analyze"]
      - _tool_fn: property → LangChain Tool

    Capability 是 Planner 与 Skill 之间唯一的契约。
    """

    name: str = ""
    capabilities: ClassVar[list[str]] = []

    @property
    @abstractmethod
    def _tool_fn(self) -> Any:
        """返回关联的 LangChain Tool 可调用对象"""
        ...

    async def execute(
        self,
        state: dict,
        step_capability: str = "",
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """执行当前 Capability：从 state 提取 step → 调用 Tool → 写回结果。

        返回: {"step_results": {...}}
        """
        from backend.orchestration.alerts import make_alert, log_degradation

        step_id = state.get("current_step_id")
        if not step_id:
            logger.error(f"[{self.name}] current_step_id 为空")
            return {}

        plan = state.get("plan", {})
        step_info = plan.get("nodes", {}).get(step_id)
        if not step_info:
            logger.error(f"[{self.name}] 找不到 step: {step_id}")
            return {}

        step_results = dict(state.get("step_results", {}))

        sr = step_results.get(step_id, {})
        sr["step_id"] = step_id
        sr["capability"] = step_capability or step_info.get("capability", "unknown")
        sr["description"] = step_info.get("description", "")
        sr["retries"] = 0

        params = dict(step_info.get("params", {}))
        params.pop("_previous_outputs", None)

        last_error = None
        for attempt in range(max_retries + 1):
            sr["status"] = "running"
            sr["started_at"] = time.time()
            sr["retries"] = attempt
            step_results[step_id] = dict(sr)

            try:
                logger.info(
                    f"[{self.name}] step={step_id} cap={sr['capability']} "
                    f"(第{attempt+1}/{max_retries+1}次，timeout={timeout}s)"
                )

                output = await asyncio.wait_for(
                    asyncio.to_thread(self._tool_fn.invoke, params),
                    timeout=timeout,
                )

                sr["status"] = "success"
                sr["output"] = output
                sr["error"] = None
                sr["error_type"] = None
                sr["finished_at"] = time.time()
                step_results[step_id] = dict(sr)

                elapsed = sr["finished_at"] - sr.get("started_at", sr["finished_at"])
                logger.info(f"[{self.name}] step={step_id} 成功 (耗时 {elapsed:.2f}s)")
                break

            except asyncio.TimeoutError:
                last_error = f"步骤执行超时（{timeout}s）"
                logger.warning(f"[{self.name}] step={step_id} 超时")

            except Exception as e:
                last_error = str(e)
                logger.warning(f"[{self.name}] step={step_id} 失败: {e}")

            if not _is_retryable(str(last_error)):
                break

            if attempt < max_retries:
                delay = RETRY_BACKOFF_BASE ** (attempt + 1)
                await asyncio.sleep(delay)

        if sr.get("status") == "running":
            sr["status"] = "failed"
            sr["error"] = last_error
            sr["error_type"] = "timeout" if "超时" in str(last_error) else "unknown"
            sr["finished_at"] = time.time()
            step_results[step_id] = dict(sr)

            code = "WORKER_TIMEOUT" if sr["error_type"] == "timeout" else "WORKER_RETRY_EXHAUST"
            alert = make_alert(code, {"step_id": step_id, "error": last_error})
            log_degradation(alert)
            logger.error(f"[{self.name}] step={step_id} 最终失败: {last_error}")

        return {"step_results": step_results}


# 向后兼容
async def execute_with_retry(state: dict, tool_fn, max_retries=DEFAULT_MAX_RETRIES, timeout=DEFAULT_TIMEOUT) -> dict:
    skill = _CompatSkill(tool_fn)
    return await skill.execute(state, max_retries=max_retries, timeout=timeout)


class _CompatSkill(BaseSkill):
    name = "_compat"
    capabilities = []

    def __init__(self, tool_fn):
        self._tool = tool_fn

    @property
    def _tool_fn(self):
        return self._tool

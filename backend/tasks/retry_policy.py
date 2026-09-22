"""tasks/retry_policy.py — 集中重试策略（Phase2 Step2）。

三套机制边界（本模块只承载第一套）：
  Retry   = 进程仍活着、已知临时错误 → 本模块（延迟/budget/重投）
  Recovery = Worker/进程死亡 → lease 过期 → stale sweeper（Step1）
  Failure = 不可恢复错误或重试耗尽 → TaskState FAILED

原则：
- retry 参数集中于此，禁止散落在 Celery decorator（P4：参数源沿用
  CELERY_MAX_RETRIES / TASK_RETRY_INITIAL_DELAY / TASK_RETRY_MAX_DELAY）
- max_retries 以 tasks.max_retries 行级快照为权威（创建时取自
  CELERY_MAX_RETRIES），policy 只提供延迟形态
- 不同 workflow 可覆盖（default / agent / rag_index），未命中回落 default
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from backend.shared.logger import logger


@dataclass(frozen=True)
class RetryPolicy:
    """单一 workflow 的重试延迟形态（max_retries 权威在 tasks.max_retries）。"""

    initial_delay: int
    backoff: float          # 指数底数
    max_delay: int
    jitter: bool


def _load_policies() -> dict[str, RetryPolicy]:
    from backend.config.tasks import (TASK_RETRY_INITIAL_DELAY,
                                      TASK_RETRY_JITTER,
                                      TASK_RETRY_MAX_DELAY)

    default = RetryPolicy(
        initial_delay=TASK_RETRY_INITIAL_DELAY, backoff=2.0,
        max_delay=TASK_RETRY_MAX_DELAY, jitter=TASK_RETRY_JITTER)
    return {
        # default / agent / rag_index：max_retries 统一取 CELERY_MAX_RETRIES
        # （tasks.max_retries 快照同源），延迟形态当前一致，保留覆盖点
        "default": default,
        "agent": default,
        "rag_index": default,
    }


def get_policy(workflow: str) -> RetryPolicy:
    """workflow → RetryPolicy（未注册的 graph_name 回落 default）。"""
    try:
        return _load_policies().get(workflow) or _load_policies()["default"]
    except Exception:  # noqa: BLE001 — 配置异常不让重试决策失败
        logger.warning("[RetryPolicy] policy load failed, fallback default",
                       exc_info=True)
        return RetryPolicy(initial_delay=5, backoff=2.0, max_delay=120,
                           jitter=True)


def compute_delay(policy: RetryPolicy, retry_index: int) -> int:
    """第 retry_index 次重试（0 起）的延迟秒数：指数退避 + 有界抖动。

    jitter=true 时在 [base/2, base] 均匀取值（有界抖动，避免 0 延迟惊群）。
    """
    base = min(policy.max_delay,
               int(policy.initial_delay * (policy.backoff ** max(0, retry_index))))
    if not policy.jitter or base <= 1:
        return base
    return random.randint(max(1, base // 2), base)


class TaskRetryScheduled(Exception):
    """impl 已完成分类与状态落库，请求 Celery 壳执行显式 retry。

    携带原始异常（作为 retry exc 保留真实堆栈）、分类结果与延迟；
    壳在真正入队前还须复查任务状态（CANCELLED/PAUSED 等禁止 retry）。
    """

    def __init__(self, original: BaseException, *, error_type: str,
                 retryable: bool, delay: int, retry_count: int,
                 max_retries: int):
        self.original = original
        self.error_type = error_type
        self.retryable = retryable
        self.delay = int(delay)
        self.retry_count = int(retry_count)
        self.max_retries = int(max_retries)
        super().__init__(
            f"task retry scheduled: {error_type} delay={delay}s "
            f"retry={retry_count}/{max_retries}")

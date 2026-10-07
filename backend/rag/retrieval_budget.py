"""rag/retrieval_budget.py — 检索级 deadline 预算（Phase 5，spec §23/§24）。

为什么存在：此前检索链路没有任何内部预算——60s 的 OVERALL_REQUEST_TIMEOUT
只是软告警，内部 RAG 一直耗到 Tool Runtime 的 15s policy deadline 才被杀，
30~60s 的无降级慢请求没有任何「内部刹车」。

模型：入口（RAGChain 检索阶段开始）生成一次 ``RetrievalBudget``（monotonic
deadline），下游所有阶段用 ``remaining_ms()`` 消费**同一份剩余预算**，
禁止各层自起炉灶重算 timeout。预算经 ContextVar 传播——multi_query 在
提交方线程 copy_context 快照，变体任务天然继承。

设计约束：
- 预算耗尽 ≠ 异常：各阶段按 spec §25 阶梯降级（rewrite 超时→原始 query、
  fanout 部分完成→用部分、adaptive/synonym 无预算→跳过），绝不整链失败。
- 无预算（直接调用 retriever 的旁路）→ 各阶段回退各自默认行为。
"""
from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass
from typing import Optional

from backend.shared.logger import logger


@dataclass
class RetrievalBudget:
    """一次请求检索阶段的总预算（monotonic 时钟，不受系统时间回拨影响）。"""

    total_ms: float
    deadline_at: float | None = None

    def __post_init__(self) -> None:
        # deadline = 起点 + 总预算（显式传 deadline_at 的仅测试构造过期预算）
        if self.deadline_at is None:
            self.deadline_at = time.monotonic() + max(self.total_ms, 0) / 1000

    def remaining_ms(self) -> float:
        """剩余预算（毫秒），可为负。"""
        return (self.deadline_at - time.monotonic()) * 1000

    def expired(self) -> bool:
        return self.remaining_ms() <= 0

    def slice(self, ms: float) -> "RetrievalBudget":
        """切出子预算（不超过父预算剩余）。"""
        return RetrievalBudget(
            total_ms=min(ms, max(self.remaining_ms(), 0)),
        )

    def as_dict(self) -> dict:
        return {
            "total_ms": round(self.total_ms, 1),
            "remaining_ms": round(max(self.remaining_ms(), 0), 1),
            "expired": self.expired(),
        }


_budget_var: contextvars.ContextVar[Optional[RetrievalBudget]] = (
    contextvars.ContextVar("rag_retrieval_budget", default=None)
)


def start_retrieval_budget(total_ms: float) -> RetrievalBudget:
    """入口生成预算并绑定到当前上下文（返回 token 供 reset）。"""
    budget = RetrievalBudget(total_ms=total_ms)
    _budget_var.set(budget)
    return budget


def clear_retrieval_budget() -> None:
    """请求收口清预算（防止复用线程读到上一请求的过期预算）。"""
    _budget_var.set(None)


def current_budget() -> Optional[RetrievalBudget]:
    """当前请求的检索预算；无（旁路调用/离线评测）返回 None。"""
    return _budget_var.get()


def remaining_ms(default_ms: float) -> float:
    """剩余预算的统一读取口；无预算时返回调用方默认值（行为不变）。

    spec §24：下游禁止各自重算 timeout——统一从这里拿剩余量。
    """
    budget = _budget_var.get()
    if budget is None:
        return default_ms
    return max(budget.remaining_ms(), 0.0)


def log_degrade(stage: str, reason: str, **detail) -> None:
    """降级统一留痕（日志 + 可选 trace event 由调用方补）。"""
    logger.warning(f"[RetrievalBudget] {stage} 降级: {reason} {detail or ''}")


__all__ = [
    "RetrievalBudget",
    "start_retrieval_budget",
    "clear_retrieval_budget",
    "current_budget",
    "remaining_ms",
    "log_degrade",
]

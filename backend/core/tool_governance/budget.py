"""请求级 Tool 调用预算。"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class RequestToolBudget:
    total_limit: int = 12
    per_capability_limit: int = 3
    high_risk_limit: int = 1
    total_calls: int = 0
    capability_calls: dict[str, int] = field(default_factory=dict)
    fingerprints: set[str] = field(default_factory=set)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def consume(self, capability: str, *, high_risk: bool = False, fingerprint: str = "") -> str | None:
        with self._lock:
            if self.total_calls >= self.total_limit:
                return "tool_call_budget_exhausted"
            calls = self.capability_calls.get(capability, 0)
            limit = self.high_risk_limit if high_risk else self.per_capability_limit
            if calls >= limit:
                return "tool_call_budget_exhausted"
            self.total_calls += 1
            self.capability_calls[capability] = calls + 1
            if fingerprint:
                self.fingerprints.add(fingerprint)
        return None


_current_budget: ContextVar[RequestToolBudget | None] = ContextVar(
    "tool_governance_budget", default=None
)


def bind_tool_budget(budget: RequestToolBudget | None) -> None:
    _current_budget.set(budget)


def get_tool_budget() -> RequestToolBudget | None:
    return _current_budget.get()


def reset_tool_budget() -> None:
    _current_budget.set(None)


__all__ = [
    "RequestToolBudget",
    "bind_tool_budget",
    "get_tool_budget",
    "reset_tool_budget",
]

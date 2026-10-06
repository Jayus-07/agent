"""按 Tool/租户/用户维度的进程内滑动窗口限流器。"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool = False
    requests: int = 30
    window_seconds: int = 60
    scope: str = "tenant"


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, config: RateLimitConfig, *, now: float | None = None) -> bool:
        if not config.enabled:
            return True
        current = time.monotonic() if now is None else now
        cutoff = current - max(1, config.window_seconds)
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= max(1, config.requests):
                return False
            events.append(current)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowRateLimiter()

__all__ = ["RateLimitConfig", "SlidingWindowRateLimiter", "rate_limiter"]

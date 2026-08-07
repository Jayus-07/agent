"""LLM 限流 — Token Bucket 算法。

设计：
- 按用户（user_id 或 session_id）+ 全局双层限流
- 默认 100 QPS / 用户，1000 QPS / 全局
- require_rate_limit 作为 FastAPI 依赖注入，超限时抛出 HTTP 429
- 双层 token bucket：global（整个服务）+ per-user（每个 user）
- acquire() 返回 True 表示通过，False 表示被限流
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from fastapi import Request

from backend.shared.logger import logger
from backend.config.llm import LLM_RATE_LIMIT_QPS, LLM_RATE_LIMIT_BURST


@dataclass
class _Bucket:
    """Token bucket 状态。"""
    capacity: float      # 最大令牌数（= burst）
    refill_rate: float   # 每秒补充令牌数（= qps）
    tokens: float        # 当前令牌数
    last_refill: float   # 上次补充时间戳（秒）

    def try_consume(self, n: float = 1.0) -> bool:
        """尝试消费 n 个令牌。返回 True 表示成功。"""
        now = time.monotonic()
        # 补充：(now - last_refill) * refill_rate
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


class LLMRateLimiter:
    """双层限流器：global + per-user。

    用法：
        limiter = LLMRateLimiter()
        if not limiter.acquire(user_id=session_id):
            logger.warning("rate limited")
            # 业务侧选择：等待 / 拒答 / fallback
    """

    def __init__(
        self,
        global_qps: float = LLM_RATE_LIMIT_QPS,
        global_burst: float = LLM_RATE_LIMIT_BURST,
        per_user_qps: float | None = None,
        per_user_burst: float | None = None,
    ):
        # 全局限流
        self._global = _Bucket(
            capacity=global_burst,
            refill_rate=global_qps,
            tokens=global_burst,
            last_refill=time.monotonic(),
        )
        # per-user 限流（默认是 global 的 1/10）
        pu_q = per_user_qps or max(1.0, global_qps / 10)
        pu_b = per_user_burst or max(1.0, global_burst / 10)
        self._per_user_qps = pu_q
        self._per_user_burst = pu_b
        self._users: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    def _get_user_bucket(self, user_id: str) -> _Bucket:
        b = self._users.get(user_id)
        if b is None:
            b = _Bucket(
                capacity=self._per_user_burst,
                refill_rate=self._per_user_qps,
                tokens=self._per_user_burst,
                last_refill=time.monotonic(),
            )
            self._users[user_id] = b
        return b

    def acquire(self, user_id: str | None = None) -> bool:
        """尝试获取 1 个令牌。

        Args:
            user_id: 用户标识（None 时只检查 global）

        Returns:
            True: 通过；False: 被限流
        """
        # global 必须先查（更严格的门）
        if not self._global.try_consume():
            logger.warning(
                f"[RateLimit] GLOBAL 限流触发: tokens={self._global.tokens:.1f}/{self._global.capacity}"
            )
            return False
        if user_id is None:
            return True
        ub = self._get_user_bucket(user_id)
        if not ub.try_consume():
            logger.warning(
                f"[RateLimit] per-user 限流触发: user={user_id} "
                f"tokens={ub.tokens:.1f}/{ub.capacity}"
            )
            return False
        return True

    def stats(self) -> dict:
        """返回当前限流状态（用于 /metrics 或调试）。"""
        return {
            "global_tokens": round(self._global.tokens, 1),
            "global_capacity": self._global.capacity,
            "user_count": len(self._users),
            "per_user_qps": self._per_user_qps,
        }

    def retry_after_seconds(self, user_id: str | None = None) -> float:
        """估算下次可获取令牌的等待时间（秒）。"""
        now = time.monotonic()
        # global bucket
        g_elapsed = now - self._global.last_refill
        g_tokens = min(self._global.capacity, self._global.tokens + g_elapsed * self._global.refill_rate)
        g_wait = 0.0 if g_tokens >= 1.0 else (1.0 - g_tokens) / self._global.refill_rate
        if user_id is None:
            return max(g_wait, 0.5)
        # per-user bucket
        ub = self._get_user_bucket(user_id)
        u_elapsed = now - ub.last_refill
        u_tokens = min(ub.capacity, ub.tokens + u_elapsed * ub.refill_rate)
        u_wait = 0.0 if u_tokens >= 1.0 else (1.0 - u_tokens) / ub.refill_rate
        return max(g_wait, u_wait, 0.5)


# 模块级单例
_limiter: LLMRateLimiter | None = None


def get_rate_limiter() -> LLMRateLimiter:
    """获取全局单例（lazy init）。"""
    global _limiter
    if _limiter is None:
        _limiter = LLMRateLimiter()
    return _limiter


async def require_rate_limit(request: Request, user_id: str | None = None) -> None:
    """FastAPI 依赖：检查限流，超限时抛出 HTTPException 429。

    用法:
        @router.post("/chat")
        async def chat(req: ChatRequest, _rate=Depends(require_rate_limit)):
            ...
    """
    limiter = get_rate_limiter()
    uid = user_id or (request.client.host if request.client else "unknown")
    if not limiter.acquire(uid):
        retry = int(limiter.retry_after_seconds(uid)) + 1
        from fastapi import HTTPException
        raise HTTPException(
            status_code=429,
            detail={
                "code": "RATE_LIMITED",
                "message": "请求过于频繁，请稍后重试",
                "retry_after_seconds": retry,
            },
            headers={"Retry-After": str(retry)},
        )

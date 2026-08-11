"""router.py — Router 主流程（3 层 fallback，2026-08-11）

  Rule Router（1ms）
    ↓ confidence < 0.8
  Embedding Router（~30ms）
    ↓ confidence < 0.85
  LLM Router（~3-5s，qwen 本地）
    ↓
  RouteDecision
"""
from __future__ import annotations

import asyncio
import time

from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.vector_router import VectorRouter
from backend.orchestration.router.llm_router import LLMRouter
from backend.shared.logger import logger


class Router:
    """3 层 fallback Router。"""

    def __init__(self, llm_timeout: int = 20):
        self.rule = RuleRouter()
        self.vector = VectorRouter()
        self.llm = LLMRouter(timeout=llm_timeout)

    def route(self, query: str) -> RouteDecision:
        """同步路由入口。

        链路:
          1. Rule (0.001s)
          2. Embedding (0.03s)
          3. LLM (3-5s)
        """
        from backend.observability.metrics import record_router_decision

        t0 = time.time()
        # 1. Rule Router（强信号）
        result = self.rule.route(query)
        if result and result.confidence >= 0.8:
            logger.info(
                f"[Router] Rule 命中: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "rule", result.confidence)
            return result

        # 2. Embedding Router（语义匹配）
        result = self.vector.route(query)
        if result and result.confidence >= 0.85:
            logger.info(
                f"[Router] Embedding 命中: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "embedding", result.confidence)
            return result

        # 3. LLM Router（兜底）
        result = self.llm.route(query)
        logger.info(
            f"[Router] LLM 兜底: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
        )
        record_router_decision(result.execution_mode.value, "llm", result.confidence)
        return result

    async def aroute(self, query: str) -> RouteDecision:
        """异步路由入口（FastAPI 场景）。"""
        return await asyncio.to_thread(self.route, query)


# ── 模块级单例 ──
_router_instance: Router | None = None


def get_router() -> Router:
    """获取 Router 单例（首次调用时初始化）。"""
    global _router_instance
    if _router_instance is None:
        _router_instance = Router()
    return _router_instance

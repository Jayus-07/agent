"""Self-Correction Strategy — PR-1.2（ADR-0002 阶段 1.2）。

从 RAGChain 抽出 self-correction 的：
- 状态字段：_self_correction_retry_count / _self_correction_pending
- 方法：can_retry() / try_rewrite() / record_attempt() / reset()
- 决策：low/medium/high risk 各自的 retry 上限（默认 max_retries 来自 config）

设计动机：
- RAGChain god class 6 个 mutable 字段中 2 个属于 self-correction
- can_retry 判定逻辑混在 _can_self_correct 11 行函数中
- 抽离后 RAGChain 持有 corrector 引用，不直接管理状态

边界（PR-1.2 范围）：
- ✅ 抽 _rewrite_query → try_rewrite()
- ✅ 抽 _self_correction_retry_count / _self_correction_pending 状态
- ✅ 抽 _can_self_correct 判定
- ❌ 不动 _try_self_correct（依赖 _prepare/_execute/_verify，等 PR-1.4 拆）
- ❌ 不动 _handle_llm_reject 兜底逻辑

后续（PR-1.4）：
- _try_self_correct 拆为 corrector.run_with_retry(prepare_fn, execute_fn, verify_fn)
- 接收 callable 作为依赖注入，RAGChain 不再被 corrector 反向调用
"""
from __future__ import annotations

from backend.shared.logger import logger


class SelfCorrectionStrategy:
    """Self-Correction 状态 + query 改写策略。

    状态：
      - retry_count: 已重试次数
      - pending_state: 当前 pending 状态（success/failed/None）
      - max_retries: 最大重试次数（来自 config 或构造时覆盖）

    用法：
        corrector = SelfCorrectionStrategy()
        if corrector.can_retry():
            new_q = corrector.try_rewrite(question, reason)
            if new_q:
                # ... 用 new_q 重新跑 retrieve + generate
                corrector.record_attempt(success=True)
            else:
                corrector.record_attempt(success=False)
    """

    def __init__(self, max_retries: int | None = None):
        """Args:
            max_retries: 覆盖 config.SELF_CORRECTION_MAX_RETRIES（用于测试或策略调优）
        """
        self._retry_count: int = 0
        self._pending_state: str | None = None
        self._max_retries_override = max_retries

    # =====================================================
    # 状态读写
    # =====================================================

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @property
    def pending_state(self) -> str | None:
        return self._pending_state

    def record_attempt(self, success: bool) -> None:
        """记录一次重试尝试（成功或失败都计数）。"""
        self._retry_count += 1
        self._pending_state = "success" if success else "failed"

    def reset(self) -> None:
        """新会话开始时重置状态（每次 ask() 入口调用）。"""
        self._retry_count = 0
        self._pending_state = None

    # =====================================================
    # 决策
    # =====================================================

    def can_retry(self) -> bool:
        """是否能再尝试一次 self-correction。

        受 2 个开关控制：
          - config.SELF_CORRECTION_ENABLED（总开关）
          - max_retries 限制（来自 config 或构造时覆盖）

        注意：max_retries=0 也被尊重（即禁用 retry），用 `is not None` 区分覆盖 vs 默认。
        """
        from backend.config import SELF_CORRECTION_ENABLED, SELF_CORRECTION_MAX_RETRIES
        if self._max_retries_override is not None:
            max_r = self._max_retries_override
        else:
            max_r = SELF_CORRECTION_MAX_RETRIES
        return SELF_CORRECTION_ENABLED and self._retry_count < max_r

    # =====================================================
    # Query 改写（从 RAGChain._rewrite_query 完整迁移）
    # =====================================================

    def try_rewrite(self, question: str, reason: str) -> str | None:
        """LLM 改写 query，返回新 query 或 None（改写失败）。

        行为契约：
          - 失败时 logger.warning + 写 trace span status=error，返回 None
          - 成功时返回 rewrites[0]（第一个改写结果）
        """
        from backend.observability.tracer import trace_collector, SpanKind
        from backend.infra.llm import llm
        from langchain_core.messages import HumanMessage

        rewrite_span = trace_collector.start_span(
            "self_correction_rewrite", name="Self-Correction Rewrite",
            kind=SpanKind.SELF_CORRECTION.value,
        )
        try:
            prompt = (
                f"原问题被拒答（原因：{reason}）。请改写使能从知识库命中，"
                f"给 3 个更有效的检索 query，每行一个，不要编号：\n"
                f"原问题: {question}\n改写结果:"
            )
            result = llm.invoke([HumanMessage(content=prompt)])
            raw = result.content if hasattr(result, "content") else str(result)
            rewrites = [ln.strip() for ln in raw.strip().split("\n") if ln.strip()]
            new_query = rewrites[0] if rewrites else question
            trace_collector.end_span(rewrite_span,
                metrics={"rewrites": len(rewrites), "selected": new_query[:60]})
            return new_query
        except Exception as e:
            logger.warning(f"[Self-Correction] 改写失败: {e}")
            try:
                trace_collector.end_span(rewrite_span, status="error")
            except Exception as cleanup_e:
                # 已在异常处理路径：span 收尾失败只记录，不再覆盖原始异常
                logger.error(
                    "[Self-Correction] rewrite span 收尾失败: %s", cleanup_e,
                    exc_info=True,
                )
            return None


__all__ = ["SelfCorrectionStrategy"]

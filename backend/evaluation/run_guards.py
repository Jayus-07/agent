"""评测运行守卫——超时/截止/token 上限/provider 熔断（五期 C5-1~C5-5）。

全部阈值配置化且默认宽松/关闭（灰度纪律：实机验收时逐个打开），避免
误伤长跑。守卫只决定「停不停、记什么原因」，不改写任何已产出结果：
- 超预算/超 deadline 的 run 不得以 completed/pass 收口（COST-10
  fail-closed）——守卫把 run 状态标为 failed，persist_report 保持不降级。
- provider 熔断冷却期内跳过 evaluator 调用（记 skip 语义的空结果），
  事件落操作审计（C2-6）。
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

from backend.shared.logger import logger


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# C5-1：单条 case 超时（秒）。0 = 关闭（默认，与存量行为一致）
CASE_TIMEOUT_SECONDS = _env_int("EVAL_CASE_TIMEOUT_S", 0)
# C5-2：整批 deadline（秒）。0 = 关闭
BATCH_DEADLINE_SECONDS = _env_int("EVAL_BATCH_DEADLINE_S", 0)
# C5-4：run 级 token 上限（评测进程内存读数：SUT 本地生成 + evaluator）。
# 软阈值告警一次；硬阈值熔断停止。0 = 关闭
RUN_TOKEN_SOFT_LIMIT = _env_int("EVAL_RUN_TOKEN_SOFT_LIMIT", 0)
RUN_TOKEN_HARD_LIMIT = _env_int("EVAL_RUN_TOKEN_HARD_LIMIT", 0)
# COST-02：evaluator 侧独立子限（judge/RAGAS）。0 = 关闭
EVALUATOR_TOKEN_HARD_LIMIT = _env_int("EVAL_EVALUATOR_TOKEN_LIMIT", 0)
# C5-5：provider 熔断参数（连续失败 N 次 / 窗口 M 秒 → 冷却 T 秒）
BREAKER_FAILURE_THRESHOLD = _env_int("EVAL_PROVIDER_BREAKER_THRESHOLD", 5)
BREAKER_WINDOW_SECONDS = _env_int("EVAL_PROVIDER_BREAKER_WINDOW_S", 60)
BREAKER_COOLDOWN_SECONDS = _env_int("EVAL_PROVIDER_BREAKER_COOLDOWN_S", 300)
# C5-3：judge 显式并发池（与 ragas_workers 同构；默认 2）
JUDGE_WORKERS = _env_int("EVAL_JUDGE_WORKERS", 2)


def _in_memory_token_usage() -> dict[str, int]:
    """run 期可实时读数的 token 口径（进程内计数器）。

    JSONL 埋点（云端 embedding/rerank/SUT 云调用）只在 run 结束汇总，
    不适合做运行期熔断判据；此处只计进程内可观测部分，口径在
    report.metadata.run_guards 里显式注明。
    """
    try:
        from backend.evaluation.generation import (
            get_evaluator_token_usage,
            get_token_usage,
        )

        sut = get_token_usage()
        ev = get_evaluator_token_usage()
        sut_total = int(sut.get("prompt_tokens", 0)) + int(sut.get("completion_tokens", 0))
        ev_total = int(ev.get("prompt_tokens", 0)) + int(ev.get("completion_tokens", 0))
        return {"sut": sut_total, "evaluator": ev_total, "total": sut_total + ev_total}
    except Exception:
        return {"sut": 0, "evaluator": 0, "total": 0}


def guard_config_snapshot() -> dict[str, Any]:
    """守卫配置快照（service 落 report.metadata.run_guards 用）。"""
    return {
        "case_timeout_s": CASE_TIMEOUT_SECONDS,
        "batch_deadline_s": BATCH_DEADLINE_SECONDS,
        "run_token_soft_limit": RUN_TOKEN_SOFT_LIMIT,
        "run_token_hard_limit": RUN_TOKEN_HARD_LIMIT,
        "evaluator_token_limit": EVALUATOR_TOKEN_HARD_LIMIT,
        "token_accounting": "in_process_counters_only",
        "provider_breaker": {
            "threshold": BREAKER_FAILURE_THRESHOLD,
            "window_s": BREAKER_WINDOW_SECONDS,
            "cooldown_s": BREAKER_COOLDOWN_SECONDS,
        },
        "judge_workers": JUDGE_WORKERS,
    }


class RunGuard:
    """运行期守卫：case 循环在每个检查点轮询一次 ``blocking_reason()``。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.started_ts = time.time()
        self.deadline_ts = (
            self.started_ts + BATCH_DEADLINE_SECONDS
            if BATCH_DEADLINE_SECONDS > 0 else None
        )
        self._soft_warned = False
        self.stop_reason: str = ""
        self.provider_breaker = ProviderBreaker()

    def blocking_reason(self) -> str:
        """返回非空字符串 = 应停止后续 case（值即原因码）。"""
        if self.stop_reason:
            return self.stop_reason
        try:
            from backend.evaluation.storage import is_cancel_requested

            if is_cancel_requested(self.run_id):
                self.stop_reason = "cancelled"
                return self.stop_reason
        except Exception:
            pass
        if self.deadline_ts is not None and time.time() >= self.deadline_ts:
            self.stop_reason = "deadline_exceeded"
            self._finalize_fail("deadline_exceeded")
            return self.stop_reason
        usage = _in_memory_token_usage()
        if (
            EVALUATOR_TOKEN_HARD_LIMIT > 0
            and usage["evaluator"] >= EVALUATOR_TOKEN_HARD_LIMIT
        ):
            self.stop_reason = "evaluator_token_limit"
            self._finalize_fail("evaluator_token_limit")
            return self.stop_reason
        if RUN_TOKEN_HARD_LIMIT > 0 and usage["total"] >= RUN_TOKEN_HARD_LIMIT:
            self.stop_reason = "token_circuit_break"
            self._finalize_fail("token_circuit_break")
            return self.stop_reason
        if RUN_TOKEN_SOFT_LIMIT > 0 and usage["total"] >= RUN_TOKEN_SOFT_LIMIT:
            if not self._soft_warned:
                self._soft_warned = True
                logger.warning(
                    "[RunGuard] run %s token 达软阈值 %d（当前 %d），"
                    "达硬阈值 %d 时将熔断",
                    self.run_id, RUN_TOKEN_SOFT_LIMIT, usage["total"],
                    RUN_TOKEN_HARD_LIMIT,
                )
        return ""

    def _finalize_fail(self, reason: str) -> None:
        """COST-10 fail-closed：熔断即把 run 状态钉在 failed，防 completed/pass 收口。"""
        try:
            from backend.evaluation.audit import record_operation
            from backend.evaluation.storage import mark_run_status

            mark_run_status(self.run_id, "failed", error=reason)
            record_operation(
                f"eval_run.{reason}", self.run_id,
                actor="run_guard",
                detail=f"reason={reason}",
            )
        except Exception as e:  # noqa: BLE001 — 守卫失败不掩盖原始原因
            logger.warning("[RunGuard] 熔断收口失败: %s", e)

    def snapshot(self) -> dict[str, Any]:
        """守卫配置与状态快照（随 report.metadata.run_guards 落盘）。"""
        return {
            "case_timeout_s": CASE_TIMEOUT_SECONDS,
            "batch_deadline_s": BATCH_DEADLINE_SECONDS,
            "run_token_soft_limit": RUN_TOKEN_SOFT_LIMIT,
            "run_token_hard_limit": RUN_TOKEN_HARD_LIMIT,
            "evaluator_token_limit": EVALUATOR_TOKEN_HARD_LIMIT,
            "token_accounting": "in_process_counters_only",
            "provider_breaker": {
                "threshold": BREAKER_FAILURE_THRESHOLD,
                "window_s": BREAKER_WINDOW_SECONDS,
                "cooldown_s": BREAKER_COOLDOWN_SECONDS,
            },
            "judge_workers": JUDGE_WORKERS,
            "stop_reason": self.stop_reason,
        }


class ProviderBreaker:
    """C5-5/COST-07：provider 连续失败熔断（滑动窗口计数 + 冷却期）。

    线程安全：RAGAS 批量在线程池里并发调用。冷却期内 ``allow()`` 返回
    False，调用方跳过并记 skip（reason=provider_breaker_open）。
    """

    def __init__(
        self,
        *,
        threshold: int = BREAKER_FAILURE_THRESHOLD,
        window_s: float = BREAKER_WINDOW_SECONDS,
        cooldown_s: float = BREAKER_COOLDOWN_SECONDS,
    ) -> None:
        self.threshold = threshold
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self._failures: deque[float] = deque()
        self._open_until = 0.0
        self._lock = threading.Lock()
        self.opened_count = 0

    def allow(self) -> bool:
        with self._lock:
            if self._open_until and time.time() < self._open_until:
                return False
            if self._open_until:
                # 冷却结束，半开：放行并重置计数
                self._open_until = 0.0
                self._failures.clear()
            return True

    def record_failure(self, *, run_id: str = "") -> None:
        now = time.time()
        with self._lock:
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self.window_s:
                self._failures.popleft()
            if len(self._failures) >= self.threshold and not self._open_until:
                self._open_until = now + self.cooldown_s
                self.opened_count += 1
                logger.warning(
                    "[RunGuard] provider 熔断打开（%ds 内失败 %d 次），冷却 %ds",
                    int(self.window_s), len(self._failures), int(self.cooldown_s),
                )
                if run_id:
                    try:
                        from backend.evaluation.audit import record_operation

                        record_operation(
                            "eval_run.provider_breaker_open", run_id,
                            actor="run_guard",
                            detail=(
                                f"failures={len(self._failures)} "
                                f"window_s={self.window_s:.0f} "
                                f"cooldown_s={self.cooldown_s:.0f}"
                            ),
                        )
                    except Exception:
                        pass

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()

    @property
    def open(self) -> bool:
        with self._lock:
            return bool(self._open_until and time.time() < self._open_until)


# C5-3：judge 显式并发池——judge 调用统一收敛，避免 workers>1 时
# judge 并发随 case 并发无上限放大（触发 provider 限流）。
_judge_pool: Any = None
_judge_pool_lock = threading.Lock()


def get_judge_pool() -> Any:
    global _judge_pool
    if _judge_pool is None:
        with _judge_pool_lock:
            if _judge_pool is None:
                import concurrent.futures

                _judge_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, JUDGE_WORKERS),
                    thread_name_prefix="eval-judge",
                )
    return _judge_pool


def run_judge(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """把 judge 调用提交到全局 judge 池并等待结果（并发上限 = 池大小）。"""
    return get_judge_pool().submit(fn, *args, **kwargs).result()


__all__ = [
    "RunGuard",
    "ProviderBreaker",
    "run_judge",
    "get_judge_pool",
    "CASE_TIMEOUT_SECONDS",
]

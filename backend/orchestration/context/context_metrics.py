"""context_metrics.py — ConversationContext 可观测指标（STOP G5）。

独立于 observability/metrics.py 声明（prometheus_client 默认全局
REGISTRY 与 metrics.py 相同，/metrics 端点自然汇合），避免多会话
并行改动同一文件。标签低基数约束（对齐 SQL Agent STOP C 先例）：

允许：backend(redis|memory)、operation、status、mutation、kind。
禁止：tenant_id / user_id / conversation_id / run_id / question_id。
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

conversation_context_read_total = Counter(
    "conversation_context_read_total",
    "ConversationContext 读取总数（按 backend 与结果状态）",
    labelnames=("backend", "status"),  # status: hit | miss
)

conversation_context_write_total = Counter(
    "conversation_context_write_total",
    "ConversationContext 落库写入总数（仅 applied；按 backend）",
    labelnames=("backend",),
)

conversation_context_mutation_total = Counter(
    "conversation_context_mutation_total",
    "ConversationContext mutation 总数（按类型与结果状态）",
    labelnames=("mutation", "status"),  # status: applied|noop|stale|missing|conflict
)

conversation_context_conflict_total = Counter(
    "conversation_context_conflict_total",
    "乐观锁冲突（WATCH 重试耗尽 / save CAS 失败）",
    labelnames=("operation",),  # mutate | save
)

conversation_context_stale_update_total = Counter(
    "conversation_context_stale_update_total",
    "stale 更新被拒（并发保护生效次数）",
    labelnames=("kind",),  # pending | run
)

conversation_context_backend_error_total = Counter(
    "conversation_context_backend_error_total",
    "backend 故障次数（Redis 异常 / 不可用）",
    labelnames=("backend", "operation"),
)

conversation_context_fallback_total = Counter(
    "conversation_context_fallback_total",
    "降级进程内 memory 次数（require_shared=false 开发兜底）",
    labelnames=("reason",),  # redis_unavailable | redis_error
)

conversation_context_operation_seconds = Histogram(
    "conversation_context_operation_seconds",
    "ConversationContext 操作耗时（秒）",
    labelnames=("operation",),  # get | mutate | save | delete
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)


def record_stale(kind: str) -> None:
    """stale 拒绝计数（kind: pending | run）。软失败：指标绝不影响主链。"""
    try:
        conversation_context_stale_update_total.labels(kind=kind).inc()
    except Exception:  # noqa: BLE001
        pass

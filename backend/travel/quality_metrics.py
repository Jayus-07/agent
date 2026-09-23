"""travel/quality_metrics.py — 旅游行程质量遥测（STOP I6）。

独立文件声明（prometheus_client 默认全局 REGISTRY，与 observability/
metrics.py 自然汇合到 /metrics），避免多会话并行改动同一文件 —— 先例：
orchestration/context/context_metrics.py（STOP G5）。

标签低基数约束（对齐 SQL Agent STOP C / STOP G5）：
  允许：constraint（固定违反码枚举）、status、result、stage。
  禁止：user_id / conversation_id / poi_id / city / destination —— 高基数
  标签会把 /metrics 拖垮，行程归因走 trace（travel_plan_run span /
  execution tags），不走 metrics。

结构化事件（logger，事件名进 msg 的 event= 字段，与 [travel.run] 同风格）：
  travel.candidates.retrieved / travel.data.unresolved_place /
  travel.itinerary.planned / travel.itinerary.validated /
  travel.itinerary.validation_failed / travel.itinerary.repaired

全部软失败：遥测绝不影响规划主链。
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge

# 候选池检索量（poi 专家每次运行的候选条数；result=retrieved）
travel_candidate_total = Counter(
    "travel_candidate_total",
    "旅游候选池检索条目总数（按 result）",
    labelnames=("result",),
)

# 未能解析的必去地点（unresolved must_go，如实披露的事实）
travel_unresolved_place_total = Counter(
    "travel_unresolved_place_total",
    "未能解析的必去地点总数（status=unresolved）",
    labelnames=("status",),
)

# 校验轮次与结果（每次 validator 执行 +1）
travel_itinerary_validation_total = Counter(
    "travel_itinerary_validation_total",
    "旅游行程约束校验执行次数（status=pass|errors）",
    labelnames=("status",),
)

# 违反码分布（constraint=固定 CODE_* 枚举，低基数）
travel_itinerary_constraint_violation_total = Counter(
    "travel_itinerary_constraint_violation_total",
    "旅游行程约束违反总数（按 constraint）",
    labelnames=("constraint",),
)

# 修复执行（executed=有动作的修复轮 / stalled=无自动手段或无改善）
travel_itinerary_repair_total = Counter(
    "travel_itinerary_repair_total",
    "旅游行程修复轮总数（按 status）",
    labelnames=("status",),
)

# 置信度（0-1，进程内最后值；聚合归因走 trace 的 travel_plan_run span）
travel_itinerary_quality_score = Gauge(
    "travel_itinerary_quality_score",
    "旅游行程置信度（数据完备度×校验通过度，非模型自评）",
)


def record_candidates(count: int) -> None:
    """候选池检索量（软失败）。"""
    try:
        travel_candidate_total.labels(result="retrieved").inc(max(0, count))
    except Exception:  # noqa: BLE001
        pass


def record_unresolved(count: int) -> None:
    """unresolved 必去地点（软失败）。"""
    try:
        if count > 0:
            travel_unresolved_place_total.labels(status="unresolved").inc(count)
    except Exception:  # noqa: BLE001
        pass


def record_validation(error_count: int, violation_codes: list[str]) -> None:
    """一次校验的轮次/违反分布（软失败）。"""
    try:
        travel_itinerary_validation_total.labels(
            status="pass" if error_count == 0 else "errors").inc()
        for code in violation_codes:
            travel_itinerary_constraint_violation_total.labels(
                constraint=code).inc()
    except Exception:  # noqa: BLE001
        pass


def record_repair(status: str) -> None:
    """修复轮计数（status: executed | stalled；软失败）。"""
    try:
        travel_itinerary_repair_total.labels(status=status).inc()
    except Exception:  # noqa: BLE001
        pass


def record_confidence(score: float | None) -> None:
    """置信度 gauge（软失败；None 不打）。"""
    if score is None:
        return
    try:
        travel_itinerary_quality_score.set(float(score))
    except Exception:  # noqa: BLE001
        pass


def event(name: str, **fields) -> None:
    """结构化事件行（INFO 级；事件名稳定，字段低基数）。

    形如 ``[travel.quality] event=travel.itinerary.validated errors=0 ...``
    与 ``[travel.run] event=...``（STOP F/G）同一检索风格。
    """
    try:
        from backend.shared.logger import logger

        rendered = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info("[travel.quality] event=%s %s", name, rendered)
    except Exception:  # noqa: BLE001
        pass

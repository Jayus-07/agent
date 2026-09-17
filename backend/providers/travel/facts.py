"""providers/travel/facts.py — 数据事实的时效语义（Phase 1）

任务书 §3 的核心诉求：**LLM 永远不决定事实，事实必须自带"何时观测、是否核实"
的元数据**。本模块定义 Provider 层产出事实所附带的时效字段语义：

- ``observed_at``        — 这条事实何时被观测（ISO 8601，UTC 带时区）
- ``verification_status`` — verified（已核实）/ unverified（占位或未核实）
- ``traffic_aware``      — 通勤时长是否来自真实路况
- ``is_estimate``        — 数值是否为估算（保守默认：未标注即视为估算）
- ``fallback_reason``    — 为什么降级到估算（用户可见的降级说明）

这些字段以**向后兼容默认值**落到域模型（Poi / TransitLeg），Provider 层负责
在产出事实时打上准确标注 —— 「用占位数据排行程再一本正经地校验」的自欺链路
从源头切断：占位事实永远带着 unverified 标记走完全链路，reporter 分维度披露。
"""
from __future__ import annotations

from datetime import date, timedelta, timezone
import datetime as _dt

# verification_status 取值
VERIFIED = "verified"      # 事实经过人工整理或有权威来源（本地种子数据）
UNVERIFIED = "unverified"  # 占位值 / 未核实 —— reporter 必须明示

# 远期出行日期的通勤降级原因标识
FAR_TRIP_FALLBACK_REASON = "trip_date_beyond_horizon"

# 默认远期阈值（天）：出行日期距今超过该天数时，当日实时路况不再可信
# （9 月排元旦行程套用 9 月路况是隐性伪事实）。可由 TRAVEL_TRANSIT_FAR_TRIP_DAYS 覆盖。
DEFAULT_FAR_TRIP_DAYS = 14


def now_iso() -> str:
    """事实观测时间戳（UTC ISO 8601，带时区，跨进程可比）。"""
    return _dt.datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_far_trip(trip_date: date | None, horizon_days: int = DEFAULT_FAR_TRIP_DAYS) -> bool:
    """出行日期是否超出实时数据的可信窗口。

    None（未提供出发日期）不算远期 —— 维持现状行为，实时数据照常可用，
    由 reporter 的「未指定出发日期」提示兜底。
    """
    if trip_date is None:
        return False
    today = _dt.date.today()
    return trip_date > today + timedelta(days=horizon_days)

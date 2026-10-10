"""travel/core/plan_lifecycle.py — TravelPlan 生命周期状态机（backend/travel/core/plan_lifecycle.py 冻结）

生命周期挂在 plan 实体（trip_id）上，与图 stage（单次运行执行位置）正交：
stage 管本轮执行到哪，plan_status 管这个计划处于什么阶段——「修改第三天
酒店」必须由状态而非对话推断裁决。纯规则零 LLM 零 IO；非法迁移 fail-fast
（不允许跳状态）。

TRAVELING/COMPLETED/ARCHIVED 为保留态：枚举与迁移表本期冻结，激活依赖
行程时钟基础设施，属后续立项（BLOCKED 纪律，不许假装可用）。
CANCEL（取消当前运行）不是生命周期终态：plan 与 status_history 保留，可续聊。
"""
from __future__ import annotations

import enum
from datetime import datetime


class TravelPlanStatus(str, enum.Enum):
    DRAFT = "draft"
    COLLECTING_REQUIREMENTS = "collecting_requirements"
    RESEARCHING = "researching"
    PLANNING = "planning"
    OPTIMIZING = "optimizing"
    WAITING_CONFIRMATION = "waiting_confirmation"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"
    TRAVELING = "traveling"
    COMPLETED = "completed"
    ARCHIVED = "archived"


# 保留态（本期不激活，见模块 docstring）。
RESERVED_STATUSES: frozenset[TravelPlanStatus] = frozenset({
    TravelPlanStatus.TRAVELING,
    TravelPlanStatus.COMPLETED,
    TravelPlanStatus.ARCHIVED,
})

# 合法迁移表（backend/travel/core/plan_lifecycle.py 冻结）。键 = 当前态，值 = 允许迁往的目标态集合；
# 自环（COLLECTING_REQUIREMENTS 追问轮）显式列出，不计违规。
LEGAL_TRANSITIONS: dict[TravelPlanStatus, frozenset[TravelPlanStatus]] = {
    TravelPlanStatus.DRAFT: frozenset({TravelPlanStatus.COLLECTING_REQUIREMENTS}),
    TravelPlanStatus.COLLECTING_REQUIREMENTS: frozenset({
        TravelPlanStatus.COLLECTING_REQUIREMENTS,
        TravelPlanStatus.RESEARCHING,
    }),
    TravelPlanStatus.RESEARCHING: frozenset({TravelPlanStatus.PLANNING}),
    TravelPlanStatus.PLANNING: frozenset({TravelPlanStatus.OPTIMIZING}),
    TravelPlanStatus.OPTIMIZING: frozenset({TravelPlanStatus.WAITING_CONFIRMATION}),
    TravelPlanStatus.WAITING_CONFIRMATION: frozenset({
        TravelPlanStatus.CONFIRMED,
        TravelPlanStatus.DISCARDED,
        # 用户修改：brief 槽变 → 重走 RESEARCHING；仅行程级调整 → PLANNING；
        # 仅排程/换点类 → OPTIMIZING。
        TravelPlanStatus.RESEARCHING,
        TravelPlanStatus.PLANNING,
        TravelPlanStatus.OPTIMIZING,
    }),
    TravelPlanStatus.CONFIRMED: frozenset({
        # 对已确认计划修改：出新版本后重新待确认（confirmed_version 仍指旧版）。
        TravelPlanStatus.WAITING_CONFIRMATION,
        TravelPlanStatus.TRAVELING,  # 保留态
    }),
    TravelPlanStatus.TRAVELING: frozenset({TravelPlanStatus.COMPLETED}),
    TravelPlanStatus.COMPLETED: frozenset({TravelPlanStatus.ARCHIVED}),
    TravelPlanStatus.ARCHIVED: frozenset(),
    TravelPlanStatus.DISCARDED: frozenset(),
}


class IllegalPlanTransition(ValueError):
    """非法生命周期迁移（fail-fast，调用方不得吞掉）。"""


def transition(current: TravelPlanStatus, target: TravelPlanStatus) -> TravelPlanStatus:
    """校验并返回目标状态；非法迁移抛 IllegalPlanTransition。"""
    if target not in LEGAL_TRANSITIONS[current]:
        raise IllegalPlanTransition(
            f"非法生命周期迁移 {current.value} → {target.value}"
            f"（合法目标：{sorted(s.value for s in LEGAL_TRANSITIONS[current])}）"
        )
    return target


def history_entry(
    current: TravelPlanStatus,
    target: TravelPlanStatus,
    event: str,
    reason: str = "",
    at: datetime | None = None,
) -> dict:
    """构造 status_history 条目（checkpoint dict 进 dict 出纪律）。

    at 参数供测试/调用方注入时钟（只 mock 外部边界），缺省取当前时区时间。
    """
    return {
        "from": current.value,
        "to": target.value,
        "at": (at or datetime.now().astimezone()).isoformat(),
        "event": event,
        "reason": reason,
    }

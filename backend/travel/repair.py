"""travel/repair.py — 局部修复器

与 validator 分工：validator 只判定「哪里不成立」，repair 只决定「拿掉什么」。
刻意不合并成一个模块 —— 判定口径要稳定可测（用于统计真实违反率），
修复策略要能随时调整（P1 会从「丢弃」升级为「换序/替换同类点位」），
两者的演进节奏不同。

**局部**的含义：只处理被点名的天与被点名的条目，不整条重规划。
一份 5 天行程因为第 2 天排不下一个馆而重算全部 5 天，既慢又会让用户
发现「我第 4 天怎么变了」—— 与设计目标里的增量修复一致。

一条硬规矩：**用户点名必去的条目永不被静默丢弃**。
命中违规时保留该条目，并在修复日志里记为 kept_required，
由 reporter 如实告知「这一处排不下 / 时间紧，需要你决定」。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from backend.config import travel as T
from backend.shared.logger import logger
from backend.tools.travel.cost import estimate_cost
from backend.tools.travel.routing import route_km
from backend.travel.experts.transit import rebuild_days
from backend.travel.models.itinerary import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    KIND_VISIT,
)
from backend.travel.models.validation import (
    CODE_BUDGET_OVER,
    CODE_GEO_FAR_LEG,
    CODE_GEO_SCATTER,
    CODE_PACE_TOO_INTENSE,
    CODE_PACE_TOO_MANY_POIS,
    CODE_TIME_CLOSED,
    CODE_TIME_CLOSED_WEEKDAY,
    CODE_TIME_OVERLAP,
    ValidationReport,
)


@dataclass
class RepairAction:
    """一次修复动作（进 trace / 行程单，让用户知道行程被调整过）"""

    code: str
    day_index: int
    dropped: list[str] = field(default_factory=list)
    kept_required: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "day_index": self.day_index,
                "dropped": self.dropped, "kept_required": self.kept_required,
                "reason": self.reason}


def repair_itinerary(
    itinerary: Itinerary, report: ValidationReport,
) -> tuple[Itinerary | None, list[RepairAction]]:
    """按校验报告移除不可行条目并重排。

    Returns:
        (修复后的行程, 动作列表)；无可修复动作时返回 (None, actions)
        —— 调用方据此判定「该违反无法自动处理」，交给 reporter 如实告知。
    """
    drop: dict[int, set[str]] = defaultdict(set)
    actions: list[RepairAction] = []

    for violation in report.errors:
        # day_index=0 表示全局约束（预算超支），与具体某天无关 —— 不能当成
        # 「找不到这一天」而跳过，否则超预算这类最需要修的违反永远不会被处理
        if violation.day_index == 0:
            action = _plan_global_action(violation, itinerary, drop)
        else:
            day = _find_day(itinerary, violation.day_index)
            if day is None:
                continue
            action = _plan_action(violation, day, itinerary, drop)
        if action is not None:
            actions.append(action)

    if not drop:
        return None, actions

    specs = [
        (day.day_index, day.day_date,
         [i.poi for i in day.items
          if i.kind == KIND_VISIT and i.poi is not None
          and i.poi.poi_id not in drop.get(day.day_index, set())])
        for day in itinerary.days
    ]
    repaired = rebuild_days(itinerary.brief, specs)
    repaired.repair_rounds = itinerary.repair_rounds + 1
    repaired.warnings = list(itinerary.warnings)
    repaired.sources = list(itinerary.sources)
    repaired.cost = estimate_cost(repaired.days, itinerary.brief.party_size)

    logger.info("[TravelRepair] 第 %d 轮修复: actions=%d 天数 %d→%d",
                repaired.repair_rounds, len(actions),
                len(itinerary.days), len(repaired.days))
    return repaired, actions


# ============================================================
# 单条违规 → 一个修复动作
# ============================================================
def _plan_global_action(
    violation, itinerary: Itinerary, drop: dict[int, set[str]],
) -> RepairAction | None:
    """处理 day_index=0 的全局约束（目前只有预算）。"""
    if violation.code == CODE_BUDGET_OVER:
        return _drop_most_expensive(itinerary, drop, violation)
    return None


def _plan_action(
    violation, day: ItineraryDay, itinerary: Itinerary,
    drop: dict[int, set[str]],
) -> RepairAction | None:
    code = violation.code

    if code in (CODE_TIME_CLOSED, CODE_TIME_CLOSED_WEEKDAY):
        pid = violation.detail.get("poi_id", "")
        return _drop_by_id(violation, day, pid, drop,
                           "该时段不可访问（超出开放时间或闭馆日）")

    if code == CODE_TIME_OVERLAP:
        item = _find_item(day, title=violation.detail.get("title", ""))
        if item is None:
            return None
        # 时间重叠本身可能由「前一项占太久」造成，真正的病根是它之前那个到访点
        target = item if item.poi is not None else _previous_visit(day, item)
        if target is None:
            return None
        return _drop_by_id(violation, day, target.poi.poi_id, drop,
                           "与相邻安排时间冲突")

    if code == CODE_GEO_FAR_LEG:
        item = _find_item(day, title=violation.detail.get("to", ""))
        if item is None or item.poi is None:
            return None
        return _drop_by_id(violation, day, item.poi.poi_id, drop,
                           "与当日其他地点距离过远，通勤代价过高")

    if code == CODE_GEO_SCATTER:
        item = _farthest_from_center(day)
        if item is None or item.poi is None:
            return None
        return _drop_by_id(violation, day, item.poi.poi_id, drop,
                           "当日来回折返，在途时间超限")

    if code == CODE_PACE_TOO_MANY_POIS:
        over = int(violation.detail.get("count", 0)) - int(violation.detail.get("limit", 0))
        return _drop_lowest_priority(day, max(1, over), drop, violation,
                                     "超过当日地点数上限")

    if code == CODE_PACE_TOO_INTENSE:
        limit = int(violation.detail.get("limit", 0))
        return _drop_until_minutes(day, limit, drop, violation,
                                   "超过当日活动时长上限")

    return None


# ============================================================
# 动作实现
# ============================================================
def _drop_by_id(
    violation, day: ItineraryDay, poi_id: str, drop: dict[int, set[str]],
    reason: str,
) -> RepairAction | None:
    if not poi_id:
        return None
    item = _find_item(day, poi_id=poi_id)
    if item is None or item.poi is None:
        return None

    if item.poi.required:
        return RepairAction(
            code=violation.code, day_index=day.day_index, dropped=[],
            kept_required=[item.poi.name],
            reason=f"{reason}，但你点名必去，已保留（需你决定是否调整）",
        )

    drop[day.day_index].add(poi_id)
    return RepairAction(code=violation.code, day_index=day.day_index,
                        dropped=[item.poi.name], reason=reason)


def _drop_lowest_priority(
    day: ItineraryDay, count: int, drop: dict[int, set[str]],
    violation, reason: str,
) -> RepairAction | None:
    """按「非必去 → 热度低 → poi_id」顺序移除 count 个条目。"""
    candidates = _droppable(day)
    if not candidates:
        return None
    taken = candidates[:count]
    names = [i.poi.name for i in taken]
    for i in taken:
        drop[day.day_index].add(i.poi.poi_id)
    return RepairAction(code=violation.code, day_index=day.day_index,
                        dropped=names, reason=reason)


def _drop_until_minutes(
    day: ItineraryDay, limit: int, drop: dict[int, set[str]],
    violation, reason: str,
) -> RepairAction | None:
    """优先移除耗时最长的非必去条目，直到活动时长回落到上限内。"""
    candidates = sorted(_droppable(day),
                        key=lambda i: (-i.minutes, -i.poi.rating, i.poi.poi_id))
    names: list[str] = []
    remaining = day.active_minutes
    for item in candidates:
        if remaining <= limit:
            break
        drop[day.day_index].add(item.poi.poi_id)
        names.append(item.poi.name)
        remaining -= item.minutes
    if not names:
        return None
    return RepairAction(code=violation.code, day_index=day.day_index,
                        dropped=names, reason=reason)


def _drop_most_expensive(
    itinerary: Itinerary, drop: dict[int, set[str]], violation,
) -> RepairAction | None:
    """超预算：移除单价最高的非必去收费项目（一次一个，让修复逐轮逼近）。"""
    best: tuple[float, int, ItineraryItem] | None = None
    for day in itinerary.days:
        for item in _droppable(day):
            if item.poi.ticket_cny <= 0:
                continue
            if best is None or item.poi.ticket_cny > best[0]:
                best = (item.poi.ticket_cny, day.day_index, item)
    if best is None:
        return None
    ticket, day_index, item = best
    drop[day_index].add(item.poi.poi_id)
    return RepairAction(
        code=violation.code, day_index=day_index, dropped=[item.poi.name],
        reason=f"超出预算，移除票价最高的非必去项目（¥{ticket:.0f}）",
    )


# ============================================================
# 查询辅助
# ============================================================
def _find_day(itinerary: Itinerary, day_index: int) -> ItineraryDay | None:
    for day in itinerary.days:
        if day.day_index == day_index:
            return day
    return None


def _find_item(
    day: ItineraryDay, poi_id: str = "", title: str = "",
) -> ItineraryItem | None:
    for item in day.items:
        if poi_id and item.poi is not None and item.poi.poi_id == poi_id:
            return item
        if title and item.title == title:
            return item
    return None


def _previous_visit(day: ItineraryDay, item: ItineraryItem) -> ItineraryItem | None:
    """取某条目之前最近的一个到访项。"""
    previous: ItineraryItem | None = None
    for candidate in day.items:
        if candidate is item:
            return previous
        if candidate.poi is not None:
            previous = candidate
    return None


def _droppable(day: ItineraryDay) -> list[ItineraryItem]:
    """可被移除的条目，按优先级从低到高（必去项不在其中）。"""
    items = [i for i in day.items
             if i.kind == KIND_VISIT and i.poi is not None and not i.poi.required]
    items.sort(key=lambda i: (i.poi.rating, i.poi.poi_id))
    return items


def _farthest_from_center(day: ItineraryDay) -> ItineraryItem | None:
    """当日离几何中心最远的到访项（用于缓解折返）。"""
    visits = [i for i in day.items if i.kind == KIND_VISIT and i.poi is not None]
    if len(visits) < 2:
        return None
    center_lat = sum(i.poi.lat for i in visits) / len(visits)
    center_lng = sum(i.poi.lng for i in visits) / len(visits)
    droppable = [i for i in visits if not i.poi.required]
    if not droppable:
        return None
    return max(droppable, key=lambda i: route_km(
        center_lat, center_lng, i.poi.lat, i.poi.lng))


def repair_node(state: dict) -> dict:
    """修复节点：执行一轮局部修复，并清除上一轮校验结果以触发复检。"""
    from backend.travel.graph_state import (
        load_itinerary, load_validation, save_itinerary,
    )

    itinerary = load_itinerary(state)
    report = load_validation(state)
    if itinerary is None or report is None:
        return {"stage": "validate"}

    max_rounds = T.TRAVEL_MAX_REPAIR_ROUNDS
    if itinerary.repair_rounds >= max_rounds:
        # 轮数用尽：不再改动行程，把未解决的违反留给 reporter 如实披露
        return {
            "stage": "report",
            "notes": list(state.get("notes", [])) + [
                f"行程经 {max_rounds} 轮自动调整后仍有约束无法满足，"
                "已在行程单中标注，需要你确认取舍"
            ],
        }

    repaired, actions = repair_itinerary(itinerary, report)
    log = list(state.get("repair_log", [])) + [a.to_dict() for a in actions]

    if repaired is None:
        return {
            "stage": "report",
            "repair_log": log,
            "notes": list(state.get("notes", [])) + [
                "存在无法自动调整的约束冲突（可能因地点均为必去项），"
                "已在行程单中标注"
            ],
        }

    return {
        "itinerary": save_itinerary(repaired),
        "validation": None,          # 清除 → supervisor 触发复检
        "stage": "validate",
        "repair_rounds": repaired.repair_rounds,
        "repair_log": log,
    }

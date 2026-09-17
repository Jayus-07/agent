"""travel/experts/transit.py — 通勤与排程专家

职责：把「哪天去哪些点」变成「几点到、几点走、中间怎么过去」。

两条设计取舍：

1. **排程不判可行性。** 本专家只负责排出真实时刻 —— 即使某个点排到了
   闭馆之后也不悄悄删掉，而是照排并留痕，交给 validator 判为违反、由
   repair 决定去留。若此处也做一次「排不下就丢」，就会形成两个地方
   各自决定「什么算排不下」，校验结果与用户的真实违反率都会失真。

2. **午餐是要占时间的。** 很多行程表把吃饭当成透明项，导致 12:00-13:00
   凭空多出一个小时，时刻表整体偏乐观。此处显式插入用餐项并占用时间轴。
"""
from __future__ import annotations

from datetime import timedelta

from backend.config import travel as T
from backend.shared.logger import logger
from backend.tools.travel.cost import day_cost
from backend.tools.travel.routing import estimate_leg, route_km
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import (
    data_snapshot_version,
    load_brief,
    load_itinerary,
    save_itinerary,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import (
    CHANGE_INITIAL,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    KIND_MEAL,
    KIND_VISIT,
    TransitLeg,
)
from backend.travel.models.poi import Poi
from backend.travel.timeutil import MINUTES_PER_DAY, from_min, to_min

# 单项最短时长：低于此值的到访没有意义（路上时间都比参观久）
_MIN_VISIT_MINUTES = 15


def order_pois(pois: list[Poi], day_start_min: int | None = None) -> list[Poi]:
    """就近串联（最近邻），并挑一个不会让人干等的起点。

    起点选择的关键不是「最热」而是「已经开门」：实测中把 11:00 才营业的
    美食街选作起点，会在门口干等 150 分钟，整条行程前半天空转。因此
    排序键为「已在开放时间内 → 必去 → 热度 → 稳定 id」，随后每次走
    最近的一个。
    """
    if len(pois) <= 1:
        return list(pois)

    day_start = (to_min(T.TRAVEL_DAY_START, 8 * 60 + 30)
                 if day_start_min is None else day_start_min)

    def _start_key(p: Poi) -> tuple:
        open_min = to_min(p.open_time, 0)
        return (open_min > day_start, not p.required, -p.rating, p.poi_id)

    start = min(pois, key=_start_key)
    remaining = [p for p in pois if p.poi_id != start.poi_id]
    ordered = [start]
    current = start
    while remaining:
        nxt = min(remaining, key=lambda p: (
            route_km(current.lat, current.lng, p.lat, p.lng), p.poi_id,
        ))
        ordered.append(nxt)
        remaining.remove(nxt)
        current = nxt
    return ordered


def _meal_item(start_min: int, minutes: int) -> ItineraryItem:
    end_min = min(MINUTES_PER_DAY - 1, start_min + minutes)
    return ItineraryItem(
        title="午餐", kind=KIND_MEAL,
        start=from_min(start_min), end=from_min(end_min),
        minutes=end_min - start_min, poi=None, note="就近用餐",
    )


def schedule_day(
    pois: list[Poi], day_index: int, day_date, brief: TravelBrief,
) -> ItineraryDay:
    """为一天排出时刻表（纯函数，可单测）。

    Args:
        pois: 当天要去的 POI（顺序会被就近重排）
        day_index: 第几天（1-based）
        day_date: 具体日期（可为 None —— 此时无法校验闭馆日，只做提示）
        brief: 需求契约（取人数用于费用）
    """
    day = ItineraryDay(day_index=day_index, day_date=day_date)
    ordered = order_pois(pois)

    cursor = to_min(T.TRAVEL_DAY_START, 8 * 60 + 30)
    lunch_start = to_min(T.TRAVEL_LUNCH_WINDOW[0], 12 * 60)
    lunch_minutes = max(30, to_min(T.TRAVEL_LUNCH_WINDOW[1], 13 * 60) - lunch_start)
    lunch_done = False

    prev_item: ItineraryItem | None = None
    prev_coord: tuple[float, float] | None = None

    def _place_lunch() -> None:
        nonlocal cursor, lunch_done, prev_item
        item = _meal_item(cursor, lunch_minutes)
        day.items.append(item)
        cursor = to_min(item.end, cursor)
        lunch_done = True
        prev_item = item  # 用餐不改变所在位置，prev_coord 保持

    for poi in ordered:
        if not lunch_done and cursor >= lunch_start:
            _place_lunch()

        open_min = to_min(poi.open_time, 0)
        start = max(cursor, open_min)
        wait = start - cursor
        end = start + max(_MIN_VISIT_MINUTES, poi.suggested_minutes)

        notes: list[str] = []
        if wait > 0:
            notes.append(f"{poi.open_time} 才开放，需等候 {wait} 分钟")
        if day_date is None and poi.closed_weekdays:
            notes.append(
                "该点固定闭馆日：" + "、".join(poi.closed_weekday_names())
                + "（未提供出发日期，请按实际日期核对）"
            )
        if end > MINUTES_PER_DAY - 1:
            end = MINUTES_PER_DAY - 1
            notes.append("当日可用时间不足，需另作安排")

        if prev_coord is not None:
            # 透传出行日期：providers/travel 据此执行「远期出行日期强制本地
            # 估算」策略（实时路况对远期日期是伪事实）。编排逻辑不变。
            est = estimate_leg(prev_coord[0], prev_coord[1], poi.lat, poi.lng,
                               trip_date=day_date)
            day.legs.append(TransitLeg(
                from_title=prev_item.title if prev_item else "起点",
                to_title=poi.name, **est,
            ))

        item = ItineraryItem(
            title=poi.name, kind=KIND_VISIT,
            start=from_min(start), end=from_min(end),
            minutes=max(0, end - start), wait_minutes=wait, poi=poi,
            note="；".join(notes),
        )
        day.items.append(item)
        cursor = end
        prev_item = item
        prev_coord = (poi.lat, poi.lng)

    if not lunch_done and day.items and cursor >= lunch_start:
        _place_lunch()

    # 有效活动时长只计到访项：用餐是必需开销不是游玩强度，通勤由地理轴
    # 单独约束。把这两者算进来会掩盖真正的超量，也会让两轴互相包含。
    day.active_minutes = sum(i.minutes for i in day.items if i.kind == KIND_VISIT)
    day.transit_minutes = sum(leg.minutes for leg in day.legs)
    day.cost_cny = day_cost(day, brief.party_size)
    return day


def rebuild_days(
    brief: TravelBrief,
    specs: list[tuple[int, object, list[Poi]]],
) -> Itinerary:
    """按「(天序号, 日期, POI 列表)」重建行程。

    时间轴只有这一份实现：首次排程与修复后重排都走这里。修复时复用原有的
    day_index / day_date，避免「第 3 天」在修复后变成「第 2 天」。
    """
    days = [schedule_day(pois, idx, day_date, brief)
            for idx, day_date, pois in specs if pois]
    return Itinerary(brief=brief, days=days)


def build_itinerary(
    brief: TravelBrief, pois_by_day: list[list[Poi]],
) -> tuple[Itinerary, list[str]]:
    """按骨架排出完整行程。

    空白天会被压缩掉（不产出「第 3 天：无安排」这种无意义条目），
    并把压缩事实写进 notes。
    """
    specs: list[tuple[int, object, list[Poi]]] = []
    for idx, pois in enumerate(pois_by_day, start=1):
        day_date = (brief.start_date + timedelta(days=idx - 1)
                    if brief.start_date else None)
        specs.append((idx, day_date, pois))

    itinerary = rebuild_days(brief, specs)

    notes: list[str] = []
    if len(itinerary.days) < brief.resolved_days():
        notes.append(
            f"受候选地点数量限制，行程由 {brief.resolved_days()} 天压缩为 "
            f"{len(itinerary.days)} 天（空白天已移除）"
        )
    return itinerary, notes


def _prefetch_day_legs(pois_by_day: list[list[Poi]]) -> None:
    """并行预热「同日相邻 POI」的路线（best-effort）。

    2026-09-15 性能优化：排程循环逐段串行调用腾讯路线 API（实测 5 段 ≈5s）。
    先把相邻段并发取回进缓存，串行循环随后直接命中。
    预热顺序取 order_pois 的结果，与 schedule_day 的实际遍历一致；
    午餐点位的插入会产生少量未覆盖段（数量小，可接受）。
    """
    try:
        from backend.tools.travel.live_map import prefetch_legs

        pairs = []
        for pois in pois_by_day:
            ordered = order_pois(list(pois))
            for a, b in zip(ordered, ordered[1:]):
                pairs.append((a.lat, a.lng, b.lat, b.lng))
        if pairs:
            prefetch_legs(pairs)
    except Exception as e:  # noqa: BLE001 — 预热失败不阻塞排程
        logger.debug("[TravelTransit] 路段预热跳过: %s", e)


def transit_expert_node(state: dict) -> dict:
    """通勤专家节点：骨架 → 带时刻的行程。"""

    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        candidates = {p["poi_id"]: Poi.model_validate(p)
                      for p in state.get("candidates", [])}
        day_plan = state.get("day_plan", [])

        if not day_plan:
            return {"status": "failed", "data": {},
                    "notes": [], "error": "骨架为空，无法排程"}

        pois_by_day = [
            [candidates[pid] for pid in day if pid in candidates]
            for day in day_plan
        ]
        _prefetch_day_legs(pois_by_day)
        itinerary, notes = build_itinerary(brief, pois_by_day)
        # 版本章（任务书 §4）：出生即回答「基于哪个需求、哪份数据、为什么产生」。
        # 重规划轮的 change_reason 由 slot_filler 写入 state；候选池签名按
        # state.candidates 全集计算（含未排入项 —— 数据版本不等同于行程内容）。
        itinerary.stamp_version(
            brief,
            reason=state.get("brief_change_reason") or CHANGE_INITIAL,
            data_snapshot=data_snapshot_version(state.get("candidates", [])),
            changed_fields=list(state.get("brief_changed_fields") or []),
        )
        logger.info("[TravelTransit] 排程完成 days=%d legs=%d plan=v%d(brief v%d %s)",
                    len(itinerary.days),
                    sum(len(d.legs) for d in itinerary.days),
                    itinerary.plan_version, itinerary.brief_version,
                    itinerary.change_reason)
        return {"status": "success",
                "data": {"itinerary": save_itinerary(itinerary)},
                "notes": notes}

    result = run_expert_safely("transit", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "transit", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    update: dict = {
        "last_expert_result": dict(result),
        "expert_history": history,
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }
    if data.get("itinerary"):
        update["itinerary"] = data["itinerary"]
    return update


def reschedule_after_repair(state: dict) -> Itinerary | None:
    """修复后重排：保留原有的天序号与日期，仅重算时刻/通勤/费用。"""
    brief = load_brief(state)
    itinerary = load_itinerary(state)
    if itinerary is None:
        return None
    candidates = {p["poi_id"]: Poi.model_validate(p)
                  for p in state.get("candidates", [])}

    specs: list[tuple[int, object, list[Poi]]] = []
    for day in itinerary.days:
        survivors = [candidates[i.poi.poi_id] for i in day.items
                     if i.kind == KIND_VISIT and i.poi is not None
                     and i.poi.poi_id in candidates]
        specs.append((day.day_index, day.day_date, survivors))
    return rebuild_days(brief, specs)

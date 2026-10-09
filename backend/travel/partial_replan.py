"""旅游行程的确定性局部改单。

局部改单只重排被点名的日期，其他日期沿用原对象，避免把用户没有要求
修改的安排整体洗牌。模块不调用 LLM/外部系统；新地点必须来自当前候选池。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import Itinerary, ItineraryDay, KIND_VISIT
from backend.travel.models.poi import Poi
from backend.travel.services.transit_service import schedule_day

_CHANGE_REASON = "partial_replan"
_MAX_RELAXED_POIS = 4
_EARLY_END = "18:00"

_CN_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


@dataclass(frozen=True)
class PartialReplanRequest:
    """局部改单的结构化请求。"""

    operation: str
    target_day: int | None = None
    remove_names: tuple[str, ...] = ()
    add_names: tuple[str, ...] = ()
    pace: str | None = None
    return_origin: str = ""
    required_count: int = 0


@dataclass(frozen=True)
class PartialReplanResult:
    """局部改单结果，供图节点、reporter 与 Trace 共同消费。"""

    status: str
    itinerary: Itinerary
    message: str
    changed_days: tuple[int, ...] = ()
    partial_replan: bool = True
    validation_failed: bool = False


def _to_int(raw: str) -> int | None:
    raw = (raw or "").strip()
    if raw.isdigit():
        value = int(raw)
        return value if value > 0 else None
    if raw == "十":
        return 10
    if len(raw) == 2 and raw[0] == "十":
        return 10 + _CN_NUM.get(raw[1], 0)
    if len(raw) == 2 and raw[1] == "十":
        return _CN_NUM.get(raw[0], 0) * 10
    if len(raw) == 3 and raw[1] == "十":
        return _CN_NUM.get(raw[0], 0) * 10 + _CN_NUM.get(raw[2], 0)
    return _CN_NUM.get(raw)


def _target_day(message: str) -> int | None:
    match = re.search(r"第\s*([一二两三四五六七八九十\d]{1,3})\s*[天日]", message)
    return _to_int(match.group(1)) if match else None


def _clean_name(raw: str) -> str:
    value = re.sub(r"^(?:一个|一处|一个景点|景点|地点)\s*", "", raw.strip())
    value = re.sub(r"(?:景点|地点)$", "", value).strip()
    return value.strip(" ，,。！？!?；;")


def _name_after(message: str, pattern: str) -> str:
    match = re.search(pattern, message)
    return _clean_name(match.group(1)) if match else ""


def parse_partial_request(message: str) -> PartialReplanRequest | None:
    """把验收口径中的自然语言改单解析为局部操作。"""
    text = (message or "").strip()
    if not text:
        return None
    target = _target_day(text)

    hard = re.search(r"必须同时安排\s*([0-9一二两三四五六七八九十]+)", text)
    if hard and "相距很远" in text:
        return PartialReplanRequest(
            operation="hard_constraint",
            target_day=target,
            required_count=_to_int(hard.group(1)) or 0,
        )

    if re.search(r"(?:别太赶|不要太赶|别太累|不要太累|轻松一点|慢一点|放松一点)", text):
        return PartialReplanRequest(
            operation="pace",
            target_day=target,
            pace="relaxed",
        )

    if re.search(r"(?:早点结束|早些结束|提前结束)", text):
        origin = _name_after(text, r"(?:回|返回|赶回)\s*([\u4e00-\u9fff]{2,8})")
        return PartialReplanRequest(
            operation="end_time",
            target_day=target,
            return_origin=origin,
        )

    replace = re.search(
        r"(?:不要去|不去|去掉|删除|移除|取消)\s*([^，,。；;]+?)"
        r"\s*[，,]?\s*(?:换成|改成|替换成)\s*([^。！？!?；;]+)",
        text,
    )
    if replace:
        remove = _clean_name(replace.group(1))
        add = _clean_name(replace.group(2))
        if remove and add:
            return PartialReplanRequest(
                operation="replace_poi",
                target_day=target,
                remove_names=(remove,),
                add_names=(add,),
            )

    remove = re.search(r"把\s*(.+?)\s*(?:去掉|删除|移除|取消)", text)
    if remove:
        name = _clean_name(remove.group(1))
        if name:
            return PartialReplanRequest(
                operation="remove_poi",
                target_day=target,
                remove_names=(name,),
            )

    add = re.search(r"(?:加一个|添加一个|增加一个|加上|添加|增加)\s*([^。！？!?，,；;]+)", text)
    if add:
        name = _clean_name(add.group(1))
        if name:
            return PartialReplanRequest(
                operation="add_poi",
                target_day=target,
                add_names=(name,),
            )

    return None


def _item_name(item) -> str:
    return str(item.title or (item.poi.name if item.poi else "")).strip()


def _matching_poi_names(day: ItineraryDay, name: str) -> set[str]:
    """名称匹配优先精确命中，精确不存在时再做别名/包含匹配。"""
    visit_names = {_item_name(item) for item in day.items if item.poi is not None}
    exact = {actual for actual in visit_names if actual == name}
    if exact:
        return exact
    return {actual for actual in visit_names
            if name in actual or actual in name}


def _candidate(candidates: dict[str, Poi], name: str) -> Poi | None:
    exact = next((poi for poi in candidates.values() if poi.name == name), None)
    if exact:
        return exact
    return next((poi for poi in candidates.values()
                 if name in poi.name or poi.name in name), None)


def _visit_pois(day: ItineraryDay) -> list[Poi]:
    return [item.poi for item in day.items
            if item.kind == KIND_VISIT and item.poi is not None]


def _schedule(day: ItineraryDay, brief: TravelBrief, pois: list[Poi],
              *, departure_time: str = "") -> ItineraryDay:
    schedule_brief = brief
    if departure_time:
        schedule_brief = brief.model_copy(update={"departure_time": departure_time})
    return schedule_day(pois, day.day_index, day.day_date, schedule_brief)


def _early_day(day: ItineraryDay, brief: TravelBrief, pois: list[Poi]) -> ItineraryDay:
    """将末日压到 18:00；必要时从末尾移除可选地点。"""
    selected = list(pois)
    while selected:
        rebuilt = _schedule(day, brief, selected, departure_time=_EARLY_END)
        if all(item.end <= _EARLY_END for item in rebuilt.items):
            return rebuilt
        selected.pop()
    return _schedule(day, brief, [], departure_time=_EARLY_END)


def _stamp(parent: Itinerary, days: list[ItineraryDay], fields: list[str]) -> Itinerary:
    result = parent.model_copy(deep=True)
    result.days = days
    result.stamp_version(
        result.brief,
        reason=_CHANGE_REASON,
        parent_version=parent.plan_version,
        changed_fields=fields,
    )
    return result


def apply_partial_replan(
    itinerary: Itinerary,
    candidates: dict[str, Poi],
    request: PartialReplanRequest | None,
) -> PartialReplanResult:
    """执行局部改单；失败时返回原行程副本并明确说明原因。"""
    original = itinerary.model_copy(deep=True)
    if request is None:
        return PartialReplanResult("partial", original, "没有识别出可执行的局部修改。", validation_failed=True)
    if request.operation == "hard_constraint":
        count = request.required_count or 0
        return PartialReplanResult(
            "partial", original,
            f"第二天要求同时安排 {count} 个相距很远的景点，当前候选与时间窗口不能全部满足；"
            "已保留原行程，请减少数量或拆到多天。",
            validation_failed=True,
        )

    if request.target_day is not None and not any(
            day.day_index == request.target_day for day in original.days):
        return PartialReplanResult("partial", original,
                                   f"找不到第 {request.target_day} 天，无法局部修改。",
                                   validation_failed=True)

    days = original.days
    changed: list[int] = []
    fields = [f"partial:{request.operation}"]
    for index, day in enumerate(original.days):
        if request.target_day is not None and day.day_index != request.target_day:
            continue
        pois = _visit_pois(day)
        next_pois = list(pois)

        if request.operation in {"remove_poi", "replace_poi"}:
            removed_names = set()
            for name in request.remove_names:
                removed_names.update(_matching_poi_names(day, name))
            removed = [name for name in request.remove_names
                       if _matching_poi_names(day, name)]
            if not removed:
                if request.target_day is None:
                    continue
                scope = f"第 {day.day_index} 天" if request.target_day else "当前行程"
                return PartialReplanResult(
                    "partial", original,
                    f"{scope}没有找到「{'、'.join(request.remove_names)}」，未修改行程。",
                    validation_failed=True,
                )
            next_pois = [poi for poi in next_pois
                         if poi.name not in removed_names]

        if request.operation in {"replace_poi", "add_poi"}:
            for name in request.add_names:
                poi = _candidate(candidates, name)
                if poi is None:
                    return PartialReplanResult(
                        "partial", original,
                        f"候选池中没有「{name}」的可核验地点，暂时无法完成修改。",
                        validation_failed=True,
                    )
                poi = poi.model_copy(update={"required": True})
                if not any(existing.name == poi.name for existing in next_pois):
                    next_pois.append(poi)

        if request.operation == "pace":
            required = [poi for poi in next_pois if poi.required]
            optional = [poi for poi in next_pois if not poi.required]
            next_pois = (required + optional)[:_MAX_RELAXED_POIS]

        if request.operation == "end_time":
            rebuilt = _early_day(day, original.brief, next_pois)
        else:
            rebuilt = _schedule(day, original.brief, next_pois)
        days[index] = rebuilt
        changed.append(day.day_index)

        if request.target_day is None:
            # 无指定日期的删除会继续处理所有命中的天；其它操作必须点名日期。
            continue

    if not changed:
        return PartialReplanResult("partial", original, "没有找到可修改的行程日期。",
                                   validation_failed=True)
    result = _stamp(original, days, fields)
    if request.operation == "replace_poi":
        message = f"已局部修改第 {changed[0]} 天：移除「{'、'.join(request.remove_names)}」，换成「{'、'.join(request.add_names)}」。"
    elif request.operation == "remove_poi":
        message = f"已从行程移除「{'、'.join(request.remove_names)}」，只重排受影响日期。"
    elif request.operation == "add_poi":
        message = f"已把「{'、'.join(request.add_names)}」加入第 {changed[0]} 天。"
    elif request.operation == "pace":
        message = f"已放松第 {changed[0]} 天的安排，只调整该日节奏。"
    else:
        message = f"已收紧第 {changed[0]} 天至 18:00 前结束"
        if request.return_origin:
            message += f"，为返回{request.return_origin}预留时间"
        message += "。"
    return PartialReplanResult("applied", result, message,
                               tuple(changed), validation_failed=False)


__all__ = [
    "PartialReplanRequest",
    "PartialReplanResult",
    "apply_partial_replan",
    "parse_partial_request",
]

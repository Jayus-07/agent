"""travel/experts/poi.py — POI 专家

职责单一：把候选池**分配**到各天，形成行程骨架。不排时刻、不算钱。

分配算法（确定性，无 LLM）：
  1. 必去项置顶，其余按热度降序 —— 用户点名的地点必须优先落地
  2. 逐个挑「当前项数最少」的天（先保证各天均衡，避免出现空白天）；
     项数相同时挑「加入后当日地理跨度最小」的天（就近聚类，减少折返）
  3. **同时受两道容量约束**：单日地点数上限（节奏档位）与单日有效活动
     时长上限。实测只卡数量会出现「5 个点合计 600 分钟」的骨架 —— 首版
     必然被校验判为超量、再被修复砍掉一半，等于白排一遍。约束前置后，
     修复才真正用于处理「意外」（如营业时段冲突）而不是收拾自己的烂摊子。
  4. 超过容量的候选进入丢弃清单并如实记录

不做的事：不为了凑满每天而重复同一个地点；候选不够就少排几天，
把「候选池偏小」写进 notes —— 空行程比假行程诚实。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.config import travel as T
from backend.shared.logger import logger
from backend.tools.travel.poi import search_poi
from backend.tools.travel.routing import day_radius_km
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief
from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi

# 候选池上限：足够覆盖 7 天 × intense 档，同时不让状态字典膨胀
_CANDIDATE_LIMIT = 60


@dataclass
class Skeleton:
    """骨架分配结果

    days:    每天分配到的 POI（与 TravelBrief.days 等长，可能含空列表）
    dropped: 因容量上限未排入的 POI 名（如实告知用户，不静默丢弃）
    notes:   面向用户的提示（候选偏少、必去未匹配等）
    """
    days: list[list[Poi]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_skeleton(brief: TravelBrief, candidates: list[Poi]) -> Skeleton:
    """把候选 POI 分配到各天（纯函数，可单测）。"""
    total_days = brief.resolved_days()
    pace = brief.normalized_pace()
    max_pois = T.TRAVEL_PACE_MAX_POIS.get(pace, 5)
    max_minutes = T.TRAVEL_PACE_MINUTES.get(pace, 360)
    skeleton = Skeleton(days=[[] for _ in range(total_days)])

    if not candidates:
        return skeleton

    # 必去优先，其次热度
    ordered = sorted(candidates, key=lambda p: (not p.required, -p.rating, p.poi_id))
    day_minutes = [0] * total_days

    for poi in ordered:
        options = [
            i for i in range(total_days)
            if len(skeleton.days[i]) < max_pois
            and (day_minutes[i] + poi.suggested_minutes <= max_minutes
                 # 单项就超出日预算时允许独占一天（否则长时段项目永远排不进）
                 or not skeleton.days[i])
        ]
        if not options:
            skeleton.dropped.append(poi.name)
            continue
        # 项数均衡优先，地理紧凑次之，索引最后（保证同分时结果稳定）
        chosen = min(options, key=lambda i: (
            len(skeleton.days[i]),
            day_radius_km(_coords(skeleton.days[i]) + [(poi.lat, poi.lng)]),
            i,
        ))
        skeleton.days[chosen].append(poi)
        day_minutes[chosen] += poi.suggested_minutes

    skeleton.notes.extend(_build_notes(brief, skeleton, candidates))
    return skeleton


def _coords(pois: list[Poi]) -> list[tuple[float, float]]:
    return [(p.lat, p.lng) for p in pois]


def _build_notes(
    brief: TravelBrief, skeleton: Skeleton, candidates: list[Poi],
) -> list[str]:
    """把「骨架被迫做的取舍」翻译成用户可读的提示。

    骨架只能记录事实，评价留给 reporter —— 所以这里只描述发生了什么。
    """
    notes: list[str] = []
    total_days = brief.resolved_days()
    planned = sum(len(d) for d in skeleton.days)

    if skeleton.dropped:
        preview = "、".join(skeleton.dropped[:5])
        more = f" 等 {len(skeleton.dropped)} 处" if len(skeleton.dropped) > 5 else ""
        # 注意区分：配置字典的键是英文枚举，展示给用户的是中文标签。
        # 用标签去查字典只会静默落到默认值（数值看着对、换个档位就错）。
        raw_pace = brief.normalized_pace()
        label = brief.pace_label()
        upshift = "「紧凑」" if raw_pace != "intense" else "「轻松」"
        notes.append(
            f"按{label}节奏每天最多 "
            f"{T.TRAVEL_PACE_MAX_POIS.get(raw_pace, 5)} 个地点 / "
            f"{T.TRAVEL_PACE_MINUTES.get(raw_pace, 360)} 分钟活动，"
            f"以下候选未排入：{preview}{more}。想调整松紧可说改为{upshift}节奏。"
        )

    if any(not d for d in skeleton.days):
        notes.append(
            f"当前候选数据只能填满 {total_days - sum(1 for d in skeleton.days if not d)} "
            f"天（共 {planned} 个地点），行程偏松；"
            f"补充候选或放开偏好条件后可排得更满"
        )

    unresolved = [
        want for want in brief.must_go
        if not any(want and (want in p.name or p.name in want) for p in candidates)
    ]
    if unresolved:
        notes.append(
            "以下必去地点在当前候选数据中未匹配到，未能排入："
            + "、".join(unresolved)
        )
    return notes


def poi_expert_node(state: dict) -> dict:
    """POI 专家节点：检索候选池 + 生成行程骨架。"""
    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        candidates = search_poi(
            city=brief.destination,
            preferences=brief.preferences,
            avoid=brief.avoid,
            must_go=brief.must_go,
            limit=_CANDIDATE_LIMIT,
        )

        # 用户点名要去、但本地候选池没有的地点，用腾讯位置服务补全坐标后入池。
        # 放在这里而非 search_poi 内部：补全需要网络，而 search_poi 是纯函数，
        # 保持它可离线单测的价值高于把它做成一个会发请求的函数。
        extra_notes: list[str] = []
        if brief.must_go:
            from backend.tools.travel import live_map

            if live_map.is_enabled():
                added, extra_notes = live_map.resolve_missing_places(
                    brief.destination, candidates, brief.must_go,
                )
                if added:
                    candidates = candidates + added

        if not candidates:
            logger.info("[TravelPOI] 无候选: destination=%r", brief.destination)
            return {
                "status": "success",
                "data": {"candidates": [], "day_plan": [], "dropped": []},
                "notes": extra_notes + [f"暂时没有「{brief.destination}」的地点数据"],
            }

        skeleton = build_skeleton(brief, candidates)
        return {
            "status": "success",
            "data": {
                "candidates": [p.model_dump() for p in candidates],
                "day_plan": [[p.poi_id for p in day] for day in skeleton.days],
                "dropped": skeleton.dropped,
            },
            "notes": extra_notes + skeleton.notes,
        }

    result = run_expert_safely("poi", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "poi", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    return {
        "last_expert_result": dict(result),
        "expert_history": history,
        "candidates": data.get("candidates", []),
        "day_plan": data.get("day_plan", []),
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }

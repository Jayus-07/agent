"""travel/planning.py — must_go 解析契约与候选池成员不变式（STOP I1）

任务书 §16/§17 的落点：用户点名的必去地点只有两种合法结局——
  resolved   在候选池中匹配到 canonical POI（必须排入，validator 守护）
  unresolved 候选池确认没有（如实告知「未能确认该地点的数据，暂未自动安排」，
             禁止伪造同名条目充数）
判定口径此前散在三处（poi 专家 notes、validator coverage、评测脚本），
各自实现「子串互含」匹配——口径漂移的典型温床。本模块是唯一事实源：
匹配函数、三态返回、面向用户的文案都在这里，其他地方只消费。

纯函数零 IO：candidates 是 Poi 列表（域图内已是 dict 形态的经 graph_state
转换），不做任何检索调用。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi

# unresolved 地点对用户的标准话术（reporter / notes 共用，避免文案漂移）
UNRESOLVED_NOTICE = (
    "以下必去地点未能确认其数据（当前候选数据中不存在），暂未自动安排：{names}"
)


def names_match(poi_name: str, wanted: str) -> bool:
    """POI 名与用户给定名称的宽松匹配（单一事实源）。

    双向子串包含：用户常说简称（「厦大」→「厦门大学」不成立但
    「三坊七巷」⊂「上下杭历史文化街区」不存在；主要覆盖「鼓浪屿」vs
    「鼓浪屿风景区」这类前后缀差异）。与 validator coverage 历史口径一致。
    """
    a = (poi_name or "").strip()
    b = (wanted or "").strip()
    if not a or not b:
        return False
    return b in a or a in b


@dataclass
class MustGoResolution:
    """must_go 三态解析结果（scheduled 由骨架产出后另行判定）"""

    resolved: list[str] = field(default_factory=list)
    """用户点名且候选池可匹配的地点（用户原话，保持顺序）"""
    unresolved: list[str] = field(default_factory=list)
    """候选池确认没有的地点（如实披露，绝不伪造条目）"""

    @property
    def all_resolvable(self) -> list[str]:
        return list(self.resolved)


def resolve_must_go(
    brief: TravelBrief, candidates: list[Poi],
) -> MustGoResolution:
    """把 brief.must_go 按候选池解析为 resolved / unresolved 两态。

    Args:
        brief: 需求契约（must_go 为用户点名原话列表）
        candidates: 本轮候选池（avoid 硬排除之后、打分排序之前的完整池）

    Returns:
        MustGoResolution；must_go 为空时两态皆空。
    """
    if not brief.must_go:
        return MustGoResolution()
    resolved: list[str] = []
    unresolved: list[str] = []
    for want in brief.must_go:
        needle = (want or "").strip()
        if not needle:
            continue
        if any(names_match(p.name, needle) for p in candidates):
            resolved.append(want)
        else:
            unresolved.append(want)
    return MustGoResolution(resolved=resolved, unresolved=unresolved)


def scheduled_must_go(brief: TravelBrief, itinerary) -> MustGoResolution:
    """按已生成的行程判定 must_go 排入情况（金标 Q2 与 validator coverage 同口径）。

    itinerary 为 Itinerary（或 None）。返回三态中的 scheduled / missing：
      resolved   = 行程里匹配到的用户原话
      unresolved = 行程里没有的（含候选池就没有的与漏排的——上层结合
                   resolve_must_go 区分「数据没有」与「排程漏了」）
    """
    present = [p.name for p in itinerary.all_pois()] if itinerary is not None else []
    resolved: list[str] = []
    unresolved: list[str] = []
    for want in brief.must_go:
        needle = (want or "").strip()
        if not needle:
            continue
        if any(names_match(name, needle) for name in present):
            resolved.append(want)
        else:
            unresolved.append(want)
    return MustGoResolution(resolved=resolved, unresolved=unresolved)

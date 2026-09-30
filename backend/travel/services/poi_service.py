"""travel/services/poi_service.py — POI 服务（Phase 3 Commit A）

算法真身自 experts/poi.py 逐字迁入（骨架分配零改动）。本模块同时承载
候选检索与必去项 Provider 补全（V1 收敛：调用点自 experts/poi 节点迁入）——
**service 层是唯二允许触达 Provider facade 的位置之一**（另一处
weather_service.fetch_forecast），Phase 4 ProviderRouter 的既定插入点。

不做的事（沿 experts/poi 边界）：不排时刻、不算钱、不为凑满每天重复地点。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.config import travel as T
from backend.tools.travel.poi import search_poi
from backend.tools.travel.routing import day_radius_km
from backend.travel.core.contracts import SourceType
from backend.travel.core.evidence_utils import evidence_to_dict, make_evidence, parse_iso
from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.planning import resolve_must_go

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


def retrieve_candidates(brief: TravelBrief) -> tuple[list[Poi], list[str]]:
    """候选池检索（search_poi 纯函数）+ 必去项 Provider 补全。

    用户点名要去、但本地候选池没有的地点，经 Provider 层（STOP J4：
    共享缓存/3s 预算/坐标与 id 校验/quota 软预算）用腾讯位置服务补全。
    补全需要网络，而 search_poi 是纯函数——两者分开保持检索可离线单测。

    Returns:
        (候选 POI 列表, 补全产生的提示 notes)
    """
    candidates = search_poi(
        city=brief.destination,
        preferences=brief.preferences,
        avoid=brief.avoid,
        must_go=brief.must_go,
        limit=_CANDIDATE_LIMIT,
    )

    extra_notes: list[str] = []
    if brief.must_go:
        from backend.providers.travel.live import get_place_provider

        provider = get_place_provider()
        if provider.is_enabled():
            from backend.providers.travel.live.tencent import (
                resolve_missing_places,
            )

            added, extra_notes = resolve_missing_places(
                brief.destination, candidates, brief.must_go,
            )
            if added:
                candidates = candidates + added
    return candidates, extra_notes


def build_candidate_evidences(candidates: list[Poi]) -> dict[str, dict]:
    """候选池证据表（v4 §4，Phase 4；纯函数派生，检索逻辑零改动）。

    - 种子 POI → SEED/0.5/expire_at=None（坐标/营业时间/票价是自声明示例值，
      诚实标注且不冒充时效）；
    - 外部补全 POI → LIVE/0.95/verified_at=observed_at（坐标权威性归
      Evidence；营业时间/票价的占位语义归 verification_status 字段，
      由 validator POI_UNVERIFIED 消费——字段级真相不并入证据单值）。
    """
    evidences: dict[str, dict] = {}
    for poi in candidates:
        value = {"name": poi.name, "verification_status": poi.verification_status}
        if poi.source.startswith("seed"):
            ev = make_evidence(poi.poi_id, value=value, source=poi.source,
                               source_type=SourceType.SEED)
        else:
            ev = make_evidence(poi.poi_id, value=value, source=poi.source,
                               source_type=SourceType.LIVE,
                               verified_at=parse_iso(poi.observed_at))
        evidences[poi.poi_id] = evidence_to_dict(ev)
    return evidences


def build_skeleton(brief: TravelBrief, candidates: list[Poi]) -> Skeleton:
    """把候选 POI 分配到各天（纯函数，可单测）。

    分配算法（确定性，无 LLM）：
      1. 必去项置顶，其余按热度降序 —— 用户点名的地点必须优先落地
      2. 逐个挑「当前项数最少」的天（先保证各天均衡，避免出现空白天）；
         项数相同时挑「加入后当日地理跨度最小」的天（就近聚类，减少折返）
      3. **同时受两道容量约束**：单日地点数上限（节奏档位）与单日有效活动
         时长上限。实测只卡数量会出现「5 个点合计 600 分钟」的骨架。
      4. 超过容量的候选进入丢弃清单并如实记录
    """
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

    unresolved = resolve_must_go(brief, candidates).unresolved
    if unresolved:
        notes.append(
            "以下必去地点在当前候选数据中未匹配到，未能排入："
            + "、".join(unresolved)
        )
    return notes

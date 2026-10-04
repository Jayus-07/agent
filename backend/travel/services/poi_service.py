"""travel/services/poi_service.py — POI 服务（Phase 3 Commit A）

算法真身自 experts/poi.py 逐字迁入（骨架分配零改动）。本模块同时承载
候选检索与必去项 Provider 补全（V1 收敛：调用点自 experts/poi 节点迁入）——
**service 层是唯二允许触达 Provider facade 的位置之一**（另一处
weather_service.fetch_forecast），Phase 4 ProviderRouter 的既定插入点。

不做的事（沿 experts/poi 边界）：不排时刻、不算钱、不为凑满每天重复地点。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.config import travel as T
from backend.shared.logger import logger
from backend.tools.travel.live_map import map_category, stable_fallback_id
from backend.tools.travel.poi import search_poi
from backend.tools.travel.routing import day_radius_km
from backend.travel.core.contracts import SourceType
from backend.travel.core.evidence_utils import evidence_to_dict, make_evidence, parse_iso
from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi, is_pure_meal, is_seed_source
from backend.travel.planning import names_match, resolve_must_go
from backend.travel.services import live_search_service

# 候选池上限（A2 扩容，2026-10-04：60→120）：搜索面放宽是「用户有得选」
# 的前提；120 条 Poi dict 约 36KB，checkpoint 体积可控
_CANDIDATE_LIMIT = 120

# ── 候选池实时源（TRAVEL_POI_SOURCE=live，2026-10-02 种子库下线）────
# 腾讯位置服务关键词检索：任意城市可用、零维护。诚实口径（与
# live_map.resolve_place 一致）：坐标可信（tencent:lbs + 观测时间），
# 停留时长统一 120 分钟占位、门票无来源 → unverified，由 validator/
# reporter 如实标注；评分由 A1 高德源并入。

# 2026-10-03：「美食」不再映射地点检索词——此前偏好带「美食」（口语「想吃」
# 极易命中）时候选池检索词只剩「美食」，LBS 返回的全是餐厅，行程被挤成
# 「全是吃的」。行程地点=游玩景点；美食诉求改走高德商户卡 + 知乎美食攻略
# （experts/poi 旁路），不进行程候选池。
#
# A2（2026-10-04）：每标签单词 → 双词两组（各自独立检索，合并去重）——
# 单个复合词一次检索只吃到一个切面（「公园 风景名胜」搜不到游乐园），
# 两路互补扩大覆盖；词表外偏好（温泉/citywalk 等）由第四批理解层 LLM
# 产出检索词承接，标签层不硬扩。
_LIVE_QUERIES_BY_PREF = {
    "自然": ("公园 风景名胜", "度假区 湖"),
    "人文": ("博物馆 名胜古迹", "历史街区 寺庙"),
    "亲子": ("游乐园 动植物园", "亲子乐园 科技馆"),
    "购物": ("购物中心 商业街", "步行街 市集"),
    "夜生活": ("夜市", "酒吧街 夜景"),
    "摄影": ("风景区", "观景台 老街"),
}
# 无景点类偏好时的兜底检索词：两类覆盖面最宽的通用词（保证候选池永远有景点）
_LIVE_DEFAULT_QUERIES = ("风景名胜", "博物馆")
_LIVE_PAGE_SIZE = 20
_LIVE_MAX_QUERIES = 4


def _live_pref_queries(preferences: list[str]) -> list[str]:
    """偏好标签 → LBS 检索词（每标签双词，去重后最多 _LIVE_MAX_QUERIES 路；
    无景点类偏好用兜底词）。"""
    queries: list[str] = []
    for pref in preferences:
        for q in _LIVE_QUERIES_BY_PREF.get(pref, ()):
            if q and q not in queries:
                queries.append(q)
    return queries[:_LIVE_MAX_QUERIES] or list(_LIVE_DEFAULT_QUERIES)


def _build_live_candidates(brief: TravelBrief) -> tuple[list[Poi], list[str]]:
    """实时候选检索：按偏好关键词调 LBS 地点搜索，构造诚实标注的 Poi。

    单类检索失败不拖垮其他类（逐类降级、留痕 notes）；全部失败返回
    空列表，由调用方决定披露或按配置回退种子。

    A1（2026-10-04）：腾讯结果之上并入高德景点类目源——rating/营业时间
    只有高德回（骨架评分排序此前因 rating=0 退化）。合并口径：名字相同
    或「包含关系+坐标 300m 内」视为同一处，用高德版本替换（信息更全）；
    高德单源失败只损失评分（notes 留痕），不损失腾讯候选。
    """
    notes: list[str] = []
    queries = _live_pref_queries(brief.preferences)
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pois: list[Poi] = []
    seen: set[str] = set()
    for q in queries:
        try:
            data = live_search_service.search_places(
                keyword=q, city=brief.destination, page_size=_LIVE_PAGE_SIZE,
            )
        except live_search_service.LiveSearchError as exc:
            logger.warning("[PoiService] 实时候选检索 %s 失败: %s", q, exc)
            notes.append(f"「{q}」实时检索失败，该类候选缺失")
            continue
        for item in data.get("pois") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            lat, lng = item.get("lat"), item.get("lng")
            if not name or not (isinstance(lat, (int, float)) and isinstance(lng, (int, float))):
                continue
            tx_id = str(item.get("id") or "").strip()
            if tx_id:
                pid = f"lbs:{tx_id}"
                key = pid
            else:
                pid = f"lbs:{stable_fallback_id(name, brief.destination)}"
                key = f"{name}|{round(float(lat), 4)}|{round(float(lng), 4)}"
            if key in seen:
                continue
            seen.add(key)
            pois.append(Poi(
                poi_id=pid,
                name=name,
                city=brief.destination,
                category=map_category(str(item.get("category") or "")),
                lat=float(lat),
                lng=float(lng),
                suggested_minutes=120,
                ticket_cny=0.0,
                tags=[],
                rating=0.0,
                source="tencent:lbs",
                observed_at=observed_at,
                verification_status="unverified",
                # 入选理由的基础事实（为什么选它）：来自哪一路检索词；
                # 知乎攻略提及的理由由 poi 专家节点在骨架前追加。
                reason=f"「{q}」实时检索",
                # 坐标级可信独立标注：详情（票价/时长）占位连坐标也不可信，是
                # 两个语义——地图打点按 location_status 判定（字段级拆分）。
                location_status="verified",
            ))
    pois, amap_notes = _merge_amap_candidates(
        brief, queries, pois, observed_at)
    notes.extend(amap_notes)
    pois, local_notes = _merge_local_doc_candidates(brief, pois)
    notes.extend(local_notes)
    return pois, notes


_AMAP_OPEN_HOURS_RE = re.compile(
    r"(\d{1,2}):(\d{2})\s*[-–~至]\s*(\d{1,2}):(\d{2})")
# 「同一处」的坐标容差：同名含包含关系时，300m 内才认合并——防「西湖」
# 吞掉「西湖博物馆」（名字包含但相距数公里的两个地方）。
_SAME_PLACE_METERS = 300


def _amap_open_hours(text: str) -> tuple[str, str] | None:
    """高德今日营业时段串 → (open, close)；解析失败返回 None（占位默认）。

    只取第一段（"09:00-14:00,17:00-22:00" 的午市段）——排程关心的是
    「几点开门」，第一段的开始时刻即开门时刻。
    """
    m = _AMAP_OPEN_HOURS_RE.search(text or "")
    if not m:
        return None
    h1, m1, h2, m2 = m.groups()
    return (f"{int(h1):02d}:{m1}", f"{int(h2):02d}:{m2}")


def _haversine_m_approx(lat1: float, lng1: float,
                        lat2: float, lng2: float) -> float:
    """两点球面距离（米），合并判重用（精度要求低，够区分 300m 量级）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def _same_place(a: str, b: str, lat1: float, lng1: float,
                lat2: float, lng2: float) -> bool:
    """腾讯名 vs 高德名是否同一处：精确同名，或包含关系+坐标 300m 内。"""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if (a in b or b in a) and _haversine_m_approx(lat1, lng1, lat2, lng2) <= _SAME_PLACE_METERS:
        return True
    return False


def _merge_amap_candidates(
    brief: TravelBrief, queries: list[str], pois: list[Poi],
    observed_at: str,
) -> tuple[list[Poi], list[str]]:
    """A1：高德景点类目并入候选池（评分/营业时间源）。

    逐检索词调高德商户 Tool；单源失败降级留痕（腾讯候选不受损）。
    与既有条目「同一处」→ 用高德版本替换（rating/营业时间更全）；
    新地点直接追加。返回 (合并后候选, 披露 notes)。
    """
    if not T.TRAVEL_POI_AMAP_SOURCE_ENABLED:
        return pois, []
    notes: list[str] = []
    for q in queries:
        try:
            data = live_search_service.search_attractions(
                keyword=q, city=brief.destination, page_size=_LIVE_PAGE_SIZE)
        except live_search_service.LiveSearchError as exc:
            logger.warning("[PoiService] 高德评分源 %s 失败: %s", q, exc)
            notes.append(f"「{q}」高德评分源检索失败，该类候选暂无评分")
            continue
        for rec in data.get("merchants") or []:
            if not isinstance(rec, dict):
                continue
            name = str(rec.get("name") or "").strip()
            lat, lng = rec.get("lat"), rec.get("lng")
            if not name or not (isinstance(lat, (int, float))
                                and isinstance(lng, (int, float))
                                and (lat, lng) != (0.0, 0.0)):
                continue
            rating = rec.get("rating")
            rating = float(rating) if isinstance(rating, (int, float)) else 0.0
            hours = _amap_open_hours(str(rec.get("open_time_today") or ""))
            merged = False
            for i, existing in enumerate(pois):
                if _same_place(existing.name, name,
                               existing.lat, existing.lng, float(lat), float(lng)):
                    # 同一处：高德版替换（评分/营业时间更全）；坐标用高德
                    # 自己的（两家官方数据都可信，避免混搭两套坐标）。
                    pois[i] = Poi(
                        poi_id=f"amap:{rec.get('id') or name}",
                        name=name,
                        city=brief.destination,
                        category=map_category(str(rec.get("category") or "")),
                        lat=float(lat), lng=float(lng),
                        open_time=hours[0] if hours else existing.open_time,
                        close_time=hours[1] if hours else existing.close_time,
                        suggested_minutes=existing.suggested_minutes,
                        ticket_cny=0.0,
                        tags=[],
                        rating=rating,
                        source="amap",
                        observed_at=observed_at,
                        verification_status="unverified",
                        reason=f"「{q}」实时检索 · 高德评分 {rating:g}" if rating
                        else f"「{q}」实时检索",
                        location_status="verified",
                    )
                    merged = True
                    break
            if merged:
                continue
            pois.append(Poi(
                poi_id=f"amap:{rec.get('id') or stable_fallback_id(name, brief.destination)}",
                name=name,
                city=brief.destination,
                category=map_category(str(rec.get("category") or "")),
                lat=float(lat), lng=float(lng),
                open_time=hours[0] if hours else "09:00",
                close_time=hours[1] if hours else "17:00",
                suggested_minutes=120,
                ticket_cny=0.0,
                tags=[],
                rating=rating,
                source="amap",
                observed_at=observed_at,
                verification_status="unverified",
                reason=f"「{q}」高德检索 · 评分 {rating:g}" if rating
                else f"「{q}」高德检索",
                location_status="verified",
            ))
    return pois, notes


def _local_doc_attraction_names(destination: str) -> list[str]:
    """travel 知识库 active 文档名 → 本地攻略收录的景点名（软失败→空）。

    爬虫文档命名规范「{城市}-{类目}-{名}.md」天然带结构；只取类目=景点
    且城市与目的地匹配的（城市档/美食档不进候选池）。
    """
    try:
        from backend.rag.indexing.doc_registry_pg import PostgresDocumentRegistry

        docs = PostgresDocumentRegistry().list_by_kb(T.TRAVEL_RAG_KB_ID)
    except Exception:  # noqa: BLE001 — registry 不可用跳过本地源，不挡检索
        logger.debug("[PoiService] 本地攻略文档列表读取失败（跳过本地源）",
                     exc_info=True)
        return []
    prefix = f"{(destination or '').strip()}-景点-"
    names: list[str] = []
    for d in docs:
        file_name = str(d.get("file_name") or "")
        if not file_name.startswith(prefix):
            continue
        stem = file_name[len(prefix):]
        name = re.sub(r"\.md$", "", stem).strip()
        if name:
            names.append(name)
    return names


def _merge_local_doc_candidates(
    brief: TravelBrief, pois: list[Poi],
) -> tuple[list[Poi], list[str]]:
    """A3：本地攻略收录的景点补入候选池（候选池偏少时才启用）。

    为什么设池子阈值：本地源要走腾讯逐名补坐标（每个 ≤3s 预算），池子
    够大时是纯开销；它真正的价值场景是「词表搜不到的冷门景点」。
    复用 must_go 的 resolve_missing_places 通道（共享缓存/坐标校验/
    quota 软预算），补入的 Poi 统一标注「本地攻略收录」。
    """
    if not T.TRAVEL_POI_LOCAL_DOC_ENABLED or len(pois) >= T.TRAVEL_POI_LOCAL_DOC_MIN_POOL:
        return pois, []
    all_names = _local_doc_attraction_names(brief.destination)
    if not all_names:
        return pois, []
    known = [p.name for p in pois]
    wanted = [n for n in all_names
              if not any(names_match(k, n) for k in known)][:T.TRAVEL_POI_LOCAL_DOC_MAX]
    if not wanted:
        return pois, []

    from backend.providers.travel.live.tencent import resolve_missing_places

    added, _provider_notes = resolve_missing_places(
        brief.destination, pois, wanted)
    if not added:
        return pois, []
    tagged = [p.model_copy(update={"reason": "本地攻略收录"}) for p in added]
    return pois + tagged, [
        f"本地攻略补入 {len(tagged)} 个地点：{'、'.join(p.name for p in tagged)}"
    ]


def _seed_candidates(brief: TravelBrief) -> list[Poi]:
    """本地种子候选（legacy 通道：TRAVEL_POI_SOURCE=seed 或显式回退）。"""
    return search_poi(
        city=brief.destination,
        preferences=brief.preferences,
        avoid=brief.avoid,
        must_go=brief.must_go,
        limit=_CANDIDATE_LIMIT,
    )


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
    """候选池检索（TRAVEL_POI_SOURCE: live | seed）+ 必去项 Provider 补全。

    live（默认）：腾讯位置服务关键词实时检索，任意城市可用；坐标可信，
    时长/门票为诚实占位（validator/reporter 披露）。完全失败且
    TRAVEL_POI_FALLBACK_SEED=true 时才回退种子库（显式留痕）。

    用户点名要去、但候选池没有的地点，经 Provider 层（STOP J4：
    共享缓存/3s 预算/坐标与 id 校验/quota 软预算）用腾讯位置服务补全。

    Returns:
        (候选 POI 列表, 补全/降级产生的提示 notes)
    """
    extra_notes: list[str] = []
    if T.TRAVEL_POI_SOURCE == "live":
        candidates, live_notes = _build_live_candidates(brief)
        extra_notes.extend(live_notes)
        candidates = candidates[:_CANDIDATE_LIMIT]
        if not candidates:
            extra_notes.append(
                "实时候选检索无结果（网络或配额原因），行程将偏空；稍后重试可恢复")
            if T.TRAVEL_POI_FALLBACK_SEED:
                candidates = _seed_candidates(brief)
                if candidates:
                    extra_notes.append(
                        "已按配置回退本地种子数据（仅种子城市，非实时，票价/时长未核实）")
    else:
        candidates = _seed_candidates(brief)

    if brief.must_go:
        from backend.providers.travel.live import get_place_provider

        provider = get_place_provider()
        if provider.is_enabled():
            from backend.providers.travel.live.tencent import (
                resolve_missing_places,
            )

            added, must_go_notes = resolve_missing_places(
                brief.destination, candidates, brief.must_go,
            )
            if added:
                candidates = candidates + added
            extra_notes.extend(must_go_notes)
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
        if is_seed_source(poi.source):
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
    # 类别策略（2026-10-03）：**纯餐饮**候选不排入行程——行程时间留给游玩点，
    # 吃喝由美食推荐（高德商户/知乎攻略）承接；用户点名必去的餐厅除外
    # （尊重点名）。两个豁免口径：
    #   - 点名判定用 must_go 原话匹配而非 required 字段——池中已有的必去
    #     地点不会被打 required 标（只有 Provider 补全路径会打）；
    #   - 「美食街/夜市」类是游玩型餐饮区（金标 T-D03/T-G09 的预算超限源
    #     「达明美食街」即此类），不是坐下吃饭的店，保留排入。
    def _is_user_named(poi: Poi) -> bool:
        return any(
            (want or "").strip() and names_match(poi.name, want.strip())
            for want in brief.must_go
        )

    meal_skipped = [
        p for p in ordered
        if is_pure_meal(p) and not (p.required or _is_user_named(p))
    ]
    if meal_skipped:
        skipped_ids = {p.poi_id for p in meal_skipped}
        ordered = [p for p in ordered if p.poi_id not in skipped_ids]
        preview = "、".join(p.name for p in meal_skipped[:5])
        more = f" 等 {len(meal_skipped)} 家" if len(meal_skipped) > 5 else ""
        skeleton.notes.append(
            f"{len(meal_skipped)} 个餐饮类候选不排进行程（吃饭看美食推荐）：{preview}{more}")
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


# ── A4 美食三要素排序（2026-10-04）────────────────────────────
# 拍板口径「就近 + 品类 + 评分综合」。距离/评分为可计算硬事实；
# 「品类对味」是主观映射，机制上做成配置化加权表（TRAVEL_FOOD_
# CATEGORY_BOOSTS，默认空=不启用），词表留给实机调参，不拍脑子写死。
# 综合分 = 6.0 × 评分归一 + 4.0 × 距离衰减（3km 线性归零）：
#   - rating 缺失（高德部分商家无评分）按中性 2.5 计——给 0 会把
#     「没数据」冤枉成「差评」沉底；
#   - 3km 外衰减归零但保留顺序（不是过滤——用户有选择权）。

_FOOD_RATING_WEIGHT = 6.0
_FOOD_DISTANCE_WEIGHT = 4.0
_FOOD_DISTANCE_FALLOFF_M = 3000.0
_FOOD_RATING_NEUTRAL = 2.5


def _food_category_boost(category: str) -> float:
    """品类加权（配置表：{"福建菜": 0.5, ...} 关键词→加分），默认不启用。"""
    text = category or ""
    boost = 0.0
    for keyword, value in T.TRAVEL_FOOD_CATEGORY_BOOSTS.items():
        if keyword in text:
            boost = max(boost, float(value))
    return boost


def _food_score(rating: float | None, distance_m: float | None,
                category: str) -> float:
    """综合分：评分归一 + 距离衰减 + 品类加权（纯函数，可单测）。"""
    base = float(rating) if rating is not None else _FOOD_RATING_NEUTRAL
    score = _FOOD_RATING_WEIGHT * (max(0.0, min(5.0, base)) / 5.0)
    if distance_m is not None:
        falloff = max(0.0, 1.0 - distance_m / _FOOD_DISTANCE_FALLOFF_M)
        score += _FOOD_DISTANCE_WEIGHT * falloff
    return score + _food_category_boost(category)


def rank_food_merchants(food: dict, pois: list[Poi]) -> dict:
    """把美食商户按「就近+品类+评分」重排（纯函数；不改封套结构）。

    pois = 已分配进骨架的景点（质心参照）；空骨架时保持原序（无距离
    参照不硬排）。每个 merchant 附加 `distance_m`（到质心，米，本地
    haversine——高德 v5 城市级检索不回距离）供前端展示「距景点 X m」。
    """
    merchants = (food or {}).get("merchants") or []
    if not merchants or not pois:
        return food
    centroid_lat = sum(p.lat for p in pois) / len(pois)
    centroid_lng = sum(p.lng for p in pois) / len(pois)

    def _key(item: dict) -> float:
        dist = None
        lat, lng = item.get("lat"), item.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            dist = _haversine_m_approx(centroid_lat, centroid_lng,
                                       float(lat), float(lng))
            item["distance_m"] = int(dist)
        return _food_score(
            item.get("rating"), dist, str(item.get("category") or ""))

    ranked = sorted(merchants, key=_key, reverse=True)
    return {**food, "merchants": ranked}

"""travel/services/requirement_service.py — Requirement 支撑服务（Phase 2）

职责边界（冻结）：merge_brief 多轮合并 / 指纹（包装 graph_state 单一事实源，
G2 禁止复制指纹逻辑）/ 版本与变更追踪 / checkpoint 相关辅助。不做抽取
（归 agents/requirement_agent.py）、不碰图节点编排（归 slot_filler.py）。

本模块当前还承载 KNOWN_MAJOR_CITIES 与 filter_city_names 两个共享件
（merge_brief 与 agents 抽取器共用）——**过渡安排，禁止继续向本模块堆积
通用知识资源；Phase 3 Research Agent 阶段统一抽离到数据/名录层。**
"""
from __future__ import annotations

from backend.travel.graph_state import brief_fingerprint
from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import CHANGE_BRIEF

# 「点名但明确不支持」名单（v3 P0-A 收缩，2026-10-02 拍板）：境内主要
# 城市已全部移入 data/cities.py 识别名录（支持范围 = live Provider 能力
# 边界，名录只是识别词表+消歧工具），本名单只剩境外与暂不开放的目的地
# ——点名时明确告知不支持，而不是当成没说过（用户需要知道为什么城市
# 没被接住）。
KNOWN_MAJOR_CITIES: tuple[str, ...] = (
    "纽约", "伦敦", "巴黎", "东京", "大阪", "首尔", "曼谷",
    "新加坡", "吉隆坡", "香港", "澳门",
)


def filter_city_names(names: list[str]) -> list[str]:
    """城市名不是 POI：从必去/避雷清单剔除（agents 与 merge_brief 共用）。

    「我想去北京玩」会让触发词捕获到「北京」；留着它，跨轮后必然产出
    「必去地点未能排入：北京」的假警告（城市进不了候选池是数据覆盖问题，
    不是行程排布问题）。目的地信息走 destination 槽位，不在这里表达。
    """
    city_names = set(KNOWN_MAJOR_CITIES)
    from backend.tools.travel import poi_seed

    from backend.travel.data import cities as city_directory

    city_names |= set(poi_seed.all_cities())
    city_names |= set(city_directory.all_directory_cities())
    return [n for n in names if n not in city_names]


def merge_brief(previous: TravelBrief, fresh: TravelBrief) -> TravelBrief:
    """多轮合并：新抽取到的字段覆盖旧值，未抽到的保留旧值。

    只在「用户这一轮补充了信息」时生效（如追问后回答"3天"），
    不会因为这一轮没提预算就把之前说的预算清空。
    """
    merged = previous.model_copy()
    for field in ("destination", "origin", "lodging"):
        value = getattr(fresh, field)
        if value:
            setattr(merged, field, value)
    for field in ("start_date", "days", "budget_cny",
                  "arrival_time", "departure_time"):
        value = getattr(fresh, field)
        if value is not None and value != "":
            setattr(merged, field, value)
    if fresh.party_size != 1 or previous.party_size == 1:
        if fresh.party_size >= 1:
            merged.party_size = fresh.party_size
    # adults/children（Phase 2）：本轮显式表达才覆盖；party_size 由派生规则
    # 维护（adults 显式时 = adults + children-or-0），保证与派生来源一致。
    if fresh.adults is not None:
        merged.adults = fresh.adults
    if fresh.children is not None:
        merged.children = fresh.children
    if merged.adults is not None:
        merged.party_size = merged.adults + (merged.children or 0)
    if fresh.preferences:
        merged.preferences = list(dict.fromkeys(previous.preferences + fresh.preferences))
    for field in ("must_go", "avoid", "optional_go"):
        combined = list(dict.fromkeys(getattr(previous, field) + getattr(fresh, field)))
        setattr(merged, field, combined)
    # avoid 的语义优先级高于 must_go/optional_go：上一轮的必去被这一轮拉黑
    # 后必须移出（含软清单），否则行程仍会把它排入。
    if merged.avoid:
        for field in ("must_go", "optional_go"):
            names = [
                n for n in getattr(merged, field)
                if not any(n in a or a in n for a in merged.avoid if a)
            ]
            setattr(merged, field, names)
    # must_go 与 optional_go 互斥：必去升级后不留在软清单（分级唯一）
    if merged.must_go:
        merged.optional_go = [
            n for n in merged.optional_go
            if not any(n in m or m in n for m in merged.must_go if m)
        ]
    # 城市名不进必去清单：对「历史脏状态」（旧版本代码写入的 brief）同样
    # 成立，不能只信本轮 fresh 抽取干净。
    merged.must_go = filter_city_names(merged.must_go)
    if fresh.pace != "moderate":
        merged.pace = fresh.pace
    # 方案档位（M3-e）：tier 的显式/缺省区分在 slot_filler 层做——
    # fresh.tier 缺省恒为 economy，无法在此区分「用户说了经济型」与
    # 「没提」；slot_filler 拿得到原话，显式表达时在 merge 后覆盖。
    # 饮食忌口：本轮有表述才覆盖（与 pace 同一合并语义）
    if fresh.diet:
        merged.diet = fresh.diet
    return merged


class RequirementService:
    """Requirement 支撑服务（合并 / 指纹 / 版本与变更追踪）。无状态，可复用。"""

    def merge(self, previous: TravelBrief | None,
              fresh: TravelBrief) -> TravelBrief:
        """多轮合并；无上一轮时原样返回 fresh。"""
        return merge_brief(previous, fresh) if previous else fresh

    def fingerprint(self, brief: TravelBrief) -> str:
        """指纹（graph_state.brief_fingerprint 的包装，单一事实源不复制）。"""
        return brief_fingerprint(brief)

    def detect_brief_change(
        self,
        previous: TravelBrief | None,
        brief: TravelBrief,
        last_fingerprint: str,
    ) -> dict:
        """需求变化检测与版本追踪（原 slot_filler_node 内联逻辑的服务化）。

        返回 {changed, fingerprint, new_version, change_reason,
        changed_fields}：仅在「有上一轮指纹且不同」时判定变化（首次进入
        不算）；变化时 new_version = previous.version + 1，reason =
        CHANGE_BRIEF，changed_fields 为除 version 外的逐字段 diff（排序
        保证确定性）。**不修改 brief**——version 递增由调用方执行（与
        迁移前时序等价：version 不参与 diff 比较）。
        """
        fingerprint = self.fingerprint(brief)
        changed = bool(last_fingerprint) and last_fingerprint != fingerprint
        new_version: int | None = None
        change_reason = ""
        changed_fields: list[str] = []
        if changed and previous is not None:
            new_version = previous.version + 1
            change_reason = CHANGE_BRIEF
            old_dump = previous.model_dump()
            new_dump = brief.model_dump()
            changed_fields = sorted(
                k for k in new_dump
                if k != "version" and old_dump.get(k) != new_dump[k]
            )
        return {
            "changed": changed,
            "fingerprint": fingerprint,
            "new_version": new_version,
            "change_reason": change_reason,
            "changed_fields": changed_fields,
        }

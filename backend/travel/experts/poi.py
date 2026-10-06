"""travel/experts/poi.py — POI 专家节点（Phase 3 改造：节点编排 + 兼容入口）

算法真身已迁 services/poi_service.py（骨架分配/候选检索，逐字搬运零改动）；
本文件保留 LangGraph 节点 `poi_expert_node`（state 读写 / run_expert_safely /
遥测 / expert_history，Phase 3 Node 边界冻结）并 re-export 历史公开符号
（experts/__init__、repair、存量测试零改动）。

Phase 3 Commit B 起节点经 ResearchAgent/PlanningAgent 调用 service
（Node → Agent → Service → Tool/Provider 单向）。
"""
from __future__ import annotations

import concurrent.futures
import contextvars
from typing import Any, Callable

from backend.shared.logger import logger
from backend.travel.core.events import run_travel_tool
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief
from backend.travel.planning import resolve_must_go
from backend.travel.services import live_search_service

# 兼容面 + 节点调用面（本模块命名空间 = 补丁缝）：Agent 封装落地后，
# 能力调用走 Node → Agent → Service；纯数据契约（Skeleton）直通 service
from backend.travel.agents.planning_agent import PlanningAgent
from backend.travel.agents.research_agent import ResearchAgent
from backend.travel.services.poi_service import Skeleton  # noqa: F401

_research = ResearchAgent()
_planning = PlanningAgent()


def _live_queries(message: str, preferences: list[str] | None = None) -> tuple[bool, bool]:
    """商户类实时检索的触发判定。

    food/hotel = 高德商户（结构化）。2026-10-03 起攻略（知乎官方 MCP）
    改为规划主链自动检索（景点/美食/城市特色三主题，见 ResearchAgent.
    search_guides），不再依赖用户消息触发词；「美食」偏好同时自动触发
    美食商户检索——吃的以推荐卡呈现，不进行程候选池。
    """
    text = message or ""
    food = any(token in text for token in (
        "查美食", "查餐厅", "搜美食", "搜餐厅", "附近美食", "实时美食",
    )) or "美食" in (preferences or [])
    hotel = any(token in text for token in (
        "查酒店", "搜酒店", "酒店搜索", "实时酒店",
    ))
    return food, hotel


def _merchant_summary(category: str):
    return lambda value: _research.merchant_event_summary(value, category)


def retrieve_candidates(brief):
    """候选检索（Research 能力）：委托 ResearchAgent。"""
    return _research.retrieve_candidates(brief)


def build_candidate_evidences(candidates):
    """候选池证据表（Research 能力，Phase 4）：委托 ResearchAgent。"""
    return _research.build_candidate_evidences(candidates)


def build_skeleton(brief, candidates):
    """骨架分配（Planning 能力）：委托 PlanningAgent。"""
    return _planning.build_skeleton(brief, candidates)


def poi_expert_node(state: dict) -> dict:
    """POI 专家节点：候选池检索（Research 面）+ 行程骨架（Planning 面）。"""
    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        need_food, need_hotel = _live_queries(
            state.get("user_message", ""), brief.preferences)

        # ── 第一波检索并发（#8，2026-10-04）─────────────────────
        # candidates / guides / hotel 三路互不依赖（food 需要候选池质心
        # 做就近排序，留在 candidates 完成后的第二波单独跑，保住 preview
        # 排序时序）。ContextVar（SSE sink）经 copy_context 快照传播到
        # 工作线程——直接 submit 会丢 tool.started/result 事件；
        # 每路独立降级语义与串行版逐字等价。
        ctx = contextvars.copy_context()
        wave1_jobs: dict[str, Callable[[], Any]] = {
            "candidates": lambda: run_travel_tool(
                "travel.search_poi",
                "research",
                lambda: retrieve_candidates(brief),
                result_summary=lambda value: {
                    "result_count": len(value[0]),
                    "data_status": "available" if value[0] else "empty",
                },
            ),
            "guides": lambda: run_travel_tool(
                "zhihu_search_tool",
                "research",
                lambda: _research.search_guides(brief.destination),
                result_summary=lambda value: _research.guide_event_summary(value),
            ),
        }
        if need_hotel:
            wave1_jobs["hotel"] = lambda: run_travel_tool(
                "map_merchant_search_tool",
                "research",
                lambda: _research.search_hotels(brief.destination),
                result_summary=_merchant_summary("hotel"),
            )
        wave1: dict[str, Any] = {}
        wave1_notes: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(4, len(wave1_jobs))) as pool:
            # 每个 job 独立 copy：同一 Context 对象并发 enter 会报
            # "already entered"（quality_metrics 遥测用例实抓）
            futs = {name: pool.submit(ctx.copy().run, fn)
                    for name, fn in wave1_jobs.items()}
            for name, fut in futs.items():
                try:
                    wave1[name] = fut.result()
                except live_search_service.LiveSearchError as exc:
                    # 单路失败降级留痕（与串行版同语义：不拖垮其他路）
                    logger.warning("[TravelPOI] 并发检索 %s 失败: %s", name, exc)
                    wave1[name] = None
                    wave1_notes.append(
                        f"{'酒店' if name == 'hotel' else name}实时检索不可用（{exc}）")

        candidates, extra_notes_c = wave1["candidates"] or ([], [])
        extra_notes = extra_notes_c + wave1_notes
        # 候选池证据表（Phase 4，v4 §4）：种子=SEED/腾讯补全=LIVE，
        # 纯函数派生自 Poi 字段，随 candidates 一起进 state
        evidences = build_candidate_evidences(candidates)

        if not candidates:
            logger.info("[TravelPOI] 无候选: destination=%r", brief.destination)
            return {
                "status": "success",
                "data": {"candidates": [], "day_plan": [], "dropped": []},
                "notes": extra_notes + [f"暂时没有「{brief.destination}」的地点数据"],
            }

        # 知乎攻略：结果一进 SSE 攻略卡，二做候选「知乎攻略提及」理由
        # 匹配——推荐依据要有出处（检索已在上面的波 1 完成）。
        live_search: dict[str, dict] = {}
        if wave1.get("guides"):
            live_search["guides"] = wave1["guides"]
            mentions = _research.match_guide_mentions(candidates, wave1["guides"])
            if mentions:
                candidates = [
                    p.model_copy(update={
                        "reason": f"{p.reason} · {mentions[p.poi_id]}" if p.reason
                        else mentions[p.poi_id],
                    }) if p.poi_id in mentions else p
                    for p in candidates
                ]
        if wave1.get("hotel"):
            live_search["hotel"] = wave1["hotel"]

        skeleton = build_skeleton(brief, candidates)
        # 预算-only 改动属于约束重算，不应无理由洗牌 POI。若上一版地点仍
        # 在本轮候选池中，沿用其逐日骨架；缺失的天才退回常规骨架。
        stable_days: list[int] = []
        baseline = state.get("plan_stability_baseline") or {}
        changed_fields = set(baseline.get("changed_fields") or [])
        if changed_fields and changed_fields <= {"budget_cny", "budget_constraint"}:
            candidate_map = {poi.poi_id: poi for poi in candidates}
            old_itinerary = baseline.get("itinerary") or {}
            for index, old_day in enumerate(old_itinerary.get("days") or []):
                old_pois = [
                    item.get("poi") for item in (old_day.get("items") or [])
                    if item.get("poi")
                ]
                ids = [str(poi.get("poi_id") or "") for poi in old_pois]
                if (index < len(skeleton.days) and ids
                        and all(poi_id in candidate_map for poi_id in ids)):
                    skeleton.days[index] = [candidate_map[poi_id] for poi_id in ids]
                    stable_days.append(index + 1)
            if stable_days:
                skeleton.notes.append(
                    f"预算变化仅重算费用与可行性，已保留第 {','.join(map(str, stable_days))} 天地点"
                )
        # must_go 三态契约（STOP I1）：resolved/unresolved 在此唯一产生，
        # 金标 Q2（must_go_coverage）与下游披露都消费这里的事实
        resolution = resolve_must_go(brief, candidates)

        if need_food:
            # 第二波：美食检索+就近排序——排序需要骨架质心（波 1 产物），
            # 故在 candidates 完成后跑。A4 排序在 run_travel_tool **内部**
            # （lambda 内）——result_summary 的 preview 在工具完成时生成，
            # 排序放外面会让工具行展示原序（实机 2026-10-04 时序 bug）。
            try:
                from backend.travel.services.poi_service import rank_food_merchants

                scheduled_pois = [p for day in skeleton.days for p in day]
                live_search["food"] = run_travel_tool(
                    "map_merchant_search_tool",
                    "research",
                    lambda: rank_food_merchants(
                        _research.search_food(brief.destination), scheduled_pois),
                    result_summary=_merchant_summary("food"),
                )
            except live_search_service.LiveSearchError as exc:
                extra_notes.append(f"美食商户实时检索不可用（{exc}），本次只有攻略参考")
        if need_hotel:
            try:
                live_search["hotel"] = run_travel_tool(
                    "map_merchant_search_tool",
                    "research",
                    lambda: _research.search_hotels(brief.destination),
                    result_summary=_merchant_summary("hotel"),
                )
            except live_search_service.LiveSearchError as exc:
                extra_notes.append(f"酒店商户实时检索不可用（{exc}）")

        # STOP I6 遥测 + 结构化事件（软失败）
        try:
            from backend.travel import quality_metrics as qm

            qm.record_candidates(len(candidates))
            qm.record_unresolved(len(resolution.unresolved))
            qm.event("travel.candidates.retrieved",
                     destination=brief.destination, count=len(candidates),
                     scheduled=sum(len(d) for d in skeleton.days))
            if resolution.unresolved:
                qm.event("travel.data.unresolved_place",
                         count=len(resolution.unresolved))
        except Exception:  # noqa: BLE001 — 遥测失败不影响规划
            pass

        return {
            "status": "success",
            "data": {
                "candidates": [p.model_dump() for p in candidates],
                "day_plan": [[p.poi_id for p in day] for day in skeleton.days],
                "dropped": skeleton.dropped,
                "must_go_unresolved": resolution.unresolved,
                "evidences": evidences,
                "live_search": live_search,
                "stability_preserved_days": stable_days,
            },
            "notes": extra_notes + skeleton.notes,
        }

    result = run_expert_safely("poi", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "poi", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    update = {
        "last_expert_result": dict(result),
        "expert_history": history,
        "candidates": data.get("candidates", []),
        "day_plan": data.get("day_plan", []),
        "must_go_unresolved": data.get("must_go_unresolved", []),
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }
    if data.get("live_search"):
        update["live_search"] = {
            **(state.get("live_search") or {}),
            **data["live_search"],
        }
    evidences = data.get("evidences") or {}
    if evidences:
        # 合并写入（无 reducer 键是覆盖语义，直接写会冲掉既有证据）
        update["evidences"] = {**state.get("evidences", {}), **evidences}
    return update

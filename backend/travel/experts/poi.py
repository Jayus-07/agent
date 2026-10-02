"""travel/experts/poi.py — POI 专家节点（Phase 3 改造：节点编排 + 兼容入口）

算法真身已迁 services/poi_service.py（骨架分配/候选检索，逐字搬运零改动）；
本文件保留 LangGraph 节点 `poi_expert_node`（state 读写 / run_expert_safely /
遥测 / expert_history，Phase 3 Node 边界冻结）并 re-export 历史公开符号
（experts/__init__、repair、存量测试零改动）。

Phase 3 Commit B 起节点经 ResearchAgent/PlanningAgent 调用 service
（Node → Agent → Service → Tool/Provider 单向）。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.core.events import run_travel_tool
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief
from backend.travel.planning import resolve_must_go

# 兼容面 + 节点调用面（本模块命名空间 = 补丁缝）：Agent 封装落地后，
# 能力调用走 Node → Agent → Service；纯数据契约（Skeleton）直通 service
from backend.travel.agents.planning_agent import PlanningAgent
from backend.travel.agents.research_agent import ResearchAgent
from backend.travel.services.poi_service import Skeleton  # noqa: F401

_research = ResearchAgent()
_planning = PlanningAgent()


def _live_queries(message: str) -> tuple[bool, bool, bool]:
    """只在用户明确要求实时检索时调用外部 Tool。

    food/hotel = 高德商户（结构化）；guide = 知乎官方 MCP 攻略内容
    （知乎经验帖 + 全网文章，2026-10-02 接入）。
    """
    text = message or ""
    food = any(token in text for token in (
        "查美食", "查餐厅", "搜美食", "搜餐厅", "附近美食", "实时美食",
    ))
    hotel = any(token in text for token in (
        "查酒店", "搜酒店", "酒店搜索", "实时酒店",
    ))
    guide = any(token in text for token in (
        "攻略", "必吃", "小吃", "特色美食", "美食推荐", "值得吃", "美食指南",
    ))
    return food, hotel, guide


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
        candidates, extra_notes = run_travel_tool(
            "travel.search_poi",
            "research",
            lambda: retrieve_candidates(brief),
            result_summary=lambda value: {
                "result_count": len(value[0]),
                "data_status": "available" if value[0] else "empty",
            },
        )
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

        skeleton = build_skeleton(brief, candidates)
        # must_go 三态契约（STOP I1）：resolved/unresolved 在此唯一产生，
        # 金标 Q2（must_go_coverage）与下游披露都消费这里的事实
        resolution = resolve_must_go(brief, candidates)

        live_search: dict[str, dict] = {}
        need_food, need_hotel, need_guide = _live_queries(state.get("user_message", ""))
        if need_food:
            live_search["food"] = run_travel_tool(
                "map_merchant_search_tool",
                "research",
                lambda: _research.search_food(brief.destination),
                result_summary=_merchant_summary("food"),
            )
        if need_hotel:
            live_search["hotel"] = run_travel_tool(
                "map_merchant_search_tool",
                "research",
                lambda: _research.search_hotels(brief.destination),
                result_summary=_merchant_summary("hotel"),
            )
        if need_guide:
            # 攻略检索（知乎官方 MCP）：Agent 层已做单路降级，此处不再上抛
            live_search["guides"] = run_travel_tool(
                "zhihu_search_tool",
                "research",
                lambda: _research.search_guides(brief.destination),
                result_summary=lambda value: _research.guide_event_summary(value),
            )

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

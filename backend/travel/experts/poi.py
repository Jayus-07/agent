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
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief
from backend.travel.planning import resolve_must_go

# 兼容面 + 节点调用面（本模块命名空间 = 补丁缝）：算法真身在
# services/poi_service.py，Phase 3 Commit B 起裸名改委托 Agent
from backend.travel.services.poi_service import (  # noqa: F401
    Skeleton,
    build_skeleton,
    retrieve_candidates,
)


def poi_expert_node(state: dict) -> dict:
    """POI 专家节点：候选池检索（Research 面）+ 行程骨架（Planning 面）。"""
    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        candidates, extra_notes = retrieve_candidates(brief)

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
        "must_go_unresolved": data.get("must_go_unresolved", []),
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }

"""旅游域局部改单节点：状态适配与纯函数内核之间的薄边界。"""
from __future__ import annotations

from backend.travel.graph_state import load_itinerary, save_itinerary
from backend.travel.models.poi import Poi
from backend.travel.models.validation import (
    LEVEL_ERROR,
    ValidationReport,
    Violation,
)
from backend.travel.partial_replan import (
    PartialReplanRequest,
    apply_partial_replan,
)


def partial_replan_node(state: dict) -> dict:
    """只执行被点名日期的局部改单，不重新检索或重排其它日期。"""
    itinerary = load_itinerary(state)
    raw_request = state.get("partial_replan") or {}
    if itinerary is None:
        return {
            "partial_replan_done": True,
            "partial_replan_result": {
                "status": "partial",
                "message": "当前没有可修改的已生成行程。",
                "partial_replan": True,
                "validation_failed": True,
            },
            "validation": ValidationReport(
                violations=[Violation(
                    code="PARTIAL_REPLAN_UNSATISFIED",
                    level=LEVEL_ERROR,
                    message="当前没有可修改的已生成行程。",
                )],
            ).model_dump(),
        }

    request = PartialReplanRequest(**raw_request)
    candidates = {
        value["poi_id"]: Poi.model_validate(value)
        for value in (state.get("candidates") or [])
        if isinstance(value, dict) and value.get("poi_id")
    }
    provider_notes: list[str] = []
    missing_add = [name for name in (request.add_names or ())
                   if not any(name in poi.name or poi.name in name
                              for poi in candidates.values())]
    if missing_add:
        # 换入新地点时复用既有「点名地点补全」能力；只补候选，不重新
        # 搜索整座城市，也不触发 POI 专家/Transit/Weather 全量链。
        # 边界纪律（test_agent_service_boundary）：Provider 触点只在
        # services 层——节点经 poi_service 封装调用，不直连 provider。
        try:
            from backend.travel.services.poi_service import (
                resolve_missing_candidates,
            )

            added, provider_notes = resolve_missing_candidates(
                itinerary.brief.destination,
                list(candidates.values()),
                missing_add,
            )
            candidates.update({poi.poi_id: poi for poi in added})
        except Exception:  # noqa: BLE001 — 补全失败交给局部结果如实披露
            provider_notes = []
    result = apply_partial_replan(itinerary, candidates, request)
    result_payload = {
        "status": result.status,
        "message": result.message,
        "changed_days": list(result.changed_days),
        "partial_replan": result.partial_replan,
        "validation_failed": result.validation_failed,
        "operation": request.operation,
        "target_day": request.target_day,
    }
    update: dict = {
        "partial_replan_done": True,
        "partial_replan_result": result_payload,
        "itinerary": save_itinerary(result.itinerary),
        "notes": list(state.get("notes") or []) + provider_notes + [result.message],
        "expert_history": list(state.get("expert_history") or []) + [{
            "expert": "partial_replan",
            "status": result.status,
            "duration_ms": 0,
        }],
    }
    if result.validation_failed:
        update["validation"] = ValidationReport(
            violations=[Violation(
                code="PARTIAL_REPLAN_UNSATISFIED",
                level=LEVEL_ERROR,
                message=result.message,
                day_index=request.target_day or 0,
                detail={"operation": request.operation},
            )],
            checked_days=len(result.itinerary.days),
        ).model_dump()
    else:
        # 原校验结果只属于旧版本；成功改单后交给 validator 重算。
        update["validation"] = None
        if candidates:
            update["candidates"] = [poi.model_dump() for poi in candidates.values()]
    return update


__all__ = ["partial_replan_node"]

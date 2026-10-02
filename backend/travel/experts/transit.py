"""travel/experts/transit.py — 通勤与排程专家节点（Phase 3 改造：节点编排 + 兼容入口）

算法真身已迁 services/transit_service.py（order_pois/schedule_day/
rebuild_days/build_itinerary/路段预热，逐字搬运零改动）；`rebuild_days`
全域唯一重排实现的真身在 transit_service，**experts/transit 经 re-export
承接 repair.py:26 的既有 import（零改动）**。

本文件保留 LangGraph 节点 `transit_expert_node`（state 读写 /
run_expert_safely / 版本盖章 stamp_version / 遥测 / expert_history——
Node 边界冻结）与历史死代码 `reschedule_after_repair`（无调用方，
Phase 8 清理；因读 state，按 Service 零 state 纪律不迁 service）。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.core.events import run_travel_tool
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import (
    data_snapshot_version,
    load_brief,
    load_itinerary,
    save_itinerary,
)
from backend.travel.models.itinerary import CHANGE_INITIAL, KIND_VISIT
from backend.travel.models.poi import Poi

# 兼容面 + 节点调用面（本模块命名空间 = 补丁缝）：experts/__init__、
# repair.py:26、test_p0_mvp 等的历史符号；rebuild_days 真身在
# services/transit_service.py（repair 经此 re-export 零改动）
from backend.travel.agents.optimization_agent import OptimizationAgent
from backend.travel.agents.planning_agent import PlanningAgent
from backend.travel.services.transit_service import (  # noqa: F401
    order_pois,
    rebuild_days,
    schedule_day,
)

_optimization = OptimizationAgent()
_planning = PlanningAgent()


def _needs_train_search(message: str, brief) -> bool:
    return bool(
        brief.origin.strip()
        and brief.start_date
        and any(token in (message or "") for token in (
            "查高铁", "查车票", "查火车", "查车次", "高铁票", "动车票",
        ))
    )


def prefetch_day_legs(pois_by_day):
    """路段预热（Optimization 能力）：委托 OptimizationAgent。"""
    _optimization.prefetch_day_legs(pois_by_day)


def build_itinerary(brief, pois_by_day):
    """排程（Optimization 能力）：委托 OptimizationAgent。"""
    return _optimization.build_itinerary(brief, pois_by_day)


def transit_expert_node(state: dict) -> dict:
    """通勤专家节点：骨架 → 带时刻的行程。"""

    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        candidates = {p["poi_id"]: Poi.model_validate(p)
                      for p in state.get("candidates", [])}
        day_plan = state.get("day_plan", [])

        if not day_plan:
            return {"status": "failed", "data": {},
                    "notes": [], "error": "骨架为空，无法排程"}

        live_search: dict[str, dict] = {}
        if _needs_train_search(state.get("user_message", ""), brief):
            def _search_trains_with_prices() -> dict:
                """余票 + 前 2 车次票价并查，结果并入同一份车次数据。

                票价必须在本事件内合并：SSE 的 train preview 在
                result_summary 时点生成，之后追加价格前端看不到。
                治理记账不受影响——每次票价 Tool 调用仍由
                live_search_service._invoke 独立记入 record_tool_result，
                管理端 /tools 统计按真实 Tool 粒度分列。
                """
                data = _planning.search_trains(
                    from_station=brief.origin,
                    to_station=brief.destination,
                    travel_date=brief.start_date.isoformat(),
                )
                return _planning.attach_train_prices(
                    data,
                    from_station=brief.origin,
                    to_station=brief.destination,
                    travel_date=brief.start_date.isoformat(),
                    limit=2,
                )

            live_search["train"] = run_travel_tool(
                "travel_train_search_tool",
                "planning",
                _search_trains_with_prices,
                result_summary=_planning.train_event_summary,
            )

        extra_notes: list[str] = []
        pois_by_day = [
            [candidates[pid] for pid in day if pid in candidates]
            for day in day_plan
        ]
        # 候选池成员不变式（STOP I4）：day_plan 里引用了候选池没有的
        # poi_id 属于结构异常 —— 不排（排不出没有数据的位置），但必须
        # 显式留痕交给校验/披露，不得静默蒸发
        known = {pid for day in day_plan for pid in day}
        unknown = sorted(known - set(candidates))
        if unknown:
            logger.warning("[TravelTransit] day_plan 引用了候选池外的 poi_id: %s",
                           unknown)
            extra_notes.append(
                f"行程骨架引用了 {len(unknown)} 个候选数据外的地点标识，已忽略")

        run_travel_tool(
            "travel.calculate_route",
            "optimization",
            lambda: prefetch_day_legs(pois_by_day),
            result_summary=lambda _value: {
                "data_status": "warmed_or_local_estimate",
            },
        )
        itinerary, notes = run_travel_tool(
            "route.optimizer",
            "optimization",
            lambda: build_itinerary(brief, pois_by_day),
            result_summary=lambda value: {
                "day_count": len(value[0].days),
                "leg_count": sum(len(day.legs) for day in value[0].days),
            },
        )
        # 城际班次摘要写入行程契约（B 方案）：车票检索结果此前只活在
        # SSE 过程里，done 后即弃——结果页的班次条吃不到数据。这里把
        # 摘要挂到 itinerary.intercity（前 6 个车次；prices 仅并查过的
        # 前 2 个有值）。未触发车票查询时 intercity 保持空列表。
        train_data = live_search.get("train")
        if isinstance(train_data, dict) and train_data.get("trains"):
            from backend.travel.models.itinerary import IntercityTrain

            itinerary.intercity = [
                IntercityTrain(
                    train_no=str(t.get("train_no") or ""),
                    start_time=str(t.get("start_time") or ""),
                    arrive_time=str(t.get("arrive_time") or ""),
                    duration=str(t.get("duration") or ""),
                    seats={str(k): v for k, v in (t.get("seats") or {}).items()},
                    prices={str(k): v for k, v in (t.get("prices") or {}).items()},
                    from_station=str(train_data.get("from_station") or brief.origin),
                    to_station=str(train_data.get("to_station") or brief.destination),
                    date=str(train_data.get("date") or ""),
                    source=str(train_data.get("source") or "12306"),
                    queried_at=str(train_data.get("queried_at") or ""),
                )
                for t in train_data["trains"][:6]
                if isinstance(t, dict) and t.get("train_no")
            ]

        # 版本章（任务书 §4）：出生即回答「基于哪个需求、哪份数据、为什么产生」。
        # 重规划轮的 change_reason 由 slot_filler 写入 state；候选池签名按
        # state.candidates 全集计算（含未排入项 —— 数据版本不等同于行程内容）。
        itinerary.stamp_version(
            brief,
            reason=state.get("brief_change_reason") or CHANGE_INITIAL,
            parent_version=state.get("plan_parent_version"),
            data_snapshot=data_snapshot_version(state.get("candidates", [])),
            changed_fields=list(state.get("brief_changed_fields") or []),
        )
        logger.info("[TravelTransit] 排程完成 days=%d legs=%d plan=v%d(brief v%d %s)",
                    len(itinerary.days),
                    sum(len(d.legs) for d in itinerary.days),
                    itinerary.plan_version, itinerary.brief_version,
                    itinerary.change_reason)
        # STOP I6 结构化事件（软失败）
        try:
            from backend.travel import quality_metrics as qm

            qm.event("travel.itinerary.planned",
                     days=len(itinerary.days),
                     pois=itinerary.total_pois(),
                     plan_version=itinerary.plan_version)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "success",
                "data": {"itinerary": save_itinerary(itinerary),
                         "live_search": live_search},
                "notes": extra_notes + notes}

    result = run_expert_safely("transit", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "transit", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    update: dict = {
        "last_expert_result": dict(result),
        "expert_history": history,
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }
    if data.get("itinerary"):
        update["itinerary"] = data["itinerary"]
    if data.get("live_search"):
        update["live_search"] = {
            **(state.get("live_search") or {}),
            **data["live_search"],
        }
    return update


def reschedule_after_repair(state: dict) -> "object | None":
    """修复后重排：保留原有的天序号与日期，仅重算时刻/通勤/费用。

    **死代码**（审计确认无调用方，Phase 8 清理清单）：保留原位仅因它读
    graph state（load_brief/load_itinerary），按「Service 零 state」纪律
    不迁 service。重排本体在 transit_service.rebuild_days。
    """
    brief = load_brief(state)
    itinerary = load_itinerary(state)
    if itinerary is None:
        return None
    candidates = {p["poi_id"]: Poi.model_validate(p)
                  for p in state.get("candidates", [])}

    specs: list[tuple[int, object, list[Poi]]] = []
    for day in itinerary.days:
        survivors = [candidates[i.poi.poi_id] for i in day.items
                     if i.kind == KIND_VISIT and i.poi is not None
                     and i.poi.poi_id in candidates]
        specs.append((day.day_index, day.day_date, survivors))
    return rebuild_days(brief, specs)

"""travel/experts/lodging.py — 住宿专家节点（收编重构 U2 新增）

编排位置：weather 之后、budget 之前（预算依赖住宿档位成本项，AGENTS-DESIGN
A5/A6）。零 LLM 业务编排（experts/base 纪律）；数据源走 travel.hotel.search
（收编件 hotel_search_service 三态）。只查不订——预订动作走 travel_v2
REST 确认门（收编重构 §4.2）。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief
from backend.travel.services.lodging_service import (
    HotelSearchParamError,
    search_lodging,
)


def lodging_expert_node(state: dict) -> dict:
    """住宿专家节点：行程日期 × 目的地 → 住宿候选（档位标注 + 成本口径）。"""

    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        if brief is None or not brief.destination:
            return {"status": "success", "data": {}, "notes": []}

        # 退房日期 = 出发日期 + 天数（brief 无 end_date 字段，确定性派生）
        from datetime import timedelta

        check_in = brief.start_date
        check_out = (check_in + timedelta(days=brief.resolved_days())
                     if check_in is not None else None)
        try:
            result = search_lodging(
                destination=brief.destination,
                check_in=check_in,
                check_out=check_out,
                adults=brief.adults or brief.party_size or 2,
                rooms=1,
            )
        except HotelSearchParamError as e:
            return {"status": "success", "data": {}, "notes": [
                f"住宿查询参数未通过校验（{e}），未返回住宿候选"]}
        except Exception as e:  # noqa: BLE001 — 住宿是可降级查询，不阻断主链
            logger.warning("[TravelLodging] 住宿检索异常: %s", e)
            return {"status": "success", "data": {}, "notes": [
                "住宿数据源暂时不可用，本次未返回住宿候选"]}

        notes: list[str] = []
        options = result.get("options") or []
        if result.get("status") == "disabled":
            notes.append("实时住宿查询未启用；未展示未经核实的库存与价格")
        elif result.get("status") == "empty":
            notes.append(f"{brief.destination} 暂无符合条件的住宿候选")
        elif options:
            notes.append(
                f"已检索到 {len(options)} 个住宿候选（"
                f"{result.get('source') or '实时数据源'}），"
                "价格与房态随时可能变化，预订前请以确认门复核为准")

        return {
            "status": "success",
            "data": {"lodging_options": options,
                     "lodging_disclosure": result.get("disclosure") or ""},
            "notes": notes,
        }

    result = run_expert_safely("lodging", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "lodging", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    update: dict = {
        "last_expert_result": dict(result),
        "expert_history": history,
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }
    if data.get("lodging_options") is not None:
        update["lodging_options"] = data["lodging_options"]
    if data.get("lodging_disclosure"):
        update["lodging_disclosure"] = data["lodging_disclosure"]
    return update

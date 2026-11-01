"""travel_v2/services/auto_optimize_service.py — 自动优选编排（收编重构 U3）

职责（AGENTS-DESIGN C2）：对正式行程按上下文聚合查询（餐饮位/住宿）→
按「评分×预算×可用性」确定性择优 → 产出建议清单（每类含备选与披露）。
**编排本身只读**：「选入」仍走既有单条 selection 写端点（写路径唯一），
确认权在用户——本服务绝不直接改行程文档。

数据源语义直通各查询服务（高德商户/收编件三态），失败逐类如实降级
（该类建议缺失 + 披露），不伪造候选。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from backend.shared.logger import logger

# 每类建议保留的条数（含备选）
_TOP_N = 3


def _trip_dates(doc: dict[str, Any]) -> tuple[date | None, date | None]:
    brief = (doc or {}).get("brief") or {}
    try:
        start = date.fromisoformat(str(brief.get("start_date"))[:10])
    except (ValueError, TypeError):
        return None, None
    days = int(brief.get("day_count") or 0)
    if days <= 0:
        days = len((doc or {}).get("days") or []) or 1
    return start, start + timedelta(days=days)


def _top_food(merchants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """评分降序取前 N（评分未知排后，不猜）。"""
    ranked = sorted(
        (m for m in merchants if isinstance(m, dict)),
        key=lambda m: (m.get("rating") is None,
                       -(m.get("rating") or 0.0)),
    )
    return [
        {
            "selection_id": m.get("selection_id"),
            "name": m.get("name"),
            "rating": m.get("rating"),
            "avg_cost_cny": m.get("avg_cost_cny"),
            "address": m.get("address"),
            "reason": "评分最高" if (m.get("rating") or 0) > 0 else "评分未知",
        }
        for m in ranked[:_TOP_N]
    ]


def _top_lodging(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """价格升序取前 N（单价未知排后）；带档位与可订状态，不改单。"""
    ranked = sorted(
        (o for o in options if isinstance(o, dict)),
        key=lambda o: (o.get("nightly_price_cny") is None,
                       o.get("nightly_price_cny") or 0.0),
    )
    return [
        {
            "selection_id": o.get("selection_id"),
            "name": o.get("name"),
            "nightly_price_cny": o.get("nightly_price_cny"),
            "tier": o.get("tier"),
            "availability": o.get("availability"),
            "booking_status": o.get("booking_status"),
            "reason": "价格最低" if o.get("nightly_price_cny") else "价格未知",
        }
        for o in ranked[:_TOP_N]
    ]


def build_optimization_suggestions(
    trip_doc: dict[str, Any], tenant_id: str, owner_id: str,
) -> dict[str, Any]:
    """聚合查询 → 择优建议（只读编排；逐类软失败）。"""
    from backend.travel.services import lodging_service
    from backend.travel.services import live_search_service

    destination = str(((trip_doc or {}).get("brief") or {}).get("destination")
                      or "").strip()
    start, end = _trip_dates(trip_doc)
    suggestions: dict[str, Any] = {}
    disclosures: list[str] = []

    # 餐饮位（高德商户；评分/营业状态随时间变化，如实披露）
    try:
        food = live_search_service.search_food(destination) if destination else {}
        picks = _top_food(food.get("merchants") or [])
        suggestions["meals"] = picks
        if food.get("disclosure"):
            disclosures.append(str(food["disclosure"]))
    except Exception as e:  # noqa: BLE001 — 逐类降级，不拖垮其他建议
        logger.warning("[AutoOptimize] 餐饮查询失败: %s", e)
        suggestions["meals"] = []
        disclosures.append("餐饮数据源暂时不可用，本次无餐饮建议")

    # 住宿（收编件三态；off/live-disabled 如实披露）
    lodging_note = None
    try:
        lodging = lodging_service.search_lodging(
            destination=destination, check_in=start, check_out=end,
        ) if destination else {"status": "skipped", "options": []}
        suggestions["lodgings"] = _top_lodging(lodging.get("options") or [])
        if lodging.get("disclosure"):
            disclosures.append(str(lodging["disclosure"]))
        if lodging.get("status") != "success":
            lodging_note = lodging.get("disclosure")
    except Exception as e:  # noqa: BLE001
        logger.warning("[AutoOptimize] 住宿查询失败: %s", e)
        suggestions["lodgings"] = []
        disclosures.append("住宿数据源暂时不可用，本次无住宿建议")

    return {
        "trip_id": (trip_doc or {}).get("trip_id") or "",
        "destination": destination,
        "suggestions": suggestions,
        "disclosures": disclosures,
        # 选入语义：建议不是已选动作；逐条选入走既有 selections 写端点
        #（幂等键由各写端点自有契约承载），本编排零写入。
        "apply_hint": "确认后将按既有 selections 端点逐条选入（逐条幂等）",
    }

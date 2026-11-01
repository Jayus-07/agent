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


def _now_iso() -> str:
    """建议生成的观测时点（餐饮行缺 queried_at 时的兜底，绝不回填假历史）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


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


def _top_food(merchants: list[dict[str, Any]], queried_at: str) -> list[dict[str, Any]]:
    """评分降序取前 N（评分未知排后，不猜）。

    行字段与 V2 搜索 Tab（search_service._merchant_result）同构——建议行
    自带 selection_id/merchant_id/坐标/来源/观测时点全套快照字段，前端
    「选入」可直接构 MerchantSnapshotV2（逐条幂等），无需二次查询。
    selection_id 缺失时按搜索 Tab 同一稳定规则生成（P5 运行态点测发现
    live_search 行不带 selection_id，缺失会让建议无法选入）。
    """
    from backend.travel_v2.services.search_service import stable_selection_id

    ranked = sorted(
        (m for m in merchants if isinstance(m, dict) and m.get("name")),
        key=lambda m: (m.get("rating") is None,
                       -(m.get("rating") or 0.0)),
    )
    rows: list[dict[str, Any]] = []
    for m in ranked[:_TOP_N]:
        source = str(m.get("source") or "amap")
        selection_id = str(m.get("selection_id") or "").strip()
        merchant_id = str(m.get("merchant_id") or m.get("id") or "").strip()
        if not selection_id:
            identity = merchant_id or "|".join((
                str(m.get("name") or ""), str(m.get("address") or ""),
                str(m.get("lat") or ""), str(m.get("lng") or "")))
            selection_id = stable_selection_id("food", source, identity)
        rows.append({
            "selection_id": selection_id,
            "merchant_id": merchant_id or selection_id,
            "name": m.get("name"),
            "rating": m.get("rating"),
            "avg_cost_cny": m.get("avg_cost_cny"),
            "address": m.get("address"),
            "lat": m.get("lat"),
            "lng": m.get("lng"),
            "open_status": m.get("open_status"),
            "open_time_today": m.get("open_time_today"),
            "source": source,
            "observed_at": m.get("queried_at") or queried_at,
            "reason": "评分最高" if (m.get("rating") or 0) > 0 else "评分未知",
        })
    return rows


def _top_lodging(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """价格升序取前 N（单价未知排后）；带档位与可订状态，不改单。

    字段直通 lodging_service.search_lodging options（含坐标/日期/总价/
    来源/观测时点），前端选入可直构 LodgingArrangementV2 快照。
    """
    ranked = sorted(
        (o for o in options if isinstance(o, dict)),
        key=lambda o: (o.get("nightly_price_cny") is None,
                       o.get("nightly_price_cny") or 0.0),
    )
    return [
        {
            "selection_id": o.get("selection_id"),
            "merchant_id": o.get("merchant_id"),
            "name": o.get("name"),
            "city": o.get("city"),
            "address": o.get("address"),
            "lat": o.get("lat"),
            "lng": o.get("lng"),
            "check_in": o.get("check_in"),
            "check_out": o.get("check_out"),
            "nights": o.get("nights"),
            "price_total": o.get("price_total"),
            "currency": o.get("currency"),
            "nightly_price_cny": o.get("nightly_price_cny"),
            "tier": o.get("tier"),
            "availability": o.get("availability"),
            "booking_status": o.get("booking_status"),
            "source": o.get("source"),
            "observed_at": o.get("observed_at"),
            # 跳转型预订：供应商官方下单页链接（已过 deeplink 白名单门，
            # 未配置白名单时为 None）；前端透出「去预订」，平台不代订。
            "deep_link": o.get("deep_link"),
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
    queried_at = _now_iso()

    # 餐饮位（高德商户；与 V2 搜索 Tab 同一映射，行含完整选入快照字段）
    try:
        food = live_search_service.search_food(destination) if destination else {}
        picks = _top_food(food.get("merchants") or [], queried_at)
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

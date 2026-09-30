"""travel/commerce/graph_builder.py — Commerce 域图（STOP K6）

2 节点线性图，零 LLM、零 checkpointer（K0 §7）：

    START → travel_commerce_slot_filler → travel_commerce_executor → END

  - slot_filler：意图类型 + 槽位抽取 + 请求确定性校验；缺必填槽位时生成
    澄清文案（executor 直通渲染，不进 Provider 链）。
  - executor：调 service.search_*（Provider 层执行流）→ reporter 渲染。

与 travel 域图的关系：**完全独立**（K0 §1）——不读不写 Itinerary/Poi/
TravelBrief 任何字段；Commerce failure 不经过行程链路（G1/G19）。
"""
from __future__ import annotations

import threading
from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.shared.logger import logger
from backend.travel.commerce.extract import (
    REQUIRED_SLOTS_BY_KIND,
    detect_commerce_intent,
    extract_flight_params,
    extract_hotel_params,
    iso_value,
    merge_slot_values,
)
from backend.travel.commerce.graph_state import (
    COMMERCE_EXECUTOR,
    COMMERCE_SLOT_FILLER,
    CommerceGraphState,
)
from backend.travel.commerce.graph_node_render import (
    build_clarification,
    render_commerce_result,
)
from backend.travel.commerce.request import (
    FlightSearchRequest,
    HotelSearchRequest,
)
from backend.travel.commerce.service import search_flights, search_hotels


def commerce_slot_filler_node(state: dict) -> dict:
    """意图 + 槽位 + 确定性校验（纯函数式，无 IO）。

    Phase 5 / D2：挂起在场时先走**跨轮续填**——上轮追问「入住/退房日期」
    后用户只答「10月3日到5日」，这类纯槽位值回答不含酒店/机票词，
    ``detect_commerce_intent`` 会判 None 并让用户重说一遍（掉域症状）。
    """
    message = state.get("user_message", "")

    pending = state.get("pending_intent") or {}
    pending_kind = pending.get("kind") or ""
    if pending_kind in REQUIRED_SLOTS_BY_KIND:
        return _resume_commerce(pending_kind, message, pending)

    intent = detect_commerce_intent(message)
    if intent is None:
        # prefilter 已挡；图内兜底 = 如实说明（不猜意图、不误路由）
        return {
            "commerce_type": "",
            "commerce_missing": ["intent"],
            "commerce_clarification": (
                "抱歉，当前会话没有识别到酒店或机票查询意图；"
                "请明确说明（例：帮我找大阪 10 月 3 日到 5 日的酒店）。"
            ),
        }

    collected = {
        k: iso_value(v) for k, v in (
            extract_hotel_params(message) if intent == "hotel"
            else extract_flight_params(message)
        ).items()
    }
    missing = [k for k in REQUIRED_SLOTS_BY_KIND[intent]
               if not collected.get(k)]
    request = None
    if not missing:
        request, err = _build_request(intent, collected)
        if err:
            return _invalid_params(intent, err)
    return {"commerce_type": intent, "commerce_request": request,
            "commerce_missing": missing, "commerce_collected": collected}


def _build_request(intent: str, collected: dict) -> tuple[dict | None, str]:
    """collected（ISO 化值）→ 请求 dict。Returns: (request, 错误原因)。

    首跑与续填共用——此前内联在 slot_filler 里，续填另写一套必然漂移。
    """
    try:
        if intent == "hotel":
            req = HotelSearchRequest(
                city=collected["city"],
                check_in=_to_date(collected["check_in"]),
                check_out=_to_date(collected["check_out"]),
                adults=collected.get("adults", 2),
                children=collected.get("children", 0),
                rooms=collected.get("rooms", 1))
            return {
                "city": req.city, "check_in": req.check_in.isoformat(),
                "check_out": req.check_out.isoformat(),
                "nights": req.nights, "adults": req.adults,
                "children": req.children, "rooms": req.rooms,
                "star_rating": req.star_rating,
            }, ""
        req = FlightSearchRequest(
            origin=collected["origin"], destination=collected["destination"],
            departure_date=_to_date(collected["departure_date"]),
            adults=collected.get("adults", 1),
            children=collected.get("children", 0))
        return {
            "origin": req.origin, "destination": req.destination,
            "departure_date": req.departure_date.isoformat(),
            "adults": req.adults, "children": req.children,
        }, ""
    except ValueError as e:
        return None, str(e)


def _resume_commerce(kind: str, message: str, pending: dict) -> dict:
    """挂起在场时的续填：合并槽位 → 齐备转正常查询 / 仍缺继续追问。

    合并与缺失判定复用 commerce/extract 共享实现（与预订子图同源）。
    不设 ``commerce_clarification``——executor 会走 ``build_clarification``
    生成与首跑一致的追问文案（单一事实源）。
    """
    collected, missing = merge_slot_values(
        kind, message, base=pending.get("collected") or {})
    if missing:
        logger.info("[CommerceSlotFiller] 续填未齐 kind=%s missing=%s",
                    kind, missing)
        return {"commerce_type": kind, "commerce_request": None,
                "commerce_missing": missing, "commerce_collected": collected}
    request, err = _build_request(kind, collected)
    if err:
        return _invalid_params(kind, err)
    logger.info("[CommerceSlotFiller] 续填齐备 kind=%s", kind)
    return {"commerce_type": kind, "commerce_request": request,
            "commerce_missing": [], "commerce_collected": collected}


def _invalid_params(intent: str, reason: str) -> dict:
    return {
        "commerce_type": intent,
        "commerce_request": None,
        "commerce_missing": ["params"],
        "commerce_clarification": f"查询参数有误：{reason}。请调整后重试。",
    }


def commerce_executor_node(state: dict) -> dict:
    """执行 + 渲染（唯一 IO 节点）。"""
    missing = state.get("commerce_missing") or []
    if missing:
        answer = state.get("commerce_clarification") or build_clarification(
            state.get("commerce_type", ""), missing)
        return {"final_answer": answer, "commerce_status": "clarify",
                "commerce_offer_count": 0}

    commerce_type = state.get("commerce_type", "")
    request = state.get("commerce_request") or {}
    if commerce_type == "hotel":
        req = HotelSearchRequest(
            city=request["city"],
            check_in=_to_date(request["check_in"]),
            check_out=_to_date(request["check_out"]),
            adults=request.get("adults", 2),
            children=request.get("children", 0),
            rooms=request.get("rooms", 1),
            star_rating=request.get("star_rating"),
        )
        result = search_hotels(req)
    else:
        req = FlightSearchRequest(
            origin=request["origin"], destination=request["destination"],
            departure_date=_to_date(request["departure_date"]),
            adults=request.get("adults", 1),
            children=request.get("children", 0),
        )
        result = search_flights(req)

    answer = render_commerce_result(result)
    logger.info("[CommerceExecutor] type=%s status=%s offers=%d",
                result.commerce_type, result.status, len(result.offers))
    return {
        "final_answer": answer,
        "commerce_status": result.status,
        "commerce_offer_count": len(result.offers),
    }


def _to_date(value: str):
    from datetime import date as _date

    return _date.fromisoformat(value)


def build_commerce_graph() -> Any:
    """构建 Commerce 域图（无 checkpointer——单发查询无跨轮语义）。"""
    wf = StateGraph(CommerceGraphState)
    wf.add_node(COMMERCE_SLOT_FILLER, commerce_slot_filler_node)
    wf.add_node(COMMERCE_EXECUTOR, commerce_executor_node)
    wf.add_edge(START, COMMERCE_SLOT_FILLER)
    wf.add_edge(COMMERCE_SLOT_FILLER, COMMERCE_EXECUTOR)
    wf.add_edge(COMMERCE_EXECUTOR, END)
    graph = wf.compile()
    logger.info("[CommerceGraph] 编译完成（2 节点，无 checkpointer）")
    return graph


_commerce_graph: Any | None = None
_lock = threading.Lock()


def get_commerce_graph() -> Any:
    """域图单例（double-checked locking）。"""
    global _commerce_graph
    if _commerce_graph is None:
        with _lock:
            if _commerce_graph is None:
                _commerce_graph = build_commerce_graph()
    return _commerce_graph

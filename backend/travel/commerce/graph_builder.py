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
    detect_commerce_intent,
    extract_flight_params,
    extract_hotel_params,
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
    """意图 + 槽位 + 确定性校验（纯函数式，无 IO）。"""
    message = state.get("user_message", "")
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

    if intent == "hotel":
        params = extract_hotel_params(message)
        missing = [k for k in ("city", "check_in", "check_out")
                   if k not in params]
        request = None
        if not missing:
            try:
                req = HotelSearchRequest(
                    city=params["city"], check_in=params["check_in"],
                    check_out=params["check_out"],
                    adults=params.get("adults", 2),
                    children=params.get("children", 0),
                    rooms=params.get("rooms", 1))
                request = {
                    "city": req.city, "check_in": req.check_in.isoformat(),
                    "check_out": req.check_out.isoformat(),
                    "nights": req.nights, "adults": req.adults,
                    "children": req.children, "rooms": req.rooms,
                    "star_rating": req.star_rating,
                }
            except ValueError as e:
                return _invalid_params(intent, str(e))
        return {"commerce_type": intent, "commerce_request": request,
                "commerce_missing": missing}

    params = extract_flight_params(message)
    missing = [k for k in ("origin", "destination", "departure_date")
               if k not in params]
    request = None
    if not missing:
        try:
            req = FlightSearchRequest(
                origin=params["origin"], destination=params["destination"],
                departure_date=params["departure_date"],
                adults=params.get("adults", 1),
                children=params.get("children", 0))
            request = {
                "origin": req.origin, "destination": req.destination,
                "departure_date": req.departure_date.isoformat(),
                "adults": req.adults, "children": req.children,
            }
        except ValueError as e:
            return _invalid_params(intent, str(e))
    return {"commerce_type": intent, "commerce_request": request,
            "commerce_missing": missing}


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

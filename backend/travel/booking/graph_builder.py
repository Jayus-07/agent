"""travel/booking/graph_builder.py — Booking 域图（STOP L9）

2 节点线性、零 LLM、无 checkpointer（与 commerce 域图同纪律）：

    START → travel_booking_resolver → travel_booking_executor → END

  - resolver：子意图判定（新预订 / 确认预订 / 预订状态）+ 参数抽取
    （复用 commerce extract 的日期/城市工具与 slot_filler 抽取）；
  - executor：BookingService 编排 + reporter 渲染（唯一 IO 节点）。

订单/报价事实全部在 PG——图状态不承载业务事实，崩溃恢复与图无关
（§二十六：恢复不要求用户重新点击）。
"""
from __future__ import annotations

import re
import threading
from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.shared.logger import logger
from backend.travel.booking.graph_state import (
    BOOKING_EXECUTOR,
    BOOKING_RESOLVER,
    BookingGraphState,
)
from backend.travel.booking.service import (
    BookingProviderOff,
    BookingService,
    OfferNotSelectable,
)

_RE_CONFIRM = re.compile(r"确认预订|确认下单|就订这个|订吧|好的?确认")
_RE_STATUS = re.compile(r"我的预订|预订状态|预订结果|订到没有|订成功了吗")
_RE_NEW_BOOKING = re.compile(
    r"(?:帮我|给我|我想|要)?(?:预订|订一下|订个|订一间|订一张|下单预订)")


def detect_booking_action(message: str) -> str:
    """子意图（纯函数）：confirm | status | new | unknown。

    优先级：确认 > 状态查询 > 新预订（同一句里确认语义最具体）。
    CS 售后（订单退款/退票）由 CS prefilter 先行承接，此处不再负向。
    """
    message = (message or "").strip()
    if not message:
        return "unknown"
    if _RE_CONFIRM.search(message):
        return "confirm"
    if _RE_STATUS.search(message):
        return "status"
    if _RE_NEW_BOOKING.search(message):
        return "new"
    return "unknown"


def booking_resolver_node(state: dict) -> dict:
    from backend.travel.commerce.extract import (
        detect_commerce_intent,
        extract_flight_params,
        extract_hotel_params,
    )

    message = state.get("user_message", "")
    action = detect_booking_action(message)
    params: dict = {"search_params": {}, "selection": {}}
    clarification = ""

    if action == "new":
        intent = detect_commerce_intent(message)  # 复用 STOP K 意图（hotel/flight）
        if intent is None:
            action = "unknown"
            clarification = (
                "请告诉我要预订的酒店或机票信息（城市、日期，例："
                "帮我预订大阪10月3日到5日的酒店）。")
        else:
            params["commerce_type"] = intent
            if intent == "hotel":
                raw = extract_hotel_params(message)
                missing = [k for k in ("city", "check_in", "check_out")
                           if k not in raw]
                params["search_params"] = {
                    "city": raw.get("city", ""),
                    "check_in": raw["check_in"].isoformat() if "check_in" in raw else "",
                    "check_out": raw["check_out"].isoformat() if "check_out" in raw else "",
                    "adults": raw.get("adults", 2),
                    "children": raw.get("children", 0),
                    "rooms": raw.get("rooms", 1),
                }
            else:
                raw = extract_flight_params(message)
                missing = [k for k in ("origin", "destination", "departure_date")
                           if k not in raw]
                params["search_params"] = {
                    "origin": raw.get("origin", ""),
                    "destination": raw.get("destination", ""),
                    "departure_date": raw["departure_date"].isoformat()
                    if "departure_date" in raw else "",
                    "adults": raw.get("adults", 1),
                    "children": raw.get("children", 0),
                }
            if missing:
                action = "unknown"
                clarification = (
                    "预订还需要：" + "、".join(
                        {"city": "城市", "check_in": "入住日期",
                         "check_out": "退房日期", "origin": "出发城市",
                         "destination": "目的地城市",
                         "departure_date": "出发日期"}.get(m, m)
                        for m in missing) + "。")
            else:
                # 默认选第 1 顺位（确定性排序：价格从低到高）
                params["selection"] = {"index": 1}
    elif action == "unknown":
        clarification = (
            "请说明预订意图：新预订（含城市/日期）、「确认预订」提交待确认订单、"
            "或查询「我的预订」状态。")

    return {"booking_action": action, "booking_params": params,
            "booking_clarification": clarification}


def booking_executor_node(state: dict) -> dict:
    action = state.get("booking_action", "unknown")
    tenant_id = state.get("tenant_id", "") or "default"
    user_id = state.get("user_id", "")
    if action == "unknown":
        return {"final_answer": state.get("booking_clarification", ""),
                "booking_status": "clarify"}

    from backend.travel.booking.reporter import (
        render_execution_outcome,
        render_quote_confirmation,
        render_status,
    )
    from backend.config.travel_booking import provider_name

    service = BookingService()
    try:
        if action == "new":
            params = state.get("booking_params", {})
            outcome = service.create_quote(
                tenant_id=tenant_id, user_id=user_id,
                commerce_type=params.get("commerce_type", "hotel"),
                search_params=params.get("search_params", {}),
                selection=params.get("selection", {}))
            answer = render_quote_confirmation(
                outcome.quote, outcome.offer_summary, outcome.order)
            return {"final_answer": answer, "booking_status": "quoted"}

        if action == "confirm":
            outcome = service.confirm_and_execute(
                tenant_id=tenant_id, user_id=user_id)
            return {"final_answer": render_execution_outcome(
                outcome, provider_mode=provider_name()),
                "booking_status": outcome.result}

        # status
        report = service.status_report(tenant_id=tenant_id, user_id=user_id)
        return {"final_answer": render_status(report),
                "booking_status": (report or {}).get("order", {}).get(
                    "status", "none")}
    except OfferNotSelectable as e:
        return {"final_answer": f"暂时无法创建预订：{e}", "booking_status": "clarify"}
    except BookingProviderOff:
        return {"final_answer": "预订功能暂未开通。", "booking_status": "off"}
    except Exception:
        # 主链保护：booking 任何异常不得 500 穿透主图
        logger.exception("[BookingExecutor] 执行异常")
        return {"final_answer": "预订服务暂时不可用，请稍后再试。",
                "booking_status": "error"}


def build_booking_graph() -> Any:
    wf = StateGraph(BookingGraphState)
    wf.add_node(BOOKING_RESOLVER, booking_resolver_node)
    wf.add_node(BOOKING_EXECUTOR, booking_executor_node)
    wf.add_edge(START, BOOKING_RESOLVER)
    wf.add_edge(BOOKING_RESOLVER, BOOKING_EXECUTOR)
    wf.add_edge(BOOKING_EXECUTOR, END)
    graph = wf.compile()
    logger.info("[BookingGraph] 编译完成（2 节点，无 checkpointer）")
    return graph


_booking_graph: Any | None = None
_lock = threading.Lock()


def get_booking_graph() -> Any:
    global _booking_graph
    if _booking_graph is None:
        with _lock:
            if _booking_graph is None:
                _booking_graph = build_booking_graph()
    return _booking_graph

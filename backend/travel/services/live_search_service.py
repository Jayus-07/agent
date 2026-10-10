"""旅游实时检索服务。

该层只负责调用已注册 Tool、解析统一封套和返回结构化业务数据；不提供
任何本地假数据。Tool 的失败会转成明确异常，由旅游域专家和 SSE 原样收口。

治理口径：本层是同步域图路径，通过 GovernanceRuntime 的同步适配器进入
SafeToolExecutor；Tool Adapter 只负责调用具体 Tool 并原样传递统一封套。
"""
from __future__ import annotations

import json
from typing import Any

from backend.config import map as map_config
from backend.shared.logger import logger
from backend.tools.map.merchant import map_merchant_search_tool
from backend.tools.map.place import map_place_search_tool
from backend.tools.search.zhihu import global_search_tool, zhihu_search_tool
from backend.tools.travel.train import (
    _localize_seats,
    travel_train_price_tool,
    travel_train_search_tool,
)


class LiveSearchError(RuntimeError):
    """真实数据源不可用或返回了无法消费的结构。"""

    def __init__(self, message: str, *, category: str = "business_failure"):
        super().__init__(message)
        self.category = category


def _search_error_category(status: Any = None, message: str = "") -> str:
    """保留 ToolRuntime 的失败类别，避免把「查不了」显示成「无结果」。"""
    value = getattr(status, "value", status)
    text = (message or "").lower()
    if value == "timeout" or any(token in text for token in (
        "timeout", "timed out", "connection timeout", "超时",
    )):
        return "network_timeout"
    if "未启用" in message or "disabled" in text:
        return "disabled"
    if value == "unavailable" or value == "rate_limited" or any(token in text for token in (
        "配额", "限流", "未配置", "不可用",
    )):
        return "provider_unavailable"
    return "business_failure"


def _invoke(
    tool: Any,
    tool_name: str,
    *,
    capability: str = "",
    agent: str = "travel_live_search",
    **kwargs: Any,
) -> str:
    """同步 Tool Adapter：所有真实调用均先经过 GovernanceRuntime。"""
    from backend.core.tool_governance.guard import (
        ToolCallRequest,
        governance_runtime,
    )
    from backend.core.tool_runtime.models import ToolStatus
    from backend.core.tool_runtime.tracing import finish_tool_span, start_tool_span
    from backend.travel.services.tool_cache import cached_envelope

    def _call() -> str:
        from backend.config.travel import TRAVEL_TOOL_CACHE_TTL

        raw, cache_hit = cached_envelope(
            tool_name,
            dict(kwargs),
            lambda: tool.invoke(kwargs),
            ttl=TRAVEL_TOOL_CACHE_TTL,
        )
        if cache_hit:
            payload = json.loads(raw)
            payload["cache_hit"] = True
            return json.dumps(payload, ensure_ascii=False)
        return raw

    request = ToolCallRequest(
        capability=tool_name,
        arguments=dict(kwargs),
        domain="travel",
        intent_fit="match",
        selection_reason=capability or tool_name,
    )
    span = start_tool_span(
        tool_name,
        capability=capability or tool_name,
        params=kwargs,
        agent=agent,
    )
    executed = governance_runtime.execute_sync(
        request,
        _call,
        normalize_output=True,
        domain="travel",
        tool_name=tool_name,
        trace_span=span,
        trace_capability=capability or tool_name,
        trace_agent=agent,
    )
    finish_tool_span(span, executed)
    if executed.status is not ToolStatus.SUCCESS:
        detail = executed.data if isinstance(executed.data, dict) else {}
        error = detail.get("error") if isinstance(detail, dict) else None
        detail_message = error.get("message") if isinstance(error, dict) else error
        message = str(detail_message or executed.error_message or executed.error_code or f"{tool_name} 调用失败")
        raise LiveSearchError(
            message,
            category=_search_error_category(executed.status, message),
        )
    if not isinstance(executed.data, dict):
        raise LiveSearchError(f"{tool_name} 返回了无效封套")
    return json.dumps(executed.data, ensure_ascii=False)


def _decode_success(raw: str, tool_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LiveSearchError(f"{tool_name} 返回了无法解析的封套") from exc
    if not isinstance(payload, dict) or payload.get("status") != "success":
        detail = payload.get("error") if isinstance(payload, dict) else None
        message = str(detail or f"{tool_name} 调用失败")
        raise LiveSearchError(
            message,
            category=_search_error_category(message=message),
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LiveSearchError(f"{tool_name} 成功但缺少 data")
    return data


def search_merchants(*, keyword: str, city: str, types: str = "",
                     page_size: int = 6) -> dict[str, Any]:
    """调用高德商户 Tool，返回商户数据，不吞掉真实失败。"""
    raw = _invoke(
        map_merchant_search_tool, "map_merchant_search_tool",
        capability="travel.search_poi", agent="research",
        keyword=keyword, city=city, types=types, page_size=page_size,
    )
    return _decode_success(raw, "map_merchant_search_tool")


def search_food(city: str) -> dict[str, Any]:
    return search_merchants(
        keyword="美食", city=city, types=map_config.AMAP_FOOD_TYPES,
    )


def search_attractions(*, keyword: str, city: str,
                       page_size: int = 10) -> dict[str, Any]:
    """调用高德商户 Tool 检索景点类目（A1 评分源，2026-10-04）。

    高德 v5 business 块是唯一带 rating/营业时间的源（腾讯 place 不回）；
    返回封套与 search_food 同形（merchants 列表），失败上抛 LiveSearchError
    由调用方逐词降级——评分源失败只损失评分，不损失候选（腾讯路还在）。
    """
    return search_merchants(
        keyword=keyword, city=city,
        types=map_config.AMAP_ATTRACTION_TYPES, page_size=page_size,
    )


def search_hotels(city: str) -> dict[str, Any]:
    return search_merchants(
        keyword="酒店", city=city, types=map_config.AMAP_HOTEL_TYPES,
    )


def search_trains(*, from_station: str, to_station: str,
                  travel_date: str, limit: int = 6) -> dict[str, Any]:
    """调用 12306 MCP Tool，返回车次数据，不把失败映射成空车次。"""
    raw = _invoke(
        travel_train_search_tool, "travel_train_search_tool",
        capability="travel.train.search", agent="planning",
        from_station=from_station,
        to_station=to_station,
        date=travel_date,
        limit=limit,
    )
    return _decode_success(raw, "travel_train_search_tool")


def search_places(*, keyword: str, city: str,
                  page_size: int = 8) -> dict[str, Any]:
    """调用腾讯位置服务地点检索 Tool（POI 候选池实时源）。

    返回 data 封套：{keyword, city, count, pois:[{id,name,lat,lng,
    category,address,...}]}；失败上抛 LiveSearchError 由调用方披露。
    """
    raw = _invoke(
        map_place_search_tool, "map_place_search_tool",
        capability="travel.poi_search", agent="research",
        keyword=keyword, city=city, page_size=page_size,
    )
    return _decode_success(raw, "map_place_search_tool")


def search_train_price(*, from_station: str, to_station: str,
                       travel_date: str, train_code: str) -> dict[str, Any]:
    """调用 12306 票价 MCP Tool（单车次）。失败上抛 LiveSearchError，
    由调用方按行降级——票价缺行只影响展示，不影响车票数据本体。"""
    raw = _invoke(
        travel_train_price_tool, "travel_train_price_tool",
        capability="travel.train.search", agent="planning",
        from_station=from_station,
        to_station=to_station,
        train_date=travel_date,
        train_code=train_code,
    )
    return _decode_success(raw, "travel_train_price_tool")


def train_price_preview(data: dict[str, Any]) -> dict[str, Any]:
    """票价成功封套 → 摘要（席别键复用车票侧的中文化口径，单一事实源）。"""
    prices = data.get("prices") or {}
    localized = _localize_seats(prices)
    return {
        "category": "train_price",
        "train_code": data.get("train_code") or "",
        "result_count": len(localized),
        "data_status": "available" if localized else "empty",
        "preview": [localized],
        "provider": "12306",
    }


def merchant_preview(data: dict[str, Any], kind: str) -> dict[str, Any]:
    merchants = data.get("merchants") or []
    return {
        "category": kind,
        "result_count": len(merchants),
        "data_status": "available" if merchants else "empty",
        "preview": merchants[:6],
        "provider": "amap",
    }


def train_preview(data: dict[str, Any]) -> dict[str, Any]:
    trains = data.get("trains") or []
    return {
        "category": "train",
        "result_count": len(trains),
        "data_status": "available" if trains else "empty",
        "preview": trains[:6],
        "provider": "12306",
    }


# ── 攻略检索（知乎官方 MCP，2026-10-02）：增强信息，非规划硬依赖 ──
# 失败语义与商户/车次一致：本层不吞失败，上抛 LiveSearchError 由 Agent
# 层做「单路独立降级」（攻略检索两路互不拖累，与商户/车次的硬失败不同）。
_GUIDE_QUERY_TPL = "{destination} 旅游 美食 攻略"
# 规划主链自动检索的主题词（2026-10-03）：景点/美食/城市特色三路站内检索，
# 结果供 SSE 攻略卡展示 + 候选 POI「知乎攻略提及」理由匹配；空 topic 走
# 旧模板（query_static 问答与存量调用方不受影响）。
GUIDE_TOPIC_QUERIES: dict[str, str] = {
    "attraction": "{destination} 旅游 景点 攻略",
    "food": "{destination} 美食 特色 必吃",
    "city": "{destination} 城市特色 值得去",
}
# 主题 → 前端展示标签（GuidePreview 徽章用；单一事实源）
GUIDE_TOPIC_LABELS: dict[str, str] = {
    "attraction": "景点",
    "food": "美食",
    "city": "城市特色",
}


def search_zhihu_guides(*, destination: str, limit: int = 4,
                        topic: str = "") -> dict[str, Any]:
    """知乎站内旅游攻略（经验帖，调用知乎官方 MCP）。topic 见 GUIDE_TOPIC_QUERIES。"""
    query = GUIDE_TOPIC_QUERIES.get(topic, _GUIDE_QUERY_TPL)
    raw = _invoke(
        zhihu_search_tool, "zhihu_search_tool",
        capability="travel.guide.search", agent="research",
        query=query.format(destination=destination), count=limit,
    )
    return _decode_success(raw, "zhihu_search_tool")


def search_web_guides(*, destination: str, limit: int = 4,
                      topic: str = "") -> dict[str, Any]:
    """全网旅游攻略（媒体文章/官方线路消息，调用知乎官方 MCP）。"""
    query = GUIDE_TOPIC_QUERIES.get(topic, _GUIDE_QUERY_TPL)
    raw = _invoke(
        global_search_tool, "global_search_tool",
        capability="travel.guide.search", agent="research",
        query=query.format(destination=destination), count=limit,
    )
    return _decode_success(raw, "global_search_tool")


def guides_preview(data: dict[str, Any], source: str,
                   topic: str = "") -> dict[str, Any]:
    results = [
        {**item, "topic": topic} if isinstance(item, dict) and topic else item
        for item in (data.get("results") or [])
    ]
    return {
        "category": "guide",
        "source": source,
        "result_count": len(results),
        "data_status": "available" if results else "empty",
        "preview": results[:6],
        "provider": "zhihu_mcp",
    }

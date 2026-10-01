"""tools/travel/train.py — 火车票余票查询（外部 MCP 数据源：12306）

数据源形态（2026-10-02 拍板「外部 MCP server 作为 Tool 数据源」的首例）：
上游是 drfccv/mcp-server-12306（Docker 部署，Streamable HTTP），平台经
``infra/mcp_client.py`` 同步薄客户端消费其 ``query-tickets`` 工具。

与既有数据源的关系：
  - 12306 **没有官方开放 API**，上游是非官方聚合、无 SLA、仅供学习研究
    （不商用）；开关 ``TRAIN_MCP_ENABLED`` 默认关，关闭时本工具明确报
    「未启用」，行程规划主链不受影响（交通耗时估算仍走本地直线估算）；
  - 本工具只做**余票/时刻查询**（只读、无副作用，不需要审批门），
    不做购票——购票属副作用操作，走独立的审批门链路。

上游返回形态全部来自 2026-10-02 实测（解析规则勿凭文档改）：
  - 成功        ``{"success": true, "count": N, "trains": [...]}``
  - 真无直达    ``{"success": false, "error": "未找到该线路的余票",
                  "count": 0, "trains": []}``
  - 坏站名      ``{"success": false, "error": "车站名称无效",
                  "suggestions": [...], "hint": ...}``
  - 坏日期      ``{"success": false, "errors": ["日期格式错误..."]}``

  三种业务失败**结构可区分**（errors 列表 / suggestions+hint /
  count+trains 键），按此拆「查不到」与「参数错」，不做文本匹配。
  上游 MCP 协议层失败（连接/超时/isError）由客户端抛 McpClientError，
  归「查不了」。
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from backend.config import mcp as MCP_CFG
from backend.infra.mcp_client import McpClientError, call_tool
from backend.shared.logger import logger
from backend.shared.tool_envelope import tool_error_result, tool_success_result

# 上游 query-tickets 的工具名（MCP server 侧定义，勿改）
_UPSTREAM_TOOL = "query-tickets"

# 席别键中文化（上游英文键 → 用户面中文）；未收录的键原样透传不丢信息
_SEAT_LABELS = {
    "business": "商务座",
    "first_class": "一等座",
    "second_class": "二等座",
    "first_class_preferred": "优选一等座",
    "second_class_preferred": "优选二等座",
    "no_seat": "无座",
    "sleeper_hard": "硬卧",
    "sleeper_soft": "软卧",
    "sleeper_deluxe_soft": "高级软卧",
    "seat_hard": "硬座",
    "seat_soft": "软座",
    "motion_sleeper": "动卧",
}

_DATE_FMT = "%Y-%m-%d"


def _validate_date(date: str) -> str | None:
    """校验并回显规范日期；非法返回 None。"""
    raw = (date or "").strip()
    try:
        return datetime.strptime(raw, _DATE_FMT).strftime(_DATE_FMT)
    except ValueError:
        return None


def _localize_seats(seats: dict) -> dict:
    """席别键中文化，未收录键透传（上游加新席别时不丢）。"""
    return {_SEAT_LABELS.get(k, k): v for k, v in (seats or {}).items()}


def _classify_failure(payload: dict) -> str:
    """上游 success:false 的结构化分类（见模块头实测形态表）。

    Returns:
        "no_result"（查不到：真无直达） | "bad_request"（参数错：站名/日期）
    """
    if "errors" in payload or "suggestions" in payload:
        return "bad_request"
    if "count" in payload or "trains" in payload:
        return "no_result"
    # 未知形态：按参数错处理（携带完整 details，信息不丢）
    return "bad_request"


@tool
def travel_train_search_tool(
    from_station: str,
    to_station: str,
    date: str,
    limit: int = 20,
) -> str:
    """
    查询 12306 火车票余票：两站之间的车次、出发到达时刻、历时、各席别余票。
    from_station: 出发站名，如 "福州"、"福州南"（支持中文站名全称）
    to_station: 到达站名，如 "厦门北"
    date: 出发日期，格式 YYYY-MM-DD，如 "2026-10-03"（须是今天或未来日期）
    limit: 返回车次上限，默认 20（按出发时间升序截取）
    适用场景：回答「X 到 Y 有哪些车 / 有没有票 / 几点发车多久到」。
    注意：数据来自 12306 非官方聚合源，余票可能延迟，不作为购票依据；
    车票价格与购票操作不在本工具范围内；无直达车时会返回无结果与建议，
    可提示用户考虑中转。
    """
    if not MCP_CFG.is_train_mcp_enabled():
        return tool_error_result(
            "12306 车票查询未启用",
            hint="请在 .env 设置 TRAIN_MCP_ENABLED=true 并确认 mcp-12306 容器已启动",
        )
    from_station = (from_station or "").strip()
    to_station = (to_station or "").strip()
    if not from_station or not to_station:
        return tool_error_result("from_station 与 to_station 不能为空")
    normalized_date = _validate_date(date)
    if normalized_date is None:
        return tool_error_result(
            f"date 格式无法解析：{date!r}，应为 YYYY-MM-DD（如 2026-10-03）")
    limit = max(1, min(int(limit or 20), 50))

    arguments = {
        "from_station": from_station,
        "to_station": to_station,
        "train_date": normalized_date,
    }
    try:
        payload = call_tool(
            MCP_CFG.TRAIN_MCP_BASE_URL, _UPSTREAM_TOOL, arguments,
        )
    except McpClientError as e:
        logger.warning("[TrainTool] 上游调用失败: %s", e)
        return tool_error_result(
            f"12306 查询失败（上游服务不可用）：{e}",
            hint="可稍后重试；行程规划可先用本地交通估算兜底",
        )
    except Exception as e:  # noqa: BLE001 — Tool 边界统一兜底
        logger.warning("[TrainTool] 未预期异常: %s", e)
        return tool_error_result(f"12306 查询异常: {e}")

    if not isinstance(payload, dict) or "success" not in payload:
        return tool_error_result("12306 返回了无法识别的结构",
                                 payload=str(payload)[:300])

    queried_at = datetime.now().astimezone().isoformat(timespec="seconds")

    if payload.get("success"):
        trains = list(payload.get("trains") or [])
        trains.sort(key=lambda t: str(t.get("start_time", "")))
        trains = trains[:limit]
        for t in trains:
            t["seats"] = _localize_seats(t.get("seats") or {})
            t["source"] = "12306"
            t["updated_at"] = queried_at
        return tool_success_result({
            "from_station": payload.get("from_station", from_station),
            "to_station": payload.get("to_station", to_station),
            "date": payload.get("train_date", normalized_date),
            "count": len(trains),
            "total_matched": payload.get("count"),
            "trains": trains,
            "source": "12306",
            "queried_at": queried_at,
        })

    failure = _classify_failure(payload)
    if failure == "no_result":
        # 「查不到」是确定的业务答案：成功封套 + 空列表 + 建议
        return tool_success_result({
            "from_station": from_station,
            "to_station": to_station,
            "date": normalized_date,
            "count": 0,
            "trains": [],
            "note": payload.get("error") or "未找到该线路的余票",
            "suggestion": "两站间无直达车次，可考虑中转方案",
            "source": "12306",
            "queried_at": queried_at,
        })
    # 「查不了」：站名/日期等参数问题，原样带上游纠错线索
    return tool_error_result(
        f"12306 查询参数有误：{payload.get('error') or payload.get('errors')}",
        hint=payload.get("hint", ""),
        suggestions=payload.get("suggestions") or [],
    )


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

tool_registry.register(travel_train_search_tool, __file__)

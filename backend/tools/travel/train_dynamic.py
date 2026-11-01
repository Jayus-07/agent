"""tools/travel/train_dynamic.py — 12306 动态 capability 通道（T1，2026-11-02）

12306 车次/票价查询的第二条数据通道：经管理端能力中心的动态 capability
（连接 ``12306`` × 上游工具 ``query-tickets``/``query-ticket-price``）出网，
与 rollinggo live 适配器（providers/travel/live/rollinggo_mcp.py）同构——
mcp_registry 快照解析连接/凭据，复用 skills/mcp_dynamic 的统一执行核心
（运行时在场检查、SSRF 防护、read 工具直通），**不建第二套 MCP 调用路径**。

与静态直连（train.py 两个 @tool）的关系：
  - 静态 ToolSpec（travel.train.search 等）保留——它们是平台能力声明与
    治理身份；本模块换的只是**数据源通道**，上游响应契约只有一份事实源
    （train.py 模块头实测形态表），封套解析直接复用其共享函数，业务数据
    形态与静态通道完全一致（仅 data.channel 标注实际通道）。
  - 上游响应是**不可信输入**：解析与静态通道同一套结构化判定，不做
    文本匹配猜测。

通道选择与 fail-closed（live_search_service 是唯一调用方）：
  1. 动态优先：runtime 在场（连接启用 + 工具 approved + 内容未漂移）→
     动态通道；通道内失败如实报错，**绝不回退静态直连**（撤销≠回退）。
  2. fail-closed 判别：连接已登记（快照里有同址连接）但工具 runtime 不在
     场 = 未审批/已撤销/内容漂移 → 如实拒绝，同样不回退静态绕过治理。
  3. 静态回退：动态**从未配置**（快照无同址连接）且 TRAIN_MCP_ENABLED=true
     → 静态直连（部署者未接入能力中心时的既有行为）。
  4. 如实关停：以上皆不满足 → 「未启用」。
  边界：快照 connections 只含 enabled 连接——整条连接被停用时快照不可见，
  会落回第 3/4 态；因此接入能力中心的部署应将 TRAIN_MCP_ENABLED 关闭
  （语义收窄为「静态回退通道开关」），使所有 runtime 不在场状态都如实拒绝。
"""
from __future__ import annotations

import os
from typing import Any

from backend.config import mcp as MCP_CFG
from backend.shared.logger import logger
from backend.shared.tool_envelope import tool_error_result
from backend.tools.travel.train import (
    CHANNEL_MCP_DYNAMIC,
    _validate_date,
    price_envelope_from_payload,
    search_envelope_from_payload,
)

# 与管理端登记的连接名/上游工具名对应（capability slug 由
# mcp_registry.specs.capability_slug 派生：连接名 12306 数字开头 → t12306；
# 改名需同步管理端与本配置，env 可覆盖）。
_TRAIN_SEARCH_CAPABILITY = os.getenv(
    "TRAVEL_TRAIN_MCP_CAPABILITY", "mcp.t12306__query_tickets").strip()
_TRAIN_PRICE_CAPABILITY = os.getenv(
    "TRAVEL_TRAIN_MCP_PRICE_CAPABILITY", "mcp.t12306__query_ticket_price").strip()

# 快照里"同址连接"的判别基准：12306 动态连接登记的 base_url 与静态直连
# 同址（govtest 已验证配置复用）。连接 id 是随机 uuid 不可由名称推导，
# 故按 base_url 匹配（T1 清理 govtest 残留后无同址歧义）。
_CHANNEL_URL_REF = "TRAIN_MCP_BASE_URL"

# 通道状态（train_channel 返回值）
CHANNEL_DYNAMIC = "dynamic"                    # runtime 在场 → 动态通道
CHANNEL_CONFIGURED_INACTIVE = "configured_inactive"  # 已登记但未生效 → fail-closed
CHANNEL_ABSENT = "absent"                      # 从未配置 → 静态回退/如实关停


def train_search_capability() -> str:
    """车次查询的动态 capability。"""
    return _TRAIN_SEARCH_CAPABILITY


def train_price_capability() -> str:
    """票价查询的动态 capability。"""
    return _TRAIN_PRICE_CAPABILITY


def train_channel(capability: str) -> tuple[str, str]:
    """动态通道状态判定（三态 + fail-closed，见模块头）。

    Returns:
        (状态, 拒绝原因)。拒绝原因仅 configured_inactive 态非空。
    """
    from backend.infra.mcp_registry import store as mcp_store

    if mcp_store.get_runtime(capability) is not None:
        return CHANNEL_DYNAMIC, ""
    snapshot = mcp_store.snapshot()
    base_url = getattr(MCP_CFG, _CHANNEL_URL_REF, "")
    if base_url and any(
        conn.base_url == base_url
        for conn in snapshot.connections.values()
    ):
        return CHANNEL_CONFIGURED_INACTIVE, (
            f"12306 动态通道已登记但未生效（{capability} 未审批/已撤销或"
            "内容漂移），不回退静态直连绕过治理")
    return CHANNEL_ABSENT, ""


def _invoke_dynamic(capability: str, params: dict) -> Any:
    """经动态 MCP 统一执行核心出网（read 工具；测试在此桩替换）。"""
    from backend.infra.mcp_client import McpClientError
    from backend.skills.mcp_dynamic.skill import _McpDynamicToolAdapter

    return _McpDynamicToolAdapter(capability).invoke(dict(params))


class TrainDynamicSearchChannel:
    """车次查询动态通道：与静态 Tool 同签名的封套执行器（duck-typed .invoke）。

    治理身份不变（ledger/trace 仍记 travel_train_search_tool），仅数据源
    通道换成动态 capability；参数校验与错误封套形态与静态工具一致。
    """

    name = "travel_train_search_tool"  # governance/trace 身份（静态 ToolSpec）

    def invoke(self, params: dict) -> str:
        from_station = str((params or {}).get("from_station") or "").strip()
        to_station = str((params or {}).get("to_station") or "").strip()
        limit = (params or {}).get("limit", 20)
        if not from_station or not to_station:
            return tool_error_result("from_station 与 to_station 不能为空")
        normalized_date = _validate_date((params or {}).get("date") or "")
        if normalized_date is None:
            return tool_error_result(
                f"date 格式无法解析：{(params or {}).get('date')!r}，"
                "应为 YYYY-MM-DD（如 2026-10-03）")
        if isinstance(limit, bool) or not isinstance(limit, int) \
                or not 1 <= limit <= 50:
            return tool_error_result("limit 必须是 1 到 50 的整数")
        return _search_via_mcp(
            from_station, to_station, normalized_date, limit,
            train_search_capability())


class TrainDynamicPriceChannel:
    """票价查询动态通道（与搜索侧同构：治理身份不变，仅换数据源通道）。"""

    name = "travel_train_price_tool"  # governance/trace 身份（静态 ToolSpec）

    def invoke(self, params: dict) -> str:
        from_station = str((params or {}).get("from_station") or "").strip()
        to_station = str((params or {}).get("to_station") or "").strip()
        train_code = str((params or {}).get("train_code") or "").strip()
        if not from_station or not to_station or not train_code:
            return tool_error_result(
                "from_station、to_station 与 train_code 不能为空")
        normalized_date = _validate_date((params or {}).get("train_date") or "")
        if normalized_date is None:
            return tool_error_result(
                f"train_date 格式无法解析：{(params or {}).get('train_date')!r}，"
                "应为 YYYY-MM-DD（如 2026-10-03）")
        return _price_via_mcp(
            from_station, to_station, normalized_date, train_code,
            train_price_capability())


def _search_via_mcp(
    from_station: str, to_station: str, date: str, limit: int,
    capability: str,
) -> str:
    """动态通道出网 + 契约解析（协议层失败 → 失败封套，不吞不猜）。"""
    arguments = {
        "from_station": from_station,
        "to_station": to_station,
        "train_date": date,
    }
    try:
        payload = _invoke_dynamic(capability, arguments)
    except Exception as e:  # noqa: BLE001 — 与静态 Tool 边界兜底同口径
        logger.warning("[TrainDynamic] 车次动态通道调用失败: %s", e)
        return tool_error_result(
            f"12306 查询失败（动态通道不可用）：{e}",
            hint="可稍后重试；行程规划可先用本地交通估算兜底",
        )
    return search_envelope_from_payload(
        payload,
        from_station=from_station,
        to_station=to_station,
        date=date,
        limit=limit,
        channel=CHANNEL_MCP_DYNAMIC,
    )


def _price_via_mcp(
    from_station: str, to_station: str, date: str, train_code: str,
    capability: str,
) -> str:
    arguments = {
        "from_station": from_station,
        "to_station": to_station,
        "train_date": date,
        "train_code": train_code,
    }
    try:
        payload = _invoke_dynamic(capability, arguments)
    except Exception as e:  # noqa: BLE001
        logger.warning("[TrainDynamic] 票价动态通道调用失败: %s", e)
        return tool_error_result(
            f"12306 票价查询失败（动态通道不可用）：{e}",
            hint="可稍后重试；未返回票价时不展示参考金额",
        )
    return price_envelope_from_payload(
        payload,
        from_station=from_station,
        to_station=to_station,
        train_date=date,
        train_code=train_code,
        channel=CHANNEL_MCP_DYNAMIC,
    )


__all__ = [
    "CHANNEL_ABSENT",
    "CHANNEL_CONFIGURED_INACTIVE",
    "CHANNEL_DYNAMIC",
    "TrainDynamicPriceChannel",
    "TrainDynamicSearchChannel",
    "train_channel",
    "train_price_capability",
    "train_search_capability",
]

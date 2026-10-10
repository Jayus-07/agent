"""travel/tools/spec.py — Domain Tool 治理规格（travel-domain-design-v5.md §12 冻结）

与 capabilities.yaml 的边界：后者管主图 Skill 面（travel.poi_search 唯一
注册项），本表管域内治理面（副作用级别/确认要求/Agent 白名单），两者
不重复不冲突（G2）。治理规则：
  1. TRANSACTION 只经 Commerce Agent（守护测试断言）；
  2. requires_confirmation=True 必须先有审批单（travel-domain-design-v5.md §14）才能执行；
  3. Agent→Tool 白名单 = 本表 allowed_agents 的逆向视图，Phase 3 接入
     BaseAgent 后由守护测试静态断言 import 面不越界。
标记「契约位」的工具：规格先行，实现等真实数据源（BLOCKED 纪律）。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from backend.travel.core.agent_base import (
    AGENT_ASSISTANT,
    AGENT_COMMERCE,
    AGENT_PLANNING,
    AGENT_REQUIREMENT,
    AGENT_RESEARCH,
    AGENT_OPTIMIZATION,
)


class SideEffect(str, enum.Enum):
    """副作用四级（travel-domain-design-v5.md §12 冻结）。"""

    READ = "read"          # 检索/读取
    COMPUTE = "compute"    # 纯计算
    WRITE = "write"        # 域内落库（软失败）
    TRANSACTION = "transaction"  # 外部副作用/资金相关


@dataclass(frozen=True)
class ToolSpec:
    """单个 Domain Tool 的治理规格。capability = 工具能力词表（非主图
    capability 注册）；fee_bearing=True 表示收费/资金相关。"""

    name: str
    capability: tuple[str, ...]
    side_effect: SideEffect
    requires_confirmation: bool
    allowed_agents: tuple[str, ...]
    fee_bearing: bool = False
    note: str = ""


TOOL_SPECS: tuple[ToolSpec, ...] = (
    # —— READ：检索/读取面 ——
    ToolSpec(
        name="travel.search_poi",
        capability=("search",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_RESEARCH, AGENT_ASSISTANT),
        note="已有冻结；Skill 底座 travel_poi 用法不在 Agent 白名单管辖内",
    ),
    ToolSpec(
        name="travel.poi.detail",
        capability=("detail",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_RESEARCH, AGENT_ASSISTANT),
        note="Phase 4 补位：腾讯 Place 补全（unverified 标注透传）",
    ),
    ToolSpec(
        name="map.merchant.search",
        capability=("merchant", "food", "hotel"),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_RESEARCH, AGENT_ASSISTANT),
        note=("已由 map_merchant_search_tool 实现；高德真实商户数据，"
              "types 由服务端配置/调用方筛选"),
    ),
    ToolSpec(
        name="travel.weather.query",
        capability=("weather",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_RESEARCH, AGENT_ASSISTANT),
        note="Phase 3 补位（现状缺位，expert 直调 Provider）",
    ),
    ToolSpec(
        name="travel.retrieve_knowledge",
        capability=("knowledge",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_RESEARCH, AGENT_ASSISTANT),
        note="已有；Phase 3 补 4s 显式超时",
    ),
    ToolSpec(
        name="travel.memory.search",
        capability=("memory",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_REQUIREMENT, AGENT_ASSISTANT, AGENT_COMMERCE),
        note="travel/memory/ 模块对外入口",
    ),
    # —— COMPUTE：纯计算面 ——
    ToolSpec(
        name="travel.calculate_route",
        capability=("route",),
        side_effect=SideEffect.COMPUTE,
        requires_confirmation=False,
        allowed_agents=(AGENT_OPTIMIZATION,),
        note="已有冻结；route_km 保持纯函数",
    ),
    ToolSpec(
        name="route.optimizer",
        capability=("route",),
        side_effect=SideEffect.COMPUTE,
        requires_confirmation=False,
        allowed_agents=(AGENT_OPTIMIZATION,),
        note="Phase 3 接口 + 2-opt 实现（不引 OR-Tools）",
    ),
    ToolSpec(
        name="travel.calculate_budget",
        capability=("budget",),
        side_effect=SideEffect.COMPUTE,
        requires_confirmation=False,
        allowed_agents=(AGENT_OPTIMIZATION,),
        note="已有冻结",
    ),
    ToolSpec(
        name="travel.plan.diff",
        capability=("diff",),
        side_effect=SideEffect.COMPUTE,
        requires_confirmation=False,
        allowed_agents=(AGENT_ASSISTANT,),
        note="core/plan_diff.py 的 Tool 面别名（reporter 直接函数调用）",
    ),
    ToolSpec(
        name="calendar.export",
        capability=("calendar",),
        side_effect=SideEffect.COMPUTE,
        requires_confirmation=False,
        allowed_agents=("reporter",),
        note="ICS 即其第一实现（D1 已修）",
    ),
    ToolSpec(
        name="map.link",
        capability=("map",),
        side_effect=SideEffect.COMPUTE,
        requires_confirmation=False,
        allowed_agents=("reporter",),
        note="Phase 6：deeplink 复用 commerce 白名单机制",
    ),
    # —— WRITE：域内落库（软失败） ——
    ToolSpec(
        name="travel.memory.save",
        capability=("memory",),
        side_effect=SideEffect.WRITE,
        requires_confirmation=False,
        allowed_agents=(AGENT_REQUIREMENT,),
        note="用户显式表达才写；软失败纪律",
    ),
    # —— TRANSACTION：外部副作用（只经 Commerce） ——
    ToolSpec(
        name="travel.hotel.book",
        capability=("hotel", "booking"),
        side_effect=SideEffect.TRANSACTION,
        requires_confirmation=True,
        allowed_agents=(AGENT_COMMERCE,),
        fee_bearing=True,
        note="经现有 booking 确认门（cfp 绑定/TTL/金额互检）",
    ),
    ToolSpec(
        name="travel.flight.book",
        capability=("flight", "booking"),
        side_effect=SideEffect.TRANSACTION,
        requires_confirmation=True,
        allowed_agents=(AGENT_COMMERCE,),
        fee_bearing=True,
        note="fake 源如实披露；live BLOCKED_BY_EXTERNAL_PROVIDER",
    ),
    ToolSpec(
        name="travel.hotel.cancel",
        capability=("hotel", "cancel"),
        side_effect=SideEffect.TRANSACTION,
        requires_confirmation=True,
        allowed_agents=(AGENT_COMMERCE,),
        fee_bearing=True,
    ),
    # —— 契约位（无真实源，implemented=False，BLOCKED 纪律） ——
    ToolSpec(
        name="travel.ticket.search",
        capability=("ticket",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_PLANNING,),
        note="契约位：ticket.facts implemented=False，不开放交易面",
    ),
    ToolSpec(
        name="travel.event.search",
        capability=("event",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_RESEARCH,),
        note="契约位：providers/events/ 无真实源，RAG 节事兜底",
    ),
    ToolSpec(
        name="travel.notice.search",
        capability=("notice",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_RESEARCH,),
        note="契约位：官方公告源 BLOCKED",
    ),
    ToolSpec(
        name="travel.currency.exchange",
        capability=("currency",),
        side_effect=SideEffect.COMPUTE,
        requires_confirmation=False,
        allowed_agents=(AGENT_OPTIMIZATION,),
        note="契约位：CNY 单币种现状无消费方",
    ),
    ToolSpec(
        name="travel.train.search",
        capability=("train",),
        side_effect=SideEffect.READ,
        requires_confirmation=False,
        allowed_agents=(AGENT_PLANNING,),
        note=("已由 travel_train_search_tool 实现；12306 MCP 只读查询，"
              "Tool 失败不得映射为空车次"),
    ),
)

TOOL_SPECS_BY_NAME: dict[str, ToolSpec] = {s.name: s for s in TOOL_SPECS}

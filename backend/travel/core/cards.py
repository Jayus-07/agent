"""travel/core/cards.py — 对话查询结果卡（F18）结构化事件

语义（TASK.md F18 / 原型「结果卡内嵌对话」）：车次/酒店/景点/餐饮等对话
查询完成时，除既有 ``tool.result``（字段级结构）外，追加一条 **独立
``tool.card`` 事件**，载荷是可直接渲染的卡片结构（类目 + 条目行）。
前端 V2 对话流按 turn 渲染；卡片动作全部直通既有提交链（聊天管线 /
ai-edit / selections），本层只描述事实，不携带写语义。

发射点（单一接线）：``tool_failure_policy.run_checked`` 成功路径——auxiliary
四类对话查询（query_train/query_hotel/query_poi/query_food）的 result_summary
已产出 ``category + preview``（≤6 条），白名单类目命中即发卡；失败/空结果
不发卡（失败话术走 tool.result/degraded 既有披露，卡片不伪造成功）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 允许发卡的查询类目（对话查询卡语义域；weather/guide/route 等维持
# tool.result preview 通道，不进对话卡）
CARD_CATEGORIES = frozenset({"train", "hotel", "food", "poi"})

# 单卡条目上限（后端已截断 ≤6；此处为契约防线，超限拒绝而非静默再截）
MAX_CARD_ITEMS = 8


class ToolCardPayload(BaseModel):
    """``tool.card`` 事件载荷（JSON 可序列化，前端渲染契约）。"""

    model_config = ConfigDict(extra="forbid")

    kind: str                       # train | hotel | food | poi
    title: str = Field(default="", max_length=80)  # 可空：前端按 kind 渲染标题
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_CARD_ITEMS)
    provider: str = ""
    disclosure: str = ""            # 时效/来源如实披露（可空；缺失不补造）

    @field_validator("kind")
    @classmethod
    def _kind_allowed(cls, value: str) -> str:
        if value not in CARD_CATEGORIES:
            raise ValueError(f"card kind 非法: {value}")
        return value

    @field_validator("items")
    @classmethod
    def _items_dicts(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("card items 必须是对象列表")
        return value


def card_from_summary(
    summary: dict[str, Any] | None, *, title: str,
) -> ToolCardPayload | None:
    """result_summary（category/preview/provider）→ 卡片；不可发卡 = None。

    规则：类目不在白名单、preview 为空、条目全非对象 → 不发卡
    （空结果是事实，失败是披露，都不伪造卡片）。
    """
    if not isinstance(summary, dict):
        return None
    category = str(summary.get("category") or "")
    if category not in CARD_CATEGORIES:
        return None
    preview = summary.get("preview")
    if not isinstance(preview, list):
        return None
    items = [item for item in preview if isinstance(item, dict)]
    if not items:
        return None
    try:
        return ToolCardPayload(
            kind=category,
            title=title,
            items=items[:MAX_CARD_ITEMS],
            provider=str(summary.get("provider") or ""),
        )
    except Exception:  # noqa: BLE001 — 卡片构造失败不改变业务结果
        return None


def emit_tool_card(
    payload: ToolCardPayload, *, agent: str, tool: str,
    tool_call_id: str, task_id: str = "",
) -> dict:
    """发 ``tool.card`` 事件（sink 投递失败由事件总线旁路兜底，不影响业务）。"""
    from backend.travel.core.events import emit_travel_event

    fields: dict[str, Any] = {
        "card": payload.model_dump(),
        "tool_call_id": tool_call_id,
    }
    if task_id:
        fields["task_id"] = str(task_id)[:64]
    return emit_travel_event("tool.card", agent=agent, tool=tool, **fields)


__all__ = [
    "CARD_CATEGORIES",
    "MAX_CARD_ITEMS",
    "ToolCardPayload",
    "card_from_summary",
    "emit_tool_card",
]

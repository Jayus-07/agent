"""skills/map/skill.py — 地图信息查询 Skill（聚合入口）

Capability: map.lookup

本 Skill 只声明 capability 并引用 Tool 层的聚合 Tool；聚合入口的设计理由
与不覆盖范围见 ``backend/tools/map/lookup.py`` 的模块文档。

分层约定（2026-09-16 归位）：Skill 不定义 Tool。聚合 Tool 与它内部的
action 分发表属于 Tool 层（backend/tools/map/lookup.py）并已在模块底部
注册进 tool_registry。此处仅做 re-export，保持既有导入路径可用。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.skills.base import BaseSkill
from backend.tools.map.lookup import ACTIONS, map_lookup_tool  # noqa: F401


class MapLookupSkill(BaseSkill):
    name = "map_lookup"
    output_type = "text"

    @property
    def _tool_fn(self):
        return map_lookup_tool


async def map_lookup_skill_node(state: dict) -> dict:
    """Skill 节点函数（供主图 Planner 的 DAG 调用）"""
    skill = MapLookupSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "map.lookup")
    logger.info("[MapLookupSkill] step=%s cap=%s", state.get("current_step_id"), cap)
    return await skill.execute(state, step_capability=cap)

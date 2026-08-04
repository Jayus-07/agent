"""
tool_registry.py — Capability 注册表（派生视图）

ADR-0001: 合并双注册表
  - 静态字典 CAPABILITY_MAP / CAPABILITY_SCHEMA 已废弃
  - 改为从 backend.orchestration.skills.registry 的 Skill 实例动态派生
  - 单一事实来源：Skill 类自身的 description/params_schema/examples

Skill 自己持有 Tool，Tool 调用 Infrastructure。
Planner → Capability → Skill → Tool → Infrastructure
"""

import json
from functools import cached_property
from typing import Dict, List, Optional

from backend.shared.logger import logger


# ── 节点名约定 ──────────────────────────────────────────────
# Skill.name + "_skill" → LangGraph 节点名
# 例: RAGSkill.name="rag" → 节点名 "rag_skill"
def _node_name(skill_name: str) -> str:
    return f"{skill_name}_skill"


class ToolRegistry:
    """Capability 派生注册表。

    所有 capability 元数据从已注册的 Skill 实例派生（不是硬编码）：
      - CAPABILITY_MAP:    capability → 节点名（f"{skill.name}_skill"）
      - CAPABILITY_SCHEMA: capability → {description, params, 示例}

    注册一个 Skill（两步）：
      1. 在 skills/<name>/skill.py 继承 BaseSkill，声明 capabilities + description
      2. 在 skills/registry.py import → 自动加入

    builder.py / system.py / planner.py 不再硬编码节点名或 capability 列表。
    """

    def __init__(self):
        self._skill_nodes: dict[str, object] = {}  # node_name → async node function

    # =====================================================
    # Skill 节点注册（Skill 包 import 时自注册）
    # =====================================================

    def register_skill_node(self, name: str, node_func):
        """Skill 包加载时自行调用，注册节点名 → 节点函数"""
        self._skill_nodes[name] = node_func
        logger.info(f"[ToolRegistry] 注册 Skill: {name}")

    def get_skill_nodes(self) -> dict:
        """返回 {node_name: node_func}（builder.py 用于 add_node）"""
        return dict(self._skill_nodes)

    def get_skill_node_names(self) -> set:
        """返回所有已注册的 Skill 节点名集合（system.py 用于事件分派）"""
        return set(self._skill_nodes.keys())

    # =====================================================
    # 派生私有方法：从 skills/registry 读 Skill 实例
    # =====================================================

    def _get_skill_registry(self) -> dict[str, "BaseSkill"]:
        """延迟读取 Skill 注册表（避免循环导入）

        所有 Skill 已在 registry 模块级注册（PR-2.x 消除外部惰性加载）。
        """
        from backend.skills.registry import _registry as skills
        return skills

    # =====================================================
    # 派生属性（替代原静态字典）
    # =====================================================

    @cached_property
    def CAPABILITY_MAP(self) -> Dict[str, str]:
        """派生：capability → LangGraph 节点名"""
        return {
            cap: _node_name(inst.name)
            for cap, inst in self._get_skill_registry().items()
        }

    @cached_property
    def CAPABILITY_SCHEMA(self) -> Dict[str, dict]:
        """派生：capability → Planner prompt schema"""
        result = {}
        for cap, inst in self._get_skill_registry().items():
            result[cap] = {
                "description": inst.description,
                "params": dict(inst.params_schema),
                "示例": inst.examples[0] if inst.examples else {},
            }
        return result

    # =====================================================
    # 公开 API（保持兼容）
    # =====================================================

    def get_node(self, capability: str) -> Optional[str]:
        """根据 capability 获取对应的图节点名"""
        node = self.CAPABILITY_MAP.get(capability)
        if not node:
            logger.warning(f"[ToolRegistry] 未知 capability: {capability}")
        return node

    def get_worker(self, capability: str) -> Optional[str]:
        """向后兼容别名（同 get_node）"""
        return self.get_node(capability)

    def get_schema(self, capability: str) -> Optional[dict]:
        """获取 capability 的参数 schema"""
        return self.CAPABILITY_SCHEMA.get(capability)

    def get_available_capabilities(self) -> List[str]:
        """返回所有可用的 capability 列表"""
        return list(self.CAPABILITY_MAP.keys())

    def get_capabilities_description(self) -> str:
        """生成 Planner prompt 用的能力描述文本"""
        lines = []
        for cap_name, schema in self.CAPABILITY_SCHEMA.items():
            lines.append(f"  - {cap_name}: {schema['description']}")
        return "\n".join(lines)

    def get_capabilities_schema_text(self) -> str:
        """生成完整的 capability schema 文本，用于 Critique prompt"""
        lines = []
        for cap_name in self.get_available_capabilities():
            schema = self.get_schema(cap_name)
            if not schema:
                continue
            lines.append(f"### {cap_name}")
            lines.append(f"描述: {schema['description']}")
            lines.append(f"参数: {json.dumps(schema['params'], ensure_ascii=False)}")
            if "示例" in schema:
                lines.append(f"示例: {json.dumps(schema['示例'], ensure_ascii=False)}")
            lines.append("")
        return "\n".join(lines)

    def invalidate_cache(self):
        """清除 cached_property 缓存（测试 / 动态加载新 Skill 后调用）"""
        for attr in ("CAPABILITY_MAP", "CAPABILITY_SCHEMA"):
            if attr in self.__dict__:
                del self.__dict__[attr]


# 全局单例
tool_registry = ToolRegistry()

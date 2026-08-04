"""
skills/registry.py — Skill 注册中心

以 Capability 为 Key，集中管理所有 Skill 实例。
Supervisor 通过 capability 直接找到对应 Skill。

新增 Skill:
  1. 创建 skills/<name>/skill.py → 继承 BaseSkill，声明 capabilities + description
  2. 在此 import 即可自动注册（ADR-0001：单一事实来源）
  3. tool_registry.py 不再需要手动维护 capability 映射（自动派生）
"""

from backend.skills.sql.skill import SQLSkill
from backend.skills.rag.skill import RAGSkill
from backend.skills.report.skill import ReportSkill
from backend.skills.email.skill import EmailSkill
from backend.skills.data_export.skill import DataExportSkill
from backend.skills.web_search.skill import WebSearchSkill
from backend.skills.web_crawl.skill import WebCrawlSkill
from backend.skills.data_collection.skill import DataCollectionSkill, data_collection_skill_node

# 全局实例（PR-2.x: DataCollection 已从外部惰性加载升级为内置注册）
_instances: list = [
    SQLSkill(),
    RAGSkill(),
    ReportSkill(),
    EmailSkill(),
    DataExportSkill(),
    WebSearchSkill(),
    WebCrawlSkill(),
    DataCollectionSkill(),
]

# capability → Skill 实例
_registry: dict[str, "BaseSkill"] = {}
for _inst in _instances:
    for _cap in _inst.capabilities:
        _registry[_cap] = _inst

# 注册 LangGraph 节点函数
from backend.orchestration.tool_registry import tool_registry
tool_registry.register_skill_node("data_collection_skill", data_collection_skill_node)


def get(capability: str):
    """按 Capability 获取 Skill 实例"""
    return _registry.get(capability)


def get_skill(name: str):
    """按 Skill name 获取实例（向后兼容）"""
    for _inst in _instances:
        if _inst.name == name:
            return _inst
    return None


def list_capabilities() -> dict:
    """列出所有已注册 Capability → Skill"""
    return {cap: skill.name for cap, skill in _registry.items()}


def list_skills() -> dict:
    """列出所有 Skill 实例"""
    return {inst.name: inst.capabilities for inst in _instances}

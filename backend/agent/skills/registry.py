"""
skills/registry.py — Skill 注册中心

以 Capability 为 Key，集中管理所有 Skill 实例。
Supervisor 通过 capability 直接找到对应 Skill。

新增 Skill:
  1. 创建 skills/<name>/skill.py → 继承 BaseSkill，声明 capabilities
  2. 在此 import 即可自动注册
  3. 在 tool_registry.py 添加 capability → node_name 映射
"""

from backend.agent.skills.sql.skill import SQLSkill
from backend.agent.skills.rag.skill import RAGSkill
from backend.agent.skills.report.skill import ReportSkill

# 全局实例（内置 Skill 直接加载，外部 Skill 惰性加载避免循环导入）
_instances: list = [
    SQLSkill(),
    RAGSkill(),
    ReportSkill(),
]


def _register_external_skills():
    """惰性加载外部模块 Skill（避免循环导入）"""
    if not hasattr(_register_external_skills, "_done"):
        try:
            from backend.data_collection.skill import DataCollectionSkill
            _instances.append(DataCollectionSkill())
            for _cap in DataCollectionSkill.capabilities:
                _registry[_cap] = _instances[-1]
        except ImportError:
            pass
        _register_external_skills._done = True

# capability → Skill 实例
_registry: dict[str, "BaseSkill"] = {}
for _inst in _instances:
    for _cap in _inst.capabilities:
        _registry[_cap] = _inst


def get(capability: str):
    """按 Capability 获取 Skill 实例"""
    _register_external_skills()
    return _registry.get(capability)


def get_skill(name: str):
    """按 Skill name 获取实例（向后兼容）"""
    _register_external_skills()
    for _inst in _instances:
        if _inst.name == name:
            return _inst
    return None


def list_capabilities() -> dict:
    """列出所有已注册 Capability → Skill"""
    _register_external_skills()
    return {cap: skill.name for cap, skill in _registry.items()}


def list_skills() -> dict:
    """列出所有 Skill 实例"""
    _register_external_skills()
    return {inst.name: inst.capabilities for inst in _instances}

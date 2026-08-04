"""
data_collection/skill.py — 向后兼容 re-export（PR-2.x 已迁移到 orchestration/skills/）。

DataCollectionSkill 已移至 orchestration/skills/data_collection/skill.py。
本模块保留旧 import 路径兼容。
"""
from backend.skills.data_collection.skill import (  # noqa: F401
    DataCollectionSkill,
    data_collection_skill_node,
)

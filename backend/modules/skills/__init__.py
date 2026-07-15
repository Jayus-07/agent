"""modules.skills — 业务 Skill 集合（re-export from backend.agent.skills）

注: 不重建抽象层。当前 BaseSkill + SkillRegistry 已满足需求。
   本目录仅作为 task 规范要求的统一入口。
"""
from backend.agent.skills.base import BaseSkill, execute_with_retry
from backend.agent.skills.registry import get_skill, list_skills, list_capabilities
from backend.agent.skills.sql.skill import SQLSkill, sql_skill_node
from backend.agent.skills.rag.skill import RAGSkill, rag_skill_node
from backend.agent.skills.report.skill import ReportSkill, report_skill_node

__all__ = [
    "BaseSkill",
    "execute_with_retry",
    "get_skill",
    "list_skills",
    "list_capabilities",
    "SQLSkill",
    "sql_skill_node",
    "RAGSkill",
    "rag_skill_node",
    "ReportSkill",
    "report_skill_node",
]
"""
skills/ — Skill 包集合

每个 Skill 是独立的子包:
  skills/sql/      — 数据库查询
  skills/rag/      — 知识库检索
  skills/report/   — 报告生成

后续扩展:
  skills/memory/   — 记忆检索
  skills/search/   — 网页搜索
  skills/image/    — 图像分析

公共:
  skills/base.py      — BaseSkill 抽象类
  skills/registry.py  — Skill 注册中心
"""

from backend.orchestration.skills.base import BaseSkill, execute_with_retry
from backend.orchestration.skills.registry import get_skill, list_skills
from backend.orchestration.skills.sql.skill import SQLSkill, sql_skill_node
from backend.orchestration.skills.rag.skill import RAGSkill, rag_skill_node
from backend.orchestration.skills.report.skill import ReportSkill, report_skill_node
from backend.orchestration.skills.email.skill import EmailSkill, email_skill_node
from backend.orchestration.skills.data_export.skill import DataExportSkill, data_export_skill_node
from backend.orchestration.skills.web_search.skill import WebSearchSkill, web_search_skill_node

__all__ = [
    "BaseSkill", "execute_with_retry",
    "get_skill", "list_skills",
    "SQLSkill", "sql_skill_node",
    "RAGSkill", "rag_skill_node",
    "ReportSkill", "report_skill_node",
    "EmailSkill", "email_skill_node",
    "DataExportSkill", "data_export_skill_node",
    "WebSearchSkill", "web_search_skill_node",
]

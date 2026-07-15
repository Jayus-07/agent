"""prompts — 所有 LLM Prompt 集中管理（与代码分离）

子模块:
  planner    — PLANNER_SYSTEM + 知识库关键词 + is_knowledge_question
  critique   — PLAN_CRITIQUE_SYSTEM（Plan Reviewer）
  reporter   — REPORTER_SYSTEM（最终回答汇总）

修改原则:
  - 不在 .py 业务代码中拼 Prompt 字符串
  - 所有 Prompt 集中在此目录
  - 添加新 Prompt 时新建独立文件

注：_format_capabilities_schema() 因依赖 tool_registry 保留在
   backend/agent/planner/planner.py 中（避免循环导入）。
"""
from backend.prompts.planner import PLANNER_SYSTEM, is_knowledge_question
from backend.prompts.critique import PLAN_CRITIQUE_SYSTEM
from backend.prompts.reporter import REPORTER_SYSTEM

__all__ = [
    "PLANNER_SYSTEM",
    "PLAN_CRITIQUE_SYSTEM",
    "REPORTER_SYSTEM",
    "is_knowledge_question",
]
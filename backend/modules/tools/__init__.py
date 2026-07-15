"""modules.tools — Tool 集合（re-export from backend.agent.tools）

注: 不重建抽象层。当前 LangChain Tool 已满足需求。
   本目录仅作为 task 规范要求的统一入口。
"""
from backend.agent.tools import (
    search_knowledge_tool,
    sql_query_tool,
    generate_report_tool,
)

__all__ = [
    "search_knowledge_tool",
    "sql_query_tool",
    "generate_report_tool",
]
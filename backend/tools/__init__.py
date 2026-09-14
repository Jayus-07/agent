"""tools — 统一 Tool 层（PR-2.x 从 orchestration/ + data_collection/ 收敛）。

LangChain Tool 封装，零侵入接入已有子系统。
Skill → Tool → Infrastructure (RAG / SQL / Report)

包含以下 Tools：
- sql.py:              execute_sql_tool, sql_query_tool (已修复重复定义)
- rag.py:              search_knowledge_tool
- report.py:           generate_report_tool, run_report
- web.py:              web_search_tool, web_crawl_tool
- email.py:            send_email_tool
- export.py:           export_csv_tool
- data_collection.py:  data_collection_tool
- competitor.py:       competitor_analyze_tool
- memory.py:           memory_search_tool, memory_store_tool
- calculator.py:       calculate_tool
- travel/:             travel_poi_search_tool + 距离/费用估算纯函数（旅游域）
- session.py:          set_session_id / set_tool_user_id (contextvar 辅助工具)

Tool Registry:
- tool_registry.py:    注册中心与重复定义检测 (P0 防护)
"""
from backend.tools.session import (
    set_session_id, _get_session_id, _current_session_id,
    set_tool_user_id, get_tool_user_id,  # noqa: F401
    set_tool_department, get_tool_department,  # noqa: F401
)
from backend.tools.sql import execute_sql_tool, sql_query_tool  # noqa: F401
from backend.tools.rag import search_knowledge_tool  # noqa: F401
from backend.tools.report import generate_report_tool, run_report  # noqa: F401
from backend.tools.export import export_csv_tool  # noqa: F401
from backend.tools.web import web_search_tool, web_crawl_tool  # noqa: F401
from backend.tools.email import send_email_tool  # noqa: F401
from backend.tools.data_collection import data_collection_tool  # noqa: F401
from backend.tools.competitor import (  # noqa: F401
    competitor_analyze_tool, competitor_watch_tool,
    competitor_history_tool, competitor_watchlist_tool,
)
from backend.tools.memory import memory_search_tool, memory_store_tool  # noqa: F401
from backend.tools.calculator import calculate_tool  # noqa: F401
from backend.tools.travel import travel_poi_search_tool  # noqa: F401

from backend.tools.tool_registry import tool_registry as _tool_registry
from backend.shared.logger import logger as _logger
_tool_count = len(_tool_registry._registered_tools)
_logger.info(f"[ToolRegistry] 已加载 {_tool_count} 个 Tool")
del _tool_registry, _logger, _tool_count

# ==================== Tool Registry 手动注册 ====================
# 每个工具模块在导入时自动注册自己的工具（见各模块内部）
# 这里只需导出 tool_registry 供外部使用
__all__ = [
    'set_session_id',
    '_get_session_id',
    '_current_session_id',
    'set_tool_user_id',
    'get_tool_user_id',
    'execute_sql_tool',
    'sql_query_tool',
    'search_knowledge_tool',
    'generate_report_tool',
    'run_report',
    'export_csv_tool',
    'web_search_tool',
    'web_crawl_tool',
    'send_email_tool',
    'data_collection_tool',
    'competitor_analyze_tool',
    'competitor_watch_tool',
    'competitor_history_tool',
    'competitor_watchlist_tool',
    'memory_search_tool',
    'memory_store_tool',
    'calculate_tool',
    'travel_poi_search_tool',
    'tool_registry',  # ✅ 供外部访问
]

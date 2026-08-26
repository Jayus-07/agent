"""tools — 统一 Tool 层（PR-2.x 从 orchestration/ + data_collection/ 收敛）。

LangChain Tool 封装，零侵入接入已有子系统。
Skill → Tool → Infrastructure (RAG / SQL / Report)

目录:
  - sql.py:              execute_sql_tool, sql_query_tool
  - rag.py:              search_knowledge_tool
  - report.py:           generate_report_tool, run_report
  - export.py:           export_csv_tool
  - web.py:              web_search_tool, web_crawl_tool
  - email.py:            send_email_tool
  - data_collection.py:  data_collection_tool
  - competitor.py:        competitor_analyze_tool
  - session.py:          set_session_id, _get_session_id (contextvar 工具)
"""
from backend.tools.session import set_session_id, _get_session_id, _current_session_id  # noqa: F401
from backend.tools.sql import execute_sql_tool, sql_query_tool  # noqa: F401
from backend.tools.rag import search_knowledge_tool  # noqa: F401
from backend.tools.report import generate_report_tool, run_report  # noqa: F401
from backend.tools.export import export_csv_tool  # noqa: F401
from backend.tools.web import web_search_tool, web_crawl_tool  # noqa: F401
from backend.tools.email import send_email_tool  # noqa: F401
from backend.tools.data_collection import data_collection_tool  # noqa: F401
from backend.tools.competitor import competitor_analyze_tool  # noqa: F401

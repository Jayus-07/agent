"""
tools.py — LangChain Tool 封装（零侵入接入已有子系统）

将 sql_agent / retrieval / report_agent 包装为标准 Tool 对象。
Skill 通过 Tool.invoke() 调用，不直接依赖子系统的内部实现。

Skill → Tool → Infrastructure (RAG / SQL / Report)
"""

from langchain_core.tools import tool

from backend.shared.logger import logger

# =====================================================
# 懒加载单例（首次调用时初始化，避免启动时全部加载）
# =====================================================

_sql_agent = None


def _get_sql_agent():
    global _sql_agent
    if _sql_agent is None:
        from backend.config import DB_CONFIG
        from backend.sql.sql_agent import init_sql_agent
        _sql_agent = init_sql_agent(dict(DB_CONFIG), max_retries=2)
    return _sql_agent


def _get_rag_pipeline():
    """获取 RAG Pipeline 单例（统一入口，避免双重初始化）"""
    from backend.app.api.deps import get_rag_pipeline
    return get_rag_pipeline()


# =====================================================
# Tool 定义
# =====================================================

@tool
def sql_query_tool(question: str) -> str:
    """
    查询 PostgreSQL 数据库中的结构化数据。
    输入自然语言问题，返回 Markdown 格式的查询结果表格。
    适用场景：数据统计、排行、筛选、聚合、对比分析。
    """
    logger.info(f"[Tool:sql_query] 问题: {question[:80]}...")
    agent = _get_sql_agent()
    return agent.ask(question, current_user_id=None)


@tool
def search_knowledge_tool(question: str, kb_id: str = "default") -> str:
    """
    从指定知识库检索文档内容、经验、最佳实践等。
    输入检索问题和知识库ID，返回基于相关文档生成的回答。
    适用场景：概念解释、经验查询、流程规范、技术方案参考。
    """
    logger.info(f"[Tool:search_knowledge] 检索: {question[:80]}... (kb={kb_id})")
    pipeline = _get_rag_pipeline()
    return pipeline.ask(question, session_id="multi-agent-default", kb_id=kb_id)


@tool
def generate_report_tool(report_type: str, filters: dict = None) -> str:
    """
    生成结构化 Markdown 报告（含图表）。
    报告类型需是已注册的类型。
    适用场景：需要输出的正式报告、数据分析汇总。
    """
    filters = filters or {}
    logger.info(f"[Tool:generate_report] 类型={report_type}, 筛选={filters}")
    from backend.report.report_generator import generate_report
    return generate_report(report_type, filters, user_id="multi-agent", polish=False)

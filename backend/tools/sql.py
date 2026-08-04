"""SQL 工具 — 自然语言查数据库 + 原始 SQL 执行。"""
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
def execute_sql_tool(query: str) -> str:
    """
    直接执行原始 SQL 查询 PostgreSQL。
    输入 SQL SELECT 语句，返回 JSON 格式的查询结果。
    适用场景：Workflow step 中的确定性数据拉取（不经过 NL→SQL Agent）。
    """
    import json as _json
    import psycopg2
    from backend.config import DB_CONFIG

    logger.info(f"[Tool:execute_sql] {query[:80]}...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description] if cur.description else []
        result = [dict(zip(cols, row)) for row in rows]
        cur.close()
        conn.close()
        logger.info(f"[Tool:execute_sql] 返回 {len(result)} 行")
        return _json.dumps({"rows": result, "total": len(result)}, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"[Tool:execute_sql] 失败: {e}")
        raise


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


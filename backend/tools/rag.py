"""RAG 工具 — 知识库检索。"""
from langchain_core.tools import tool
from backend.shared.logger import logger


def _get_rag_pipeline():
    """获取 RAG Pipeline 单例（统一入口，避免双重初始化）。"""
    from backend.app.api.deps import get_rag_pipeline
    return get_rag_pipeline()


@tool
def search_knowledge_tool(question: str, kb_id: str = "default") -> str:
    """
    从指定知识库检索文档内容、经验、最佳实践等。
    输入检索问题和知识库ID，返回基于相关文档生成的回答。
    适用场景：概念解释、经验查询、流程规范、技术方案参考。
    """
    logger.info(f"[Tool:search_knowledge] 检索: {question[:80]}... (kb={kb_id})")
    pipeline = _get_rag_pipeline()
    from backend.tools.session import _get_session_id
    sid = _get_session_id()
    return pipeline.ask(question, session_id=sid, kb_id=kb_id)

"""RAG 工具 — 知识库检索。"""
from langchain_core.tools import tool
from backend.shared.logger import logger


def _get_rag_pipeline():
    """获取 RAG Pipeline 单例（统一入口，避免双重初始化）。"""
    from backend.rag.pipeline import get_rag_pipeline
    return get_rag_pipeline()


@tool
def search_knowledge_tool(question: str, kb_id: str = "default") -> str:
    """
    从指定知识库检索文档内容、经验、最佳实践等。
    输入检索问题和知识库ID，返回基于相关文档生成的回答。
    适用场景：概念解释、经验查询、流程规范、技术方案参考。
    """
    logger.info(f"[Tool:search_knowledge] 检索：{question[:80]}... (kb={kb_id})")
    pipeline = _get_rag_pipeline()
    from backend.tools.session import (
        _get_session_id,
        get_tool_department,
        get_tool_permissions,
        get_tool_user_id,
    )
    sid = _get_session_id()
    # 主体解析（2026-09-22 Chat/RAG 收口修正）：
    #   - 带部门 → employee（按 owner_depts 矩阵授权）；
    #   - 已登录但未声明部门 → employee + 空部门（authorized_kbs 语义：员工
    #     未带部门只见 "all" 库，即 policy_general）——此前误判成 customer，
    #     导致内部政策库从聊天主链整体不可达（「知识库暂无相关资料」根因）；
    #   - 未登录（guest/api-key 匿名通道）→ customer fail-safe（宁严勿漏），
    #     仅 audience=customer 的 cs_* 库可见。RAG_TOOL_FAILSAFE_CUSTOMER=
    #     false 回滚为未声明主体（旧行为）。
    import os

    department = get_tool_department()
    user_id = (get_tool_user_id() or "").strip()
    authenticated = user_id not in ("", "default", "anonymous")
    if department:
        subject_type, dept = "employee", department
    elif authenticated:
        subject_type, dept = "employee", ""
    elif os.getenv("RAG_TOOL_FAILSAFE_CUSTOMER", "true").strip().lower() == "true":
        subject_type, dept = "customer", ""
    else:
        subject_type, dept = "", ""
    permissions = get_tool_permissions()
    return pipeline.ask(question, session_id=sid, kb_id=kb_id,
                        subject_type=subject_type, department=dept,
                        permissions=permissions)


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(search_knowledge_tool, __file__)

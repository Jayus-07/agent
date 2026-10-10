"""RAG 工具 — 知识库检索。"""
import json

from langchain_core.tools import tool
from backend.shared.logger import logger


def _get_rag_pipeline():
    """获取 RAG Pipeline 单例（统一入口，避免双重初始化）。"""
    from backend.rag.pipeline import get_rag_pipeline
    return get_rag_pipeline()


def append_rag_answer_meta(answer: str, answer_meta: dict) -> str:
    """把拒答状态/置信度以机器注释附在输出头部（聊天链路的文本协议）。

    聊天路径工具出口是纯文本（output_type=text，给 LLM 阅读），且 remote
    模式下 RAG 链跑在 rag-service 进程、ContextVar 跨不过 HTTP——answer_meta
    只能随文本走。格式沿用两个既有先例：「### 参考文献」（reporter 解析/
    剥离、前端视觉剥离）与 <!--META-->（MetaStreamFilter 剥离）；本标记由
    reporter（用户可见文本）与 make_done_event（done 帧结构化字段）双侧
    解析剥离，且 HTML 注释在 Markdown 渲染中天然不可见，漏剥不外显。
    头部位置：L1 输出预算截断保头部，保证标记不被截掉。
    """
    if not answer:
        return answer
    meta = answer_meta or {}
    payload: dict = {}
    status = meta.get("answer_status")
    if status:
        payload["answer_status"] = str(status)
    confidence = meta.get("confidence")
    if isinstance(confidence, (int, float)):
        payload["confidence"] = round(float(confidence), 2)
    if not payload:
        return answer
    return f"<!--RAGMETA{json.dumps(payload, ensure_ascii=False)}-->\n{answer}"


@tool
def search_knowledge_tool(question: str, kb_id: str = "default") -> str:
    """
    从指定知识库检索文档内容、经验、最佳实践等。
    输入检索问题和知识库ID，返回基于相关文档生成的回答。
    适用场景：概念解释、经验查询、流程规范、技术方案参考。
    """
    logger.info(f"[Tool:search_knowledge] 检索：{question[:80]}... (kb={kb_id})")
    pipeline = _get_rag_pipeline()
    from backend.security.principal import resolve_tool_principal
    from backend.tools.session import (
        _get_session_id,
        get_tool_department,
        get_tool_permissions,
        get_tool_tenant_id,
        get_tool_user_id,
    )
    sid = _get_session_id()
    # 主体解析（2026-09-23 授权收口）：c4b865b 的推导语义原样迁移至
    # security/principal.resolve_tool_principal（单一权威，HTTP 通道同规则）：
    #   - 带部门 → employee（按 owner_depts 矩阵授权）；
    #   - 已登录但未声明部门 → employee + 空部门（authorized_kbs 语义：员工
    #     未带部门只见 "all" 库，即 policy_general）——此前误判成 customer，
    #     导致内部政策库从聊天主链整体不可达（「知识库暂无相关资料」根因）；
    #   - 未登录（guest/api-key 匿名通道）→ customer fail-safe（宁严勿漏），
    #     仅 audience=customer 的 cs_* 库可见。RAG_TOOL_FAILSAFE_CUSTOMER=
    #     false 回滚为未声明主体（旧行为）。
    principal = resolve_tool_principal(
        user_id=get_tool_user_id(),
        department=get_tool_department(),
        permissions=get_tool_permissions(),
        tenant_id=get_tool_tenant_id(),
    )
    # ask_result（非 ask）：拒答状态/置信度随返回值带回（D1-6 口径，
    # 单例实例属性在并发下会串扰），随 RAGMETA 标记流出聊天链路
    outcome = pipeline.ask_result(question, session_id=sid, kb_id=kb_id,
                                  subject_type=principal.subject_type,
                                  department=principal.department,
                                  permissions=principal.permissions,
                                  user_id=get_tool_user_id(),
                                  tenant_id=get_tool_tenant_id(),
                                  # 聊天主图的 Reporter 会产出最终权威答案，
                                  # Memory 统一由父 Runner 在完成后写入。
                                  persist_memory=False)
    result = append_rag_answer_meta(outcome.answer, outcome.answer_meta)
    logger.info(f"[Tool:search_knowledge] 返回 len={len(result or '')} repr_head={repr((result or '')[:40])}")
    return result


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(search_knowledge_tool, __file__)

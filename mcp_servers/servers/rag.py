"""RAG MCP Server — 知识库检索能力"""
from backend.shared.logger import logger
from mcp_servers.manager import MCPServer, _safe_error_location
from mcp_servers.schema_adapter import langchain_tool_to_mcp_meta
from backend.tools.rag import search_knowledge_tool


def _tool_principal():
    """ContextVar 身份 → Principal（与 HTTP/Graph/Tool 通道同一解析语义）。"""
    from backend.core.request_context import (
        get_tool_department,
        get_tool_permissions,
        get_tool_roles,
        get_tool_tenant_id,
        get_tool_user_id,
    )
    from backend.security.principal import resolve_tool_principal

    return resolve_tool_principal(
        user_id=get_tool_user_id(),
        department=get_tool_department(),
        permissions=get_tool_permissions(),
        tenant_id=get_tool_tenant_id(),
        # admin 裁决（rag.admin 权限点）依赖网关验签 roles，必须随 principal 透传
        roles=get_tool_roles(),
    )


def _tool_authorization():
    """Principal → RagAuthorization（构建即裁决，异常 fail-closed）。

    与 GET /api/rag/documents|stats 同一裁决源（RagAuthorization.build），
    MCP 通道不建立第二套授权语义。
    """
    from backend.rag.authz import RagAuthorization

    return RagAuthorization.build(_tool_principal())


# MCP 边界允许输出的 registry 行字段白名单。
# file_path 等服务器内部路径不出边界（对齐 HTTP 通道 2026-10-01 收口口径）。
_DOC_ROW_PUBLIC_FIELDS = (
    "doc_id",
    "file_name",
    "kb_id",
    "department",
    "doc_type",
    "status",
    "chunk_count",
    "file_size",
    "created_at",
    "updated_at",
)


def _safe_doc_row(row: dict) -> dict:
    """registry 行 → MCP 边界安全字段。"""
    return {k: row.get(k) for k in _DOC_ROW_PUBLIC_FIELDS}


def _deny(reason: str) -> "MCPToolError":
    """失败语义（返修3+复核修正）：未认证/越权不返回错误 dict（那会被
    manager 包装成 ok:true / 协议层 isError:false）——统一 raise 受控
    异常，REST 通道由 manager.route 转 ok:false，标准 MCP 通道转
    isError:true。reason 仅接受代码内固定文案；底层异常文本（含
    RagAuthorizationError 可能包裹的原始异常）一律不进 safe_message。
    """
    from mcp_servers.manager import MCPToolError

    return MCPToolError(f"RAG MCP {reason}", "permission_denied")


class RAGMCPServer(MCPServer):
    """知识库检索：search/list_documents/get_stats。

    search_knowledge 的参数定义从 search_knowledge_tool.args_schema 派生
    （ADR-0001 单一事实来源延伸：tool 层改参数 → MCP tools/list 自动同步）；
    list_documents/get_stats 无 tool 等价物，保留手写。
    """
    name = "rag"
    description = "跨境电商知识库检索（RAG）"

    def list_tools(self) -> list:
        search_meta = langchain_tool_to_mcp_meta(
            search_knowledge_tool,
            name="search_knowledge",
            description="从知识库检索 + LLM 生成回答",
        )
        return [
            search_meta,
            {
                "name": "list_documents",
                "description": "列出知识库中的文档",
                "parameters": {
                    "keyword": {"type": "string", "required": False, "default": "", "description": "搜索关键词"},
                    "page": {"type": "integer", "required": False, "default": 1, "description": "页码"},
                },
            },
            {
                "name": "get_stats",
                "description": "知识库统计信息",
                "parameters": {},
            },
        ]

    def call_tool(self, tool_name: str, params: dict):
        import os

        from backend.config import DOC_REGISTRY_PATH, EMBEDDING_MODEL_PATH
        from backend.rag.authz import RagAuthorizationError
        from backend.rag.indexing.doc_registry import DocumentRegistry

        if tool_name == "search_knowledge":
            # P1-3 收口（返修4）：经 ToolGovernance Runtime 执行——MCP
            # 通道与 HTTP/Graph 通道同一治理链（意图门/预算/风险/审计）。
            # 不能假设 .invoke() 自动纳管：治理必须显式经 governance_runtime。
            # capability 对应 capabilities.yaml 已登记的 search_knowledge_tool
            # （read/R0/none，无需新登记）。
            from backend.core.tool_governance.guard import (
                ToolCallRequest,
                governance_runtime,
            )
            from backend.core.tool_runtime.models import ToolStatus

            arguments = {
                "question": params.get("question", ""),
                "kb_id": params.get("kb_id", "default"),
            }
            request = ToolCallRequest(
                capability="search_knowledge_tool",
                arguments=arguments,
                domain="rag",
                intent_fit="match",
                selection_reason="mcp:rag server direct call",
            )
            result = governance_runtime.execute_sync(
                request,
                lambda: search_knowledge_tool.invoke(arguments),
                normalize_output=False,
                domain="rag",
                tool_name="search_knowledge_tool",
                trace_agent="mcp-rag",
            )
            if result.status is not ToolStatus.SUCCESS:
                # 业务失败 ≠ 协议成功（返修3+复核修正）：显式失败上抛受控
                # 异常。result.error_message 来自治理链/执行器底层，可能
                # 含敏感数据——不进 safe_message、不落日志，只记枚举状态
                # 与错误码（安全诊断）。
                from mcp_servers.manager import MCPToolError

                logger.error(
                    f"[MCP-rag] search_knowledge 治理失败 "
                    f"status={result.status.value} "
                    f"error_code={result.error_code or '-'}")
                raise MCPToolError(
                    "search_knowledge 执行失败，请稍后重试", "tool_failed")
            return {"answer": result.data}

        if tool_name == "list_documents":
            # 资源授权：授权范围下推（kb_scope/visible_scopes 进 SQL），
            # 未认证主体在 RagAuthorization.build 即被拒绝（fail-closed）。
            try:
                authz = _tool_authorization()
            except RagAuthorizationError as e:
                # 复核终轮修正：RagAuthorizationError 的 str(e) 可能携带
                # 底层原始异常文本（authz.build 包 "{exc}"）——不进
                # safe_message、不落日志，只记类型+位置；固定安全文案 +
                # permission_denied，fail-closed 语义不变。
                logger.error(
                    f"[MCP-rag] list_documents 授权失败 "
                    f"{type(e).__name__} at {_safe_error_location(e)}")
                raise _deny("未认证或授权失败，已拒绝列出文档") from None
            registry = DocumentRegistry(DOC_REGISTRY_PATH)
            keyword = params.get("keyword", "")
            page = params.get("page", 1)
            page_size = 20
            result = registry.search(
                keyword=keyword, status_filter="active",
                page=page, page_size=page_size,
                kb_scope=sorted(authz.kb_scope),
                visible_scopes=authz.sql_visible_scopes(
                    registry.distinct_permission_scopes()),
            )
            docs = [_safe_doc_row(d) for d in result["items"]]
            return {"documents": docs, "total": result["total"],
                    "page": page, "page_size": page_size}

        if tool_name == "get_stats":
            # P0-1 收口：限管理员 + 脱敏。此前匿名可调且返回 CHROMA_PATH/
            # DOC_DB_PATH/EMBEDDING_MODEL_PATH 服务器绝对路径；现对齐
            # GET /api/rag/stats 口径只回计数与逻辑名，路径不再出边界。
            try:
                authz = _tool_authorization()
            except RagAuthorizationError as e:
                logger.error(
                    f"[MCP-rag] get_stats 授权失败 "
                    f"{type(e).__name__} at {_safe_error_location(e)}")
                raise _deny("未认证或授权失败，已拒绝统计") from None
            if not authz.is_admin:
                raise _deny("get_stats 仅限管理员（rag.admin 权限点）")
            registry = DocumentRegistry(DOC_REGISTRY_PATH)
            docs = [d for d in registry.list_active()
                    if str(d.get("kb_id") or "") in authz.kb_scope]
            return {
                "kb_count": len(set(str(d.get("kb_id") or "default") for d in docs)),
                "doc_count": len(docs),
                "chunk_count": sum(d.get("chunk_count", 0) for d in docs),
                "embedding_model": os.path.basename(EMBEDDING_MODEL_PATH),
                "vector_db": "pgvector",
            }

        raise ValueError(f"未知 tool: {tool_name}")

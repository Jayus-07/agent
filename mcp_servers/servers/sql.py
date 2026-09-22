"""SQL MCP Server — 自然语言查询数据库（STOP C 收口版）

身份链与 HTTP/Graph/Tool 通道完全一致：
    /api/mcp/call 绑定可信身份 contextvars → build_sql_policy_context
        → SQLAgent 策略链（SQLPolicyGuard 权限门/表域/scope 注入/审计）
无可信身份（contextvars 空）时 roles 为空 → 权限门 fail-closed 拒绝，
不存在「HTTP 有权限、MCP 匿名可查」的旁路。
不创建 MCP 专属授权体系（规格 §五）。
"""
from mcp_servers.manager import MCPServer
from mcp_servers.schema_adapter import langchain_tool_to_mcp_meta
from backend.sql.sql_agent import get_sql_agent
from backend.tools.sql import sql_query_tool


def _mcp_sql_policy_context():
    """从 contextvars 装配策略上下文（source_channel="mcp" 审计归因）。"""
    from backend.core.request_context import (
        get_tool_department,
        get_tool_roles,
        get_tool_tenant_id,
        get_tool_user_id,
    )
    from backend.sql.policy import build_sql_policy_context

    return build_sql_policy_context(
        user_id=get_tool_user_id() or "",
        department=get_tool_department() or "",
        tenant_id=get_tool_tenant_id() or "",
        roles=get_tool_roles(),
        source_channel="mcp",
    )


def _unavailable():
    return {"error": "SQL 查询服务暂不可用，请稍后重试。",
            "reason": "service_unavailable"}


class SQLMCPServer(MCPServer):
    """自然语言 SQL 查询 + 白名单表结构。

    sql_query 参数定义从 sql_query_tool.args_schema 派生（单一事实来源）。
    list_tables（STOP C 修复）：直接返回 schema_loader 白名单——
      ① 修复旧实现连错库（DB_CONFIG=agent_memory 元数据库）；
      ② 修复 information_schema 探测面（内部表名泄露）；
      ③ 与 SQL Agent 执行白名单天然一致（同一数据源 schema_config），
         不再需要独立 DB introspection（规格 §七）。
    """
    name = "sql"
    description = "自然语言查询 PostgreSQL 跨境电商数据库"

    def list_tools(self) -> list:
        query_meta = langchain_tool_to_mcp_meta(
            sql_query_tool,
            name="sql_query",
            description="自然语言转 SQL 并执行",
        )
        return [
            query_meta,
            {
                "name": "list_tables",
                "description": "列出 SQL Agent 白名单内的业务数据表",
                "parameters": {},
            },
        ]

    def call_tool(self, tool_name: str, params: dict):
        from backend.config import SQL_AGENT_ENABLED

        if not SQL_AGENT_ENABLED:
            return _unavailable()

        if tool_name == "sql_query":
            # 权限门/表域/scope 注入/审计全部在 agent 策略链内强制；
            # 无可信身份 → 权限门拒绝（fail-closed）
            agent = get_sql_agent()
            result = agent.ask_struct(params["question"],
                                      policy=_mcp_sql_policy_context())
            if result.error_type == "service_unavailable":
                return _unavailable()
            if result.status in ("success", "no_data"):
                return {"result": result.to_markdown()}
            return {"error": result.error or "查询失败",
                    "reason": result.status}

        if tool_name == "list_tables":
            from backend.sql.schema_loader import schema_loader

            tables = schema_loader.get_all_table_names()
            return {"tables": tables, "count": len(tables)}

        raise ValueError(f"未知 tool: {tool_name}")

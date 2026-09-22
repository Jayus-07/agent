"""SQL 工具 — 自然语言查数据库 + 受控原始 SQL 执行（STOP C 收口版）。

两个 Tool 都走统一 choke point：
  sql_query_tool    → SQLAgent 策略链（权限门/表域/scope 注入/审计）
  execute_sql_tool  → SQLPolicyGuard.validate_and_rewrite（同一权限门与
                      scope 注入）→ readonly executor
身份来自服务端可信上下文（contextvars：网关验签头/图状态 bind），客户端
参数永远不参与构造 SQLPolicyContext。
"""
from langchain_core.tools import tool
from backend.shared.logger import logger
from backend.shared.tool_envelope import tool_success_result, tool_error_result

# =====================================================
# 懒加载单例（首次调用时初始化，避免启动时全部加载）
# =====================================================

_sql_agent = None


def _get_sql_agent():
    global _sql_agent
    if _sql_agent is None:
        from backend.config import BUSINESS_DB_CONFIG
        from backend.sql.sql_agent import init_sql_agent
        _sql_agent = init_sql_agent(dict(BUSINESS_DB_CONFIG), max_retries=2)
    return _sql_agent


def tool_policy_context():
    """Tool 通道 SQLPolicyContext 装配（contextvars → 授权层单一来源）。

    无可信身份（脚本/MCP 直调未绑定）时 roles 为空 → 权限门 fail-closed
    拒绝；不虚构身份。source_channel="tool" 供审计归因。
    公共入口：export_csv_tool 等 SQL 数据工具复用同一装配（单一事实源）。
    """
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
        source_channel="tool",
    )


def _sql_disabled_result():
    """kill switch 关闭时的统一 unavailable 封套（非权限语义）。"""
    return tool_error_result(
        "SQL 查询服务暂不可用，请稍后重试。", reason="service_unavailable")


# =====================================================
# Tool 定义
# =====================================================

@tool
def execute_sql_tool(query: str) -> str:
    """
    在安全策略约束下执行一条 PostgreSQL 只读查询（SELECT）。
    输入 SQL SELECT 语句；语句必须通过 SQLPolicyGuard（只读校验、表白名单、
    数据范围注入）后才会执行，返回 JSON 格式查询结果。
    适用场景：Workflow step 中的确定性数据拉取（不经过 NL→SQL Agent）。
    """
    import time
    from backend.config import SQL_AGENT_ENABLED

    if not SQL_AGENT_ENABLED:
        return _sql_disabled_result()

    logger.info(f"[Tool:execute_sql] {query[:80]}...")

    try:
        from backend.sql.policy import SQLPolicyGuard, SQLPolicyError
        from backend.sql.executor import execute_sql_struct

        # STOP C：原始 SQL 同样过统一 choke point（权限门 + 表域 + scope
        # 注入），不再直调 validator+executor 旁路（规格 §六）
        policy_ctx = tool_policy_context()
        guarded = SQLPolicyGuard().validate_and_rewrite(query, policy_ctx)
        result = execute_sql_struct(
            guarded.executable_sql, params=guarded.params)

        from backend.sql.sql_agent import sql_audit_decision
        from backend.sql.sql_agent import _observe

        _observe(
            decision=sql_audit_decision(result.status) if result.status in (
                "success", "no_data", "timeout", "syntax_error",
                "permission_denied", "failed") else "EXECUTION_FAILED",
            policy=policy_ctx, sql=query,
            tables=guarded.referenced_tables,
            duration_ms=int((result.elapsed_sec or 0) * 1000),
            row_count=result.row_count, status=result.status,
            error_type=result.error_type or "",
        )

        if result.status in ("success", "no_data"):
            logger.info(f"[Tool:execute_sql] 返回 {result.row_count} 行")
            # 统一封套（shared/tool_envelope.py）：数据嵌套在 data 下
            return tool_success_result(
                {"rows": result.rows, "columns": result.columns, "total": result.row_count},
            )
        logger.error(f"[Tool:execute_sql] 失败: {result.status} - {result.error}")
        return tool_error_result(result.error, reason=result.status)
    except SQLPolicyError as e:
        # 安全拒绝同样必须留审计痕迹（deny 归因），再转错误封套
        from backend.sql.sql_agent import _DENY_DECISION, _observe

        _observe(
            decision=_DENY_DECISION.get(e.code, "DENY_SCOPE"),
            policy=tool_policy_context(), sql=query,
            deny_code=e.code,
        )
        logger.error(f"[Tool:execute_sql] 策略拒绝 code={e.code}")
        return tool_error_result(e.user_text, reason=e.code)
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
    from backend.config import SQL_AGENT_ENABLED

    if not SQL_AGENT_ENABLED:
        return _sql_disabled_result()

    logger.info(f"[Tool:sql_query] 问题：{question[:80]}...")
    agent = _get_sql_agent()
    # STOP C：策略链（权限门/表域/scope 注入/审计），身份来自 contextvars
    # （graph bind / MCP /api/mcp/call 入口绑定）；无可信身份时权限门拒绝
    result = agent.ask_struct(question, policy=tool_policy_context())
    if result.status in ("success", "no_data"):
        return result.to_markdown()
    if result.error_type == "service_unavailable":
        return _sql_disabled_result()
    return tool_error_result(result.error or "查询失败", reason=result.status)


# ==================== Tool Registry 自动注册 ====================
# 本模块导出两个 Tool，启动时自动注册到全局 Registry
from backend.tools.tool_registry import tool_registry

# 显式注册当前模块的所有 Tool
tool_registry.register(execute_sql_tool, __file__)
tool_registry.register(sql_query_tool, __file__)

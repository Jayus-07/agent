"""SQL 路由 — 自然语言 → SQL 安全查询（6层硬校验 + SQLPolicyGuard）

STOP C 接线（2026-09-23）：身份链完全复用统一授权体系——

    APISIX JWT → 可信身份头 → deps.get_principal → AuthorizationContext
        → SQLPolicyContext(source_channel="http") → SQLAgent 策略链
        → SQLPolicyGuard（权限门/表域/scope 注入）→ readonly executor

路由层只做两件事，不自行判断角色/作用域：
  1. kill switch（SQL_AGENT_ENABLED=false → 503 服务不可用语义）
  2. sql.read 权限预检（authz.has_permission → 403，快速失败不进 LLM；
     agent 内 Guard 仍强制检查，纵深防御）

管理端表浏览（2026-10-01，GET /sql/tables*）：NL2SQL 之外的确定性只读
通道——SQL 由服务端按 schema_loader 白名单元数据拼装（无 LLM、无用户
SQL 文本），走同一 Guard/executor/脱敏栈，供核对 NL2SQL 回答是否正确。
"""
import asyncio
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.api.deps import get_sql_agent, get_principal
from backend.app.api.schemas import (
    BrowseColumn,
    BrowseTable,
    SQLAskRequest,
    SQLQueryResponse,
    ErrorResponse,
    TableBrowseResponse,
    TableCatalogResponse,
)
from backend.shared.logger import logger
from backend.security.principal import Principal
from backend.sql.policy import SQLPolicyContext, SQLPolicyError
from backend.sql.schema_loader import schema_loader

router = APIRouter(prefix="/sql", tags=["SQL查询"])


def _resolve_user_id(request: Request) -> Optional[int]:
    """[legacy] P1-11 时代的头身份推导，STOP C 起路由改走 SQLPolicyContext。

    保留供 tests/test_sql_user_context.py 的身份收敛语义回归；
    生产路径不再调用——身份经 deps.get_principal → AuthorizationContext。
    """
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)  # 不传 body_user_id：SQL 侧从未采信请求体
    if not ident.authenticated:
        return None

    raw = ident.user_id
    try:
        return int(raw)
    except ValueError:
        return None


def _build_policy(principal: Principal,
                  source_channel: str = "http") -> SQLPolicyContext:
    """HTTP 通道 SQLPolicyContext 装配（复用 AuthorizationContext，不重推导）。

    Principal → AuthorizationContext 的构建在 deps.get_principal 链上
    已完成；source_channel 仅作审计/trace 归因（http=NL2SQL 通道，
    admin=管理端表浏览），低基数，不参与授权判定。
    """
    from backend.security.authorization import build_authorization_context

    authz = build_authorization_context(principal)
    return SQLPolicyContext(
        principal=principal, authz=authz, source_channel=source_channel)


def _require_sql_enabled() -> None:
    """kill switch（规格 §十四）：关闭时统一 503 服务不可用语义。"""
    from backend.config import SQL_AGENT_ENABLED

    if not SQL_AGENT_ENABLED:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="SQL 查询服务暂不可用，请稍后重试。")


def _require_sql_read(policy: SQLPolicyContext) -> None:
    """sql.read 权限预检（快速失败，不进 LLM 生成链）。

    消费 AuthorizationContext.has_permission，不读角色字符串；
    agent 内 SQLPolicyGuard 会再次强制检查（纵深，规格 §三）。
    """
    from fastapi import HTTPException

    if not policy.authz.has_permission("sql.read"):
        raise HTTPException(status_code=403, detail="当前查询超出你的数据访问范围。")


@router.post("", responses={500: {"model": ErrorResponse}})
async def sql_ask(
    req: SQLAskRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
):
    """自然语言转 SQL 查询：敏感列拦截 + 数据范围注入 + 脱敏 + 审计。

    用户身份/权限/数据范围全部来自服务端可信链（网关验签头 → Principal
    → AuthorizationContext），请求体中的 current_user_id 已废弃（可伪造，
    一律忽略）。
    """
    _require_sql_enabled()
    policy = _build_policy(principal)
    _require_sql_read(policy)

    if req.current_user_id is not None:
        logger.warning(
            f"[SQL] 客户端在请求体中传递了 current_user_id={req.current_user_id}，"
            "该字段已废弃（可伪造），已忽略；用户身份由服务端可信链推导")

    agent = get_sql_agent()
    answer = await asyncio.to_thread(agent.ask_struct, req.question, policy=policy)
    return {"answer": answer.to_markdown()}


@router.post("/query", response_model=SQLQueryResponse,
             responses={500: {"model": ErrorResponse}})
async def sql_query(
    req: SQLAskRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> SQLQueryResponse:
    """结构化查询端点：返回 status / 行列数据 / 耗时 / 错误分类。

    安全语义与 POST /sql 一致（权限预检 + SQLPolicyGuard + 审计）。
    """
    _require_sql_enabled()
    policy = _build_policy(principal)
    _require_sql_read(policy)

    agent = get_sql_agent()
    result = await asyncio.to_thread(agent.ask_struct, req.question, policy=policy)
    return SQLQueryResponse(
        status=result.status,
        answer=result.to_markdown(),
        columns=result.columns or [],
        rows=result.rows or [],
        row_count=result.row_count,
        elapsed_sec=result.elapsed_sec,
        error=result.error,
        error_type=result.error_type,
        sql=result.sql_text,
    )


# =================================================
# 管理端表浏览（确定性只读通道，2026-10-01）
# =================================================

# 标识符合法性（防御纵深：表/列名来自 schema_config 代码配置，
# 正常永远合法；一旦配置被污染在拼 SQL 前快速失败，不进数据库）
_BROWSE_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# 与 schema_loader.max_limit（validator Layer 5 上限）对齐
_BROWSE_MAX_PAGE_SIZE = 100

# SQLPolicyError.code → 审计决策（与 audit.py 决策枚举同口径）
_DENY_DECISION = {
    "SQL_PERMISSION_DENIED": "DENY_PERMISSION",
    "SQL_TABLE_NOT_ALLOWED": "DENY_TABLE",
    "SQL_SCOPE_UNAVAILABLE": "DENY_SCOPE",
}


def _validated_browse_columns(qualified_name: str) -> list[str]:
    """可见列清单（敏感列剔除）+ 标识符合法性 fail-fast。"""
    columns = list(schema_loader.get_browse_columns(qualified_name))
    if not columns:
        raise HTTPException(status_code=404, detail="该表没有可浏览的列。")
    for ident in (*qualified_name.split("."), *columns):
        if not _BROWSE_IDENT_RE.match(ident):
            # 元数据来自代码配置；走到这里说明配置被污染，快速失败不进 DB
            raise HTTPException(status_code=500, detail="表元数据异常，已阻止查询。")
    return columns


def _browse_sync(policy: SQLPolicyContext, qualified_name: str,
                 columns: list[str], order_col: str, order_dir: str,
                 page: int, page_size: int) -> dict:
    """同步执行：Guard（表域/scope 注入）→ 只读 executor（含脱敏）→ 审计。

    count 与 rows 两条 SQL 都过 SQLPolicyGuard——与 NL2SQL 通道同一
    表域判定与 scope 注入语义（internal 表仅 all 可读、personal 表
    department/self scope 不可表达即拒绝），不因服务端拼装而放宽。
    """
    from backend.sql.audit import record_sql_audit
    from backend.sql.executor import execute_sql_struct
    from backend.sql.policy import SQLPolicyGuard
    from backend.sql.sql_validator import ValidationError

    offset = (page - 1) * page_size
    count_sql = f"SELECT COUNT(*) AS row_total FROM {qualified_name}"
    rows_sql = (
        f"SELECT {', '.join(columns)} FROM {qualified_name} "
        f"ORDER BY {order_col} {order_dir} LIMIT {page_size} OFFSET {offset}"
    )

    guard = SQLPolicyGuard()
    t0 = time.monotonic()
    try:
        count_guarded = guard.validate_and_rewrite(count_sql, policy)
        rows_guarded = guard.validate_and_rewrite(rows_sql, policy)
    except SQLPolicyError as e:
        record_sql_audit(
            decision=_DENY_DECISION.get(e.code, "DENY_SCOPE"),
            user_id=policy.user_id, tenant_id=policy.tenant_id,
            department=policy.department, data_scope=policy.data_scope or "",
            source_channel=policy.source_channel, tool_name="sql.browse",
            sql=rows_sql, tables=(qualified_name,), deny_code=e.code,
        )
        raise HTTPException(status_code=403, detail=e.user_text) from e
    except ValidationError as e:
        # 服务端拼装的 SQL 进不了校验 = 配置/实现缺陷，快速失败
        logger.error(f"[SQL:browse] 拼装 SQL 未通过校验: {e}")
        raise HTTPException(status_code=500, detail="浏览查询未通过安全校验。") from e

    count_result = execute_sql_struct(
        count_guarded.executable_sql, params=count_guarded.params)
    if count_result.status not in ("success", "no_data"):
        record_sql_audit(
            decision="EXECUTION_FAILED",
            user_id=policy.user_id, tenant_id=policy.tenant_id,
            department=policy.department, data_scope=policy.data_scope or "",
            source_channel=policy.source_channel, tool_name="sql.browse",
            sql=rows_guarded.executable_sql, tables=(qualified_name,),
            status=count_result.status, error_type=count_result.error_type or "",
        )
        return {"status": count_result.status, "total": 0, "columns": columns,
                "rows": [], "elapsed_sec": time.monotonic() - t0,
                "error": count_result.error, "error_type": count_result.error_type}

    total = 0
    if count_result.rows:
        raw_total = count_result.rows[0].get("row_total", 0)
        total = int(raw_total) if raw_total is not None else 0

    result = execute_sql_struct(
        rows_guarded.executable_sql, params=rows_guarded.params)
    elapsed = time.monotonic() - t0

    from backend.sql.audit import decision_from_result
    record_sql_audit(
        decision=decision_from_result(result.status),
        user_id=policy.user_id, tenant_id=policy.tenant_id,
        department=policy.department, data_scope=policy.data_scope or "",
        source_channel=policy.source_channel, tool_name="sql.browse",
        sql=rows_guarded.executable_sql, tables=(qualified_name,),
        duration_ms=int(elapsed * 1000), row_count=result.row_count,
        status=result.status, error_type=result.error_type or "",
    )
    return {
        "status": result.status, "total": total, "columns": columns,
        "rows": result.rows or [], "elapsed_sec": elapsed,
        "error": result.error, "error_type": result.error_type,
    }


@router.get("/tables", response_model=TableCatalogResponse)
async def list_tables(
    principal: Principal = Depends(get_principal),
) -> TableCatalogResponse:
    """表目录：白名单内全部业务表 + 可见列元数据（敏感列不出口）。

    纯元数据（schema_loader 内存数据，零 DB 查询）；权限语义与
    NL2SQL 通道一致（kill switch + sql.read 预检）。
    """
    _require_sql_enabled()
    policy = _build_policy(principal, source_channel="admin")
    _require_sql_read(policy)

    tables = []
    for qualified in schema_loader.get_all_table_names():
        schema_name, table_name = schema_loader.split_qualified(qualified)
        tables.append(BrowseTable(
            schema_name=schema_name,
            name=table_name,
            qualified_name=qualified,
            description=schema_loader.get_table_description(qualified),
            columns=[
                BrowseColumn(name=col, comment=comment)
                for col, comment in schema_loader.get_browse_columns(qualified).items()
            ],
        ))
    return TableCatalogResponse(tables=tables)


@router.get("/tables/{schema_name}/{table_name}", response_model=TableBrowseResponse)
async def browse_table(
    schema_name: str,
    table_name: str,
    page: int = Query(1, ge=1, le=100_000, description="页码（1 起）"),
    page_size: int = Query(20, ge=1, le=_BROWSE_MAX_PAGE_SIZE,
                           description="每页行数（上限与 validator max_limit 对齐）"),
    sort: Optional[str] = Query(None, description="排序列（缺省 id，无 id 取首列）"),
    order: str = Query("asc", pattern="^(asc|desc)$", description="排序方向"),
    principal: Principal = Depends(get_principal),
) -> TableBrowseResponse:
    """传统表分页浏览：确定性只读 SELECT，供核对 NL2SQL 回答是否正确。

    安全面与 NL2SQL 完全同构：kill switch → sql.read 预检 →
    SQLPolicyGuard（表域/scope 注入）→ 只读连接池 + 列级脱敏 + 审计
    （source_channel=admin）。差异仅在 SQL 来源——服务端按白名单
    元数据拼装，不存在用户 SQL 注入面。
    """
    _require_sql_enabled()
    policy = _build_policy(principal, source_channel="admin")
    _require_sql_read(policy)

    qualified = f"{schema_name}.{table_name}".lower()
    if qualified not in schema_loader.allowed_tables:
        raise HTTPException(status_code=404, detail="数据表不存在或不在可浏览范围内。")
    columns = _validated_browse_columns(qualified)

    order_col = sort if sort else ("id" if "id" in columns else columns[0])
    if order_col not in columns:
        raise HTTPException(status_code=400, detail="排序列不在该表可浏览列中。")

    result = await asyncio.to_thread(
        _browse_sync, policy, qualified, columns, order_col, order, page, page_size)
    return TableBrowseResponse(qualified_name=qualified, page=page,
                               page_size=page_size, **result)

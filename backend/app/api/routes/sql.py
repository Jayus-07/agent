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
import inspect
import json
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

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
from backend.sql.query_context import (
    clear_sql_query_context,
    load_sql_query_context,
    persist_sql_query_result,
)

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


def _ask_sql_agent(
    agent,
    question: str,
    *,
    policy: SQLPolicyContext,
    query_context: dict | None = None,
    event_sink=None,
):
    """调用 SQLAgent，并兼容外部旧版测试替身/集成适配器的旧签名。"""
    kwargs = {"policy": policy}
    try:
        parameters = inspect.signature(agent.ask_struct).parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
    except (TypeError, ValueError):
        accepts_kwargs = True
        parameters = {}
    if query_context is not None and (
        accepts_kwargs or "query_context" in parameters
    ):
        kwargs["query_context"] = query_context
    if event_sink is not None and (
        accepts_kwargs or "event_sink" in parameters
    ):
        kwargs["event_sink"] = event_sink
    return agent.ask_struct(question, **kwargs)


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
    query_context = None
    if req.reset_context:
        clear_sql_query_context(policy.tenant_id, policy.user_id, req.session_id)
    else:
        query_context = load_sql_query_context(
            policy.tenant_id, policy.user_id, req.session_id)
    if query_context is None:
        # 首轮保持旧调用签名，兼容外部 Tool/测试替身；有上下文时才
        # 显式传入追问摘要。
        result = await asyncio.to_thread(
            _ask_sql_agent, agent, req.question, policy=policy)
    else:
        result = await asyncio.to_thread(
            _ask_sql_agent, agent, req.question, policy=policy,
            query_context=query_context)
    memory = persist_sql_query_result(
        tenant_id=policy.tenant_id,
        user_id=policy.user_id,
        session_id=req.session_id,
        raw_question=req.question,
        query_context=query_context,
        policy=policy,
        result=result,
    )
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
        column_comments=_comments_for_result_columns(result.columns or []),
        memory=memory,
    )


def _sql_sse_encode(event: dict) -> str:
    """编码 SQL 查询流事件，复用聊天流已有的 meta/status/log/done/error 契约。"""
    event_name = event.get("event", "log")
    data = event.get("data", {})
    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.post("/query/stream")
async def sql_query_stream(
    req: SQLAskRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
):
    """流式执行 SQL 查询，持续返回需求理解、Tool 阶段和最终结果。

    事件类型沿用聊天 SSE：不新增前端协议，只在既有 status/log 的 payload
    中补充 phase、tool 和用户可读 message。SQL 执行在线程池中运行，避免
    同步的模型/数据库调用阻塞事件循环。
    """
    _require_sql_enabled()
    policy = _build_policy(principal)
    _require_sql_read(policy)

    if req.current_user_id is not None:
        logger.warning(
            "[SQL] 流式请求携带已废弃的 current_user_id=%s，已忽略",
            req.current_user_id,
        )

    if req.reset_context:
        clear_sql_query_context(policy.tenant_id, policy.user_id, req.session_id)
        query_context = None
    else:
        query_context = load_sql_query_context(
            policy.tenant_id, policy.user_id, req.session_id)

    agent = get_sql_agent()
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue(maxsize=128)
    request_id = uuid.uuid4().hex
    started_at = time.monotonic()

    def enqueue(event: dict | None) -> None:
        """从 SQL 工作线程安全地投递事件到当前事件循环。"""
        try:
            events.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("[SQL] 流式事件队列已满，丢弃中间进度事件")

    def emit_from_worker(event: dict) -> None:
        try:
            loop.call_soon_threadsafe(enqueue, event)
        except RuntimeError:
            logger.debug("[SQL] 流式事件循环已关闭", exc_info=True)

    def worker() -> None:
        try:
            result = _ask_sql_agent(
                agent, req.question, policy=policy,
                query_context=query_context, event_sink=emit_from_worker,
            )
            memory = persist_sql_query_result(
                tenant_id=policy.tenant_id,
                user_id=policy.user_id,
                session_id=req.session_id,
                raw_question=req.question,
                query_context=query_context,
                policy=policy,
                result=result,
            )
            response = SQLQueryResponse(
                status=result.status,
                answer=result.to_markdown(),
                columns=result.columns or [],
                rows=result.rows or [],
                row_count=result.row_count,
                elapsed_sec=result.elapsed_sec,
                error=result.error,
                error_type=result.error_type,
                sql=result.sql_text,
                column_comments=_comments_for_result_columns(result.columns or []),
                memory=memory,
            )
            emit_from_worker({
                "event": "done",
                "data": {
                    "elapsed": time.monotonic() - started_at,
                    "sources": [],
                    "result": response.model_dump(),
                },
            })
        except Exception:  # noqa: BLE001 — 流式终帧需统一返回错误
            logger.exception("[SQL] 流式查询失败")
            emit_from_worker({
                "event": "error",
                "data": {
                    "message": "查询执行失败，请稍后重试。",
                    "ts": time.time(),
                },
            })
        finally:
            emit_from_worker(None)

    async def event_stream():
        yield _sql_sse_encode({
            "event": "meta",
            "data": {
                "request_id": request_id,
                "stream_id": request_id,
                "node_labels": {
                    "query_understanding": "需求理解",
                    "table_router": "数据表选择",
                    "sql_generator": "SQL生成",
                    "sql_validator": "安全校验",
                    "sql_executor": "数据查询工具",
                },
            },
        })
        # 让页面在模型/数据库调用开始前就能反馈已接收请求。
        yield _sql_sse_encode({
            "event": "status",
            "data": {
                "node": "query_understanding",
                "step_id": "query_understanding",
                "status": "running",
                "message": "正在理解查询需求…",
                "ts": time.time(),
            },
        })
        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                event = await events.get()
                if event is None:
                    break
                yield _sql_sse_encode(event)
                await asyncio.sleep(0)
        finally:
            if not task.done():
                await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-Id": request_id,
        },
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

# 结果列 → 中文注释的全局映射（惰性构建一次；不同表同名列取首见注释——
# id/created_at 等公共列注释语义一致，重名业务列冲突可忽略）
_RESULT_COMMENT_MAP: dict[str, str] | None = None


def _comments_for_result_columns(columns: list) -> dict[str, str]:
    """NL2SQL 结果列的中文注释尽力匹配。

    LLM 生成 SQL 的输出列可能是聚合/别名（无注释可配），只对能匹配
    白名单列名的补注释，匹配不到的列前端回退显示物理列名。
    """
    global _RESULT_COMMENT_MAP
    if not columns:
        return {}
    if _RESULT_COMMENT_MAP is None:
        mapping: dict[str, str] = {}
        for qname in schema_loader.get_all_table_names():
            for col, comment in schema_loader.get_browse_columns(qname).items():
                mapping.setdefault(col, comment)
        _RESULT_COMMENT_MAP = mapping
    return {c: _RESULT_COMMENT_MAP[c] for c in columns if c in _RESULT_COMMENT_MAP}


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
    # 中文注释与列名同源（schema_config 数据字典），供前端表头主显
    column_comments = schema_loader.get_browse_columns(qualified_name)

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
                "column_comments": column_comments, "rows": [],
                "elapsed_sec": time.monotonic() - t0,
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
        "column_comments": column_comments, "rows": result.rows or [],
        "elapsed_sec": elapsed,
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

    from backend.sql.policy import SQLPolicyGuard

    visible_tables = set(SQLPolicyGuard().get_allowed_tables(policy))
    tables = []
    for qualified in schema_loader.get_all_table_names():
        if qualified not in visible_tables:
            continue
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
    from backend.sql.policy import SQLPolicyGuard
    if qualified not in set(SQLPolicyGuard().get_allowed_tables(policy)):
        raise HTTPException(status_code=403, detail="该数据不在当前可访问范围内。")
    columns = _validated_browse_columns(qualified)

    order_col = sort if sort else ("id" if "id" in columns else columns[0])
    if order_col not in columns:
        raise HTTPException(status_code=400, detail="排序列不在该表可浏览列中。")

    result = await asyncio.to_thread(
        _browse_sync, policy, qualified, columns, order_col, order, page, page_size)
    return TableBrowseResponse(qualified_name=qualified, page=page,
                               page_size=page_size, **result)

"""
sql_agent.py — SQL Agent 主编排器

流程: Router(选表) → Generator(生成SQL) → Validator(硬校验)
      → RowSecurity(行级注入) → Executor(执行+脱敏+格式化)

安全原则: 6 层硬校验，无一依赖 LLM 承诺。

返回契约 (A 段重构)：
  - `ask(question)`     — 旧入口，返回 Markdown 字符串（向后兼容 tools 层）
  - `ask_struct(question)` — 新入口，返回 SQLResult（推荐给 SQLSkill）

策略链（2026-09-23 SQL 收口 STOP B）：
  - `ask_struct(question, policy=SQLPolicyContext)` — 生产链路：权限门 +
    表域判定 + 三维 scope 注入（backend/sql/policy.py），SQLPolicyError
    为终态拒绝；重试仍重走 Guard。
  - policy=None（评测/脚本/未接上下文调用）— 旧行为：仅 6 层校验 +
    旧 row_security 注入（受 SQL_ROW_SECURITY_ENABLED 控制）。
"""
import inspect
from typing import Callable, Optional

from backend.config import SQL_AGENT_ENABLED
from backend.sql.policy import SQLPolicyContext
from backend.sql.router import select_tables
from backend.sql.sql_generator import generate_sql
from backend.sql.sql_validator import sql_validator, ValidationError
from backend.sql.row_security import inject_row_filter, RowSecurityError
from backend.sql.executor import execute_sql_struct
from backend.sql.sql_result import SQLResult
from backend.shared.logger import logger
from backend.sql.query_context import (
    SQLQueryContext,
    is_sql_context_compatible,
    permission_fingerprint,
    resolve_sql_followup,
)
from backend.sql.stream_events import emit_sql_stage

# ── 服务不可用语义（kill switch 关闭时；区别于权限拒绝，避免误导诊断）──
_UNAVAILABLE_ERROR = "SQL 查询服务暂不可用，请稍后重试。"

# 数据库发现的对象/列不存在属于生成错误，允许一次受控反馈修复；
# 真实安全拒绝、SQL 语法错误和权限错误仍然是终态。
_REPAIRABLE_SCHEMA_ERROR_TYPES = frozenset({
    "schema_mismatch", "column_not_found", "relation_not_found",
})


def _is_repairable_schema_result(result: SQLResult) -> bool:
    return (
        result.status == "syntax_error"
        and (result.error_type or "") in _REPAIRABLE_SCHEMA_ERROR_TYPES
    )


def _select_authorized_tables(
    question: str, allowed_tables: list[str],
) -> list[str]:
    """把授权表范围传给 Router，且兼容旧版单参数测试替身。"""
    try:
        parameters = inspect.signature(select_tables).parameters
        supports_scope = (
            "allowed_tables" in parameters
            or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
        )
    except (TypeError, ValueError):
        supports_scope = True
    if supports_scope:
        return select_tables(question, allowed_tables=allowed_tables)
    return select_tables(question)


def _prepare_sql_question(
    question: str,
    *,
    query_context: dict | None,
    policy: Optional[SQLPolicyContext],
) -> str:
    """按当前授权指纹解析 SQL 追问，返回供 Router/Generator 使用的独立问题。"""
    previous = SQLQueryContext.from_dict(query_context)
    if policy is not None:
        fingerprint = permission_fingerprint(policy)
        if not is_sql_context_compatible(previous, fingerprint):
            previous = None
    resolution = resolve_sql_followup(question, previous)
    return str(resolution.get("standalone_question") or question).strip()


def _unavailable_result() -> SQLResult:
    return SQLResult.failed(
        status="failed",
        error=_UNAVAILABLE_ERROR,
        error_type="service_unavailable",
    )


# SQLPolicyError.code → 审计决策映射（低基数；deny 全部为终态）
_DENY_DECISION = {
    "SQL_PERMISSION_DENIED": "DENY_PERMISSION",
    "SQL_TABLE_NOT_ALLOWED": "DENY_TABLE",
    "SQL_SCOPE_UNAVAILABLE": "DENY_SCOPE",
}
_DENY_METRIC_REASON = {
    "SQL_PERMISSION_DENIED": "permission",
    "SQL_TABLE_NOT_ALLOWED": "table",
    "SQL_SCOPE_UNAVAILABLE": "scope",
}


def _observe(
    *,
    decision: str,
    policy: Optional[SQLPolicyContext],
    sql: str = "",
    tables=(),
    deny_code: str = "",
    duration_ms: int = 0,
    row_count: int | None = None,
    status: str = "",
    error_type: str = "",
) -> None:
    """统一观测出口：audit（best-effort 落库）+ Prometheus 指标。

    只在 agent 层 choke point 调用，各入口不复制审计逻辑；
    任何观测失败都不影响主流程（record_sql_audit 内部吞异常）。
    """
    try:
        from backend.observability.metrics import (
            sql_agent_denied_total,
            sql_agent_execution_duration_seconds,
            sql_agent_execution_total,
            sql_agent_requests_total,
            sql_agent_rows_returned_total,
        )
        from backend.sql import audit as sql_audit

        source = (policy.source_channel if policy else "") or "unknown"
        sql_agent_requests_total.labels(
            source=source, decision=decision).inc()
        if decision.startswith("DENY"):
            reason = (_DENY_METRIC_REASON.get(deny_code)
                      or ("validator" if decision == "DENY_VALIDATOR"
                          else "unknown_scope"))
            sql_agent_denied_total.labels(reason=reason).inc()
        if status:
            sql_agent_execution_total.labels(status=status).inc()
            if status == "timeout":
                sql_agent_denied_total.labels(reason="timeout").inc()
        if duration_ms:
            sql_agent_execution_duration_seconds.observe(duration_ms / 1000)
        if row_count:
            sql_agent_rows_returned_total.inc(row_count)

        sql_audit.record_sql_audit(
            decision=decision,
            session_id=_current_session(),
            user_id=(policy.user_id if policy else "") or "",
            tenant_id=(policy.tenant_id if policy else "") or "",
            department=(policy.department if policy else "") or "",
            data_scope=str(policy.data_scope or "") if policy else "",
            source_channel=source,
            sql=sql,
            tables=tables,
            deny_code=deny_code,
            duration_ms=duration_ms,
            row_count=row_count,
            status=status,
            error_type=error_type,
        )
    except Exception as e:  # 观测永不阻塞主查询
        logger.warning(f"[SQLAgent] 观测埋点失败（忽略）: {e}")


def _current_session() -> str:
    try:
        from backend.core.request_context import get_current_session_id

        return get_current_session_id()
    except Exception:
        return ""


def sql_audit_decision(status: str) -> str:
    """executor status → 审计决策（供 policy 链/旧链/收口 tool 共用）。"""
    from backend.sql.audit import decision_from_result

    return decision_from_result(status)


class SQLAgent:
    """生产级 SQL Agent 入口"""

    def __init__(self, db_config: dict, max_retries: int = 1):
        """
        参数:
            db_config: PostgreSQL 连接配置
                       {"host": "localhost", "port": 5432, "dbname": "demo",
                        "user": "readonly", "password": "..."}
            max_retries: 校验失败后重新生成 SQL 的次数
        """
        self.db_config = db_config
        self.max_retries = max_retries

    # =================================================
    # 主入口：旧（Markdown 字符串）
    # =================================================

    def ask(
        self,
        question: str,
        current_user_id: Optional[int] = None,
        policy: Optional[SQLPolicyContext] = None,
    ) -> str:
        """处理自然语言问题，返回 Markdown 表格或错误字符串（向后兼容）。

        推荐新调用方使用 `ask_struct()` 拿 SQLResult。
        policy：STOP C 策略链上下文（生产入口必须携带）。
        """
        result = self.ask_struct(question, current_user_id=current_user_id,
                                 policy=policy)
        return result.to_markdown()

    # =================================================
    # 主入口：新（结构化 SQLResult）
    # =================================================

    def ask_struct(
        self,
        question: str,
        current_user_id: Optional[int] = None,
        policy: Optional["SQLPolicyContext"] = None,
        query_context: dict | None = None,
        event_sink: Callable[[dict], None] | None = None,
    ) -> SQLResult:
        """处理自然语言问题并返回 SQLResult。

        policy 给定 → 策略链（权限门/表域/scope 注入，见模块 docstring）；
        policy=None → 旧行为（未声明主体 = 授权未启用，评测/脚本路径）。

        错误语义约定：
          - router 抛任何异常 → status="failed", error_type="router_error"
          - router 返回 []     → status="no_table"
          - ValidationError    → status="validation_error"
          - SQLPolicyError     → status="permission_denied"（终态，不重试）
          - RowSecurityError   → status="permission_denied"
          - executor 返回的 status 透传（success / no_data / timeout / syntax_error / permission_denied / failed）
        """
        if not SQL_AGENT_ENABLED:
            # kill switch（STOP C §十四）：全入口统一服务不可用语义，
            # 不区分权限——避免误导权限诊断
            return _unavailable_result()
        if policy is not None:
            effective_question = _prepare_sql_question(
                question, query_context=query_context, policy=policy)
            return self._ask_struct_with_policy(
                effective_question, policy, event_sink=event_sink)

        effective_question = _prepare_sql_question(
            question, query_context=query_context, policy=None)

        logger.info(
            f"[SQLAgent] 收到问题: {effective_question[:80]}... "
            f"(user={current_user_id})")

        user_context = {}
        if current_user_id is not None:
            user_context["current_user_id"] = current_user_id

        emit_sql_stage(
            event_sink, node="query_understanding", phase="understanding",
            message="已理解查询需求")

        # — Step 1: 路由选表 —
        try:
            table_names = select_tables(effective_question)
            logger.info(f"[SQLAgent] 选中表: {table_names}")
            emit_sql_stage(
                event_sink, node="table_router", phase="table_routing",
                message=f"已匹配 {len(table_names)} 张数据表",
                tables=table_names[:20], status="success")
        except Exception as e:
            logger.error(f"[SQLAgent] 表路由失败: {e}")
            _observe(decision="EXECUTION_FAILED", policy=None,
                     status="failed", error_type="router_error")
            return SQLResult.failed(
                status="failed",
                error=f"内部错误：表路由失败 {e}",
                error_type="router_error",
            )

        if not table_names:
            _observe(decision="EXECUTION_FAILED", policy=None,
                     status="no_table", error_type="no_table")
            return SQLResult.failed(
                status="no_table",
                error="未找到相关数据表，请调整问题后重试。",
                error_type="no_table",
            )

        # — Step 2-5: 生成 + 校验循环 —
        feedback: str | None = None  # 重试时携带上次 SQL + 失败原因，引导 LLM 修正
        last_sql: str | None = None
        last_result: SQLResult | None = None
        for attempt in range(self.max_retries + 1):
            sql: str | None = None
            try:
                emit_sql_stage(
                    event_sink, node="sql_generator", phase="sql_generation",
                    message="正在生成查询语句")
                sql = generate_sql(effective_question, table_names, feedback=feedback)
                last_sql = sql

                emit_sql_stage(
                    event_sink, node="sql_validator", phase="sql_validation",
                    message="正在校验查询安全性")
                safe_sql, _, _ = sql_validator.validate(sql)

                # 行级安全：返回 (sql_with_placeholders, params_dict)
                safe_sql, rs_params = inject_row_filter(safe_sql, user_context)

                # 结构化执行
                emit_sql_stage(
                    event_sink, node="sql_executor", phase="tool_start",
                    message="正在调用数据查询工具", status="running")
                result = execute_sql_struct(safe_sql, self.db_config, params=rs_params)
                last_result = result
                emit_sql_stage(
                    event_sink, node="sql_executor", phase="tool_result",
                    message=("查询完成" if result.status in ("success", "no_data")
                             else "查询未完成"),
                    status=("success" if result.status in ("success", "no_data")
                            else "failed"),
                    row_count=result.row_count,
                    error_type=result.error_type or "",
                )

                # 成功路径 → 直接返回
                if result.status in ("success", "no_data"):
                    _observe(
                        decision=sql_audit_decision(result.status), policy=None,
                        sql=sql, duration_ms=int((result.elapsed_sec or 0) * 1000),
                        row_count=result.row_count, status=result.status,
                    )
                    return result

                # 表/列不存在是模型生成错误，带真实错误类型反馈重试；
                # 其它 syntax_error 仍终态，避免把安全/语法错误反复送模型。
                if result.status in ("validation_error", "permission_denied") \
                        or (result.status == "syntax_error"
                            and not _is_repairable_schema_result(result)):
                    _observe(
                        decision=sql_audit_decision(result.status), policy=None,
                        sql=sql, status=result.status,
                        error_type=result.error_type or "",
                    )
                    return result

                if _is_repairable_schema_result(result):
                    if attempt < self.max_retries:
                        feedback = (
                            f"上次生成的 SQL:\n{sql}\n"
                            f"执行发现数据字典不匹配：{result.error}\n"
                            "请只使用已提供表结构中的真实列名，重新生成。"
                        )
                        continue
                    _observe(
                        decision=sql_audit_decision(result.status), policy=None,
                        sql=sql, status=result.status,
                        error_type=result.error_type or "",
                    )
                    return result

                # 其它失败：尝试重试
                if attempt < self.max_retries:
                    feedback = f"上次生成的 SQL:\n{sql}\n执行报错: {result.error}"
                    continue
                _observe(
                    decision=sql_audit_decision(result.status), policy=None,
                    sql=sql, status=result.status,
                    error_type=result.error_type or "",
                )
                return result

            except ValidationError as e:
                logger.warning(f"[SQLAgent] 校验失败 (第{attempt+1}次): {e}")
                _observe(
                    decision="DENY_VALIDATOR", policy=None, sql=sql or "",
                    deny_code=f"validator:{e.reason or 'unknown'}",
                )
                # STOP C 终态分类：安全/策略拒绝（表白名单/敏感列/危险函数/
                # 非 SELECT 等）不携带反馈重试——LLM 不允许根据拒绝原因
                # 改写后再试；仅 parse/别名/LIMIT 类语法错误可有限修复
                if attempt < self.max_retries and not e.is_terminal_deny:
                    feedback = (
                        f"上次生成的 SQL:\n{last_sql}\n"
                        f"被安全校验拒绝（{e}），请避免同样问题"
                    )
                    continue
                return SQLResult.failed(
                    status="validation_error",
                    error="查询未通过安全校验，请调整问题后重试。",
                    error_type="validation",
                )

            except RowSecurityError as e:
                logger.error(f"[SQLAgent] 行级安全注入失败: {e}")
                _observe(decision="DENY_SCOPE", policy=None,
                         deny_code="SQL_SCOPE_UNAVAILABLE")
                return SQLResult.failed(
                    status="permission_denied",
                    error="当前无法确定你的数据访问范围，查询被拒绝。",
                    error_type="row_security",
                )

            except Exception as e:
                logger.error(f"[SQLAgent] 执行失败 (第{attempt+1}次): {e}")
                _observe(decision="EXECUTION_FAILED", policy=None,
                         sql=sql or "", status="failed", error_type="unknown")
                if attempt < self.max_retries:
                    feedback = (
                        f"上次生成的 SQL:\n{last_sql}\n执行报错: {e}"
                        if last_sql else f"上次执行报错: {e}"
                    )
                    continue
                return SQLResult.failed(
                    status="failed",
                    error=f"查询失败: {e}",
                    error_type="unknown",
                )

        # 极端兜底（理论上不可达；防御性返回）
        if last_result is not None:
            return last_result
        return SQLResult.failed(
            status="failed",
            error="查询失败，已达到最大重试次数。",
            error_type="retry_exhausted",
        )

    # =================================================
    # 策略链（policy 链：权限门 + 表域判定 + 三维 scope 注入）
    # =================================================

    def _ask_struct_with_policy(
        self,
        question: str,
        policy: "SQLPolicyContext",
        *,
        event_sink: Callable[[dict], None] | None = None,
    ) -> SQLResult:
        """生产策略链路。与旧行为的差异：

        - 执行前过 SQLPolicyGuard（权限门/表域/scope 注入）；
        - SQLPolicyError 是终态拒绝（对外只回安全文案，原因进日志），
          不进入「带反馈重试」——安全拒绝不允许模型绕过；
        - ValidationError（解析/别名等语法类）保留既有有限重试，
          且每次重试重新走 Guard；
        - scope 注入取代旧 inject_row_filter（避免 customer_id 双重注入）。
        """
        from backend.sql.policy import SQLPolicyGuard, SQLPolicyError

        logger.info(
            f"[SQLAgent:policy] 收到问题: {question[:80]}... "
            f"(user={policy.user_id}, scope={policy.data_scope}, "
            f"dept={policy.department or '-'}, tenant={policy.tenant_id or '-'}, "
            f"source={policy.source_channel or '-'})"
        )

        guard = SQLPolicyGuard()
        # — Step 0: 前置权限门（STOP C §八）：无权限/非法 scope 在任何
        #    LLM 调用之前拒绝——路由选表与 SQL 生成都不发生
        try:
            guard.precheck(policy)
        except SQLPolicyError as e:
            logger.warning(f"[SQLAgent:policy] 前置策略拒绝 code={e.code}: {e}")
            _observe(decision=_DENY_DECISION.get(e.code, "DENY_SCOPE"),
                     policy=policy, deny_code=e.code)
            return SQLResult.failed(
                status="permission_denied",
                error=e.user_text,
                error_type="row_security",
            )

        emit_sql_stage(
            event_sink, node="query_understanding", phase="understanding",
            message="已理解查询需求")

        # — Step 1: 路由选表（与旧链路一致）—
        try:
            allowed_tables = guard.get_allowed_tables(policy)
            # 兼容现有 Router/测试替身的单参数调用，同时把最终交给
            # Generator 的表集合收口到当前主体授权范围。
            table_names = [
                table for table in _select_authorized_tables(
                    question, allowed_tables)
                if table in allowed_tables
            ]
            emit_sql_stage(
                event_sink, node="table_router", phase="table_routing",
                message=f"已匹配 {len(table_names)} 张授权数据表",
                tables=table_names[:20], status="success")
        except Exception as e:
            logger.error(f"[SQLAgent:policy] 表路由失败: {e}")
            _observe(decision="EXECUTION_FAILED", policy=policy,
                     status="failed", error_type="router_error")
            return SQLResult.failed(
                status="failed",
                error=f"内部错误：表路由失败 {e}",
                error_type="router_error",
            )
        if not table_names:
            _observe(decision="EXECUTION_FAILED", policy=policy,
                     status="permission_denied", error_type="table_scope")
            return SQLResult.failed(
                status="permission_denied",
                error="该数据不在当前可访问范围内。",
                error_type="row_security",
            )

        # — Step 2-5: 生成 + Guard 循环 —
        feedback: str | None = None
        sql: str | None = None
        last_result: SQLResult | None = None
        for attempt in range(self.max_retries + 1):
            try:
                emit_sql_stage(
                    event_sink, node="sql_generator", phase="sql_generation",
                    message="正在生成查询语句")
                sql = generate_sql(question, table_names, feedback=feedback)

                emit_sql_stage(
                    event_sink, node="sql_validator", phase="sql_validation",
                    message="正在校验查询安全性")
                guarded = guard.validate_and_rewrite(sql, policy)
                emit_sql_stage(
                    event_sink, node="sql_executor", phase="tool_start",
                    message="正在调用数据查询工具", status="running")
                result = execute_sql_struct(
                    guarded.executable_sql, self.db_config,
                    params=guarded.params,
                )
                last_result = result
                emit_sql_stage(
                    event_sink, node="sql_executor", phase="tool_result",
                    message=("查询完成" if result.status in ("success", "no_data")
                             else "查询未完成"),
                    status=("success" if result.status in ("success", "no_data")
                            else "failed"),
                    row_count=result.row_count,
                    error_type=result.error_type or "",
                )

                if result.status in ("success", "no_data"):
                    _observe(
                        decision=sql_audit_decision(result.status),
                        policy=policy, sql=sql,
                        tables=guarded.referenced_tables,
                        duration_ms=int((result.elapsed_sec or 0) * 1000),
                        row_count=result.row_count,
                        status=result.status,
                    )
                    return result
                # 表/列不存在属于可修复的模型生成错误；其它语法/权限类终态。
                if result.status in ("validation_error", "permission_denied") \
                        or (result.status == "syntax_error"
                            and not _is_repairable_schema_result(result)):
                    _observe(
                        decision=sql_audit_decision(result.status),
                        policy=policy, sql=sql,
                        tables=guarded.referenced_tables,
                        duration_ms=int((result.elapsed_sec or 0) * 1000),
                        status=result.status,
                        error_type=result.error_type or "",
                    )
                    return result

                if _is_repairable_schema_result(result):
                    if attempt < self.max_retries:
                        feedback = (
                            f"上次生成的 SQL:\n{sql}\n"
                            f"执行发现数据字典不匹配：{result.error}\n"
                            "请只使用已提供表结构中的真实列名，重新生成。"
                        )
                        continue
                    _observe(
                        decision=sql_audit_decision(result.status), policy=policy,
                        sql=sql, tables=guarded.referenced_tables,
                        duration_ms=int((result.elapsed_sec or 0) * 1000),
                        status=result.status,
                        error_type=result.error_type or "",
                    )
                    return result
                if attempt < self.max_retries:
                    feedback = f"上次生成的 SQL:\n{sql}\n执行报错: {result.error}"
                    continue
                _observe(
                    decision=sql_audit_decision(result.status),
                    policy=policy, sql=sql,
                    tables=guarded.referenced_tables,
                    duration_ms=int((result.elapsed_sec or 0) * 1000),
                    status=result.status,
                    error_type=result.error_type or "",
                )
                return result

            except SQLPolicyError as e:
                # 安全拒绝 = 终态（对外安全文案，详细原因只进日志）；
                # STOP C：deny 后不携带 feedback 重试，generate 次数 = attempt+1
                logger.warning(
                    f"[SQLAgent:policy] 策略拒绝 code={e.code}: {e}")
                _observe(
                    decision=_DENY_DECISION.get(e.code, "DENY_SCOPE"),
                    policy=policy, sql=sql,
                    deny_code=e.code,
                )
                return SQLResult.failed(
                    status="permission_denied",
                    error=e.user_text,
                    error_type="row_security",
                )

            except ValidationError as e:
                logger.warning(
                    f"[SQLAgent:policy] 校验失败 (第{attempt+1}次): {e}")
                _observe(
                    decision="DENY_VALIDATOR", policy=policy, sql=sql or "",
                    deny_code=f"validator:{e.reason or 'unknown'}",
                )
                if attempt < self.max_retries and not e.is_terminal_deny:
                    # 仅语法/schema 类可带反馈修复重试（每次重走 Guard）；
                    # 安全拒绝（table/column/function/类型类）为终态
                    feedback = (
                        f"上次生成的 SQL:\n{sql}\n"
                        f"被安全校验拒绝（{e}），请避免同样问题"
                    )
                    continue
                return SQLResult.failed(
                    status="validation_error",
                    error="查询未通过安全校验，请调整问题后重试。",
                    error_type="validation",
                )

            except Exception as e:
                logger.error(f"[SQLAgent:policy] 执行失败 (第{attempt+1}次): {e}")
                _observe(decision="EXECUTION_FAILED", policy=policy,
                         sql=sql or "", status="failed",
                         error_type="unknown")
                if attempt < self.max_retries:
                    feedback = f"执行报错: {e}"
                    continue
                return SQLResult.failed(
                    status="failed",
                    error=f"查询失败: {e}",
                    error_type="unknown",
                )

        if last_result is not None:
            return last_result
        return SQLResult.failed(
            status="failed",
            error="查询失败，已达到最大重试次数。",
            error_type="retry_exhausted",
        )


# =================================================
# 工厂（多 Agent/路由懒加载）
# =================================================

def init_sql_agent(db_config: dict, max_retries: int = 1) -> SQLAgent:
    """构造 SQLAgent 实例。"""
    host = db_config.get("host", "?")
    dbname = db_config.get("dbname", "?")
    logger.info(f"SQLAgent 初始化完成: postgresql://{host}/{dbname}")
    return SQLAgent(db_config=db_config, max_retries=max_retries)


# 线程安全单例（供 FastAPI deps + MCP server 共用）
import threading as _threading
_sql_agent_lock = _threading.Lock()
_sql_agent_singleton: SQLAgent | None = None


def get_sql_agent() -> SQLAgent:
    """惰性初始化 SQLAgent 单例（线程安全，连业务库）。"""
    global _sql_agent_singleton
    if _sql_agent_singleton is None:
        with _sql_agent_lock:
            if _sql_agent_singleton is None:
                from backend.config import BUSINESS_DB_CONFIG
                _sql_agent_singleton = init_sql_agent(BUSINESS_DB_CONFIG, max_retries=2)
    return _sql_agent_singleton

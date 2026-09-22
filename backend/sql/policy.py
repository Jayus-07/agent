"""
policy.py — SQL Policy Guard（SQL Agent 生产收口 STOP B）

在既有 6 层硬校验（sql_validator）之上，把 AuthorizationContext 的
permission_codes / data_scope 落成可执行的确定性数据范围控制：

  权限门  →  sql.read 权限点（映射唯一来源 security/authorization.py，
             本模块只消费 has_permission，禁止自判角色）
  表域判定 →  TablePolicy.data_domain（shared/personal/internal）
  三维注入 →  tenant_column / department_column / self_column 声明位，
             逐表逐别名参数化注入（值全部走 %(name)s 通道）

身份来源：SQLPolicyContext 只能由 Principal/AuthorizationContext 装配
（SQLSkill 经图状态、HTTP 通道经 deps.get_authorization_context），
本模块不做任何身份解析。

与旧 inject_row_filter 的关系（过渡，STOP C 收口）：
  走本 Guard 的链路不再调用 inject_row_filter，避免 order.orders.customer_id
  双重注入；旧 row_security 配置与 SQL_ROW_SECURITY_ENABLED 开关保留，
  供未接策略上下文的存量调用（评测 runner / 脚本）维持旧行为。

不重写已有 Guard：sqlglot AST、单语句 SELECT、递归 CTE 写检测、表/列
白名单、危险函数、LIMIT 均由 sql_validator 承担，本模块组合复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

import sqlglot
from sqlglot import exp

from backend.security.authorization import AuthorizationContext
from backend.security.principal import Principal
from backend.shared.logger import logger
from backend.sql.schema_loader import TablePolicy, schema_loader
from backend.sql.sql_validator import ValidationError, sql_validator

# ── 对外错误码（低基数；对外文案见 _USER_SAFE_TEXT，详细原因只进日志）──
SQL_PERMISSION_DENIED = "SQL_PERMISSION_DENIED"
SQL_TABLE_NOT_ALLOWED = "SQL_TABLE_NOT_ALLOWED"
SQL_SCOPE_UNAVAILABLE = "SQL_SCOPE_UNAVAILABLE"

_VALID_SCOPES = ("all", "department", "self")

# scope 参数占位符（psycopg2 named params）；同值多表共享同一占位符
_PARAM_TENANT = "sql_scope_tenant_id"
_PARAM_DEPARTMENT = "sql_scope_department"
_PARAM_SELF = "sql_scope_self_value"

_USER_SAFE_TEXT = {
    SQL_PERMISSION_DENIED: "当前查询超出你的数据访问范围。",
    SQL_TABLE_NOT_ALLOWED: "该数据不在当前可访问范围内。",
    SQL_SCOPE_UNAVAILABLE: "暂时无法确定你的数据访问范围，查询被拒绝。",
}


class SQLPolicyError(Exception):
    """策略拒绝（终态，调用方不得携带原因重试绕过）。

    code 供上游映射错误协议；对外文案一律取 _USER_SAFE_TEXT，
    详细原因（表名/缺失维度）只进日志，不回显给用户。
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    @property
    def user_text(self) -> str:
        return _USER_SAFE_TEXT.get(self.code, _USER_SAFE_TEXT[SQL_SCOPE_UNAVAILABLE])


def resolve_self_value(user_id: str) -> int | None:
    """self scope 的身份值解析（demo warehouse 显式兼容映射）。

    当前业务仓库 order.orders.customer_id 是 integer，而网关身份
    Principal.user_id 是 auth 侧字符串 ID。约定：纯数字 user_id 且在
    PG integer 范围内时显式转为整数 self_value；其余（UUID/用户名/空/
    超出 integer 范围）一律解析失败 → 调用方 fail-closed 拒绝
    （超范围值留到执行期只会变成 PG 类型错误，早拒更干净）。

    ⚠️ 这是当前 demo warehouse 的显式兼容映射，
    不代表 auth user_id 与 customer_id 是领域上同一身份。
    禁止猜 customer_id / 按用户名查找 / 模糊匹配 / 默认值。
    """
    s = str(user_id or "").strip()
    if not s.isdigit():
        return None
    value = int(s)
    if value > 2_147_483_647:  # PG integer 上限
        return None
    return value


@dataclass(frozen=True)
class SQLPolicyContext:
    """一次 SQL 查询的策略上下文（规格书 §十形态，复用不新建）。

    只能由 Principal/AuthorizationContext 装配（build_sql_policy_context），
    不得携带请求体/客户端声明的身份字段。
    source_channel（STOP C）：http|graph|tool|mcp，仅用于审计/metrics
    归因，低基数，不参与任何授权判定。
    """

    principal: Principal
    authz: AuthorizationContext
    source_channel: str = ""

    @property
    def user_id(self) -> str:
        return self.principal.user_id

    @property
    def tenant_id(self) -> str:
        return self.authz.tenant_id

    @property
    def department(self) -> str:
        return self.authz.department

    @property
    def data_scope(self) -> str | None:
        return self.authz.data_scope


def build_sql_policy_context(
    *,
    user_id: str,
    department: str = "",
    tenant_id: str = "",
    roles: tuple[str, ...] = (),
    data_scope: str | None = None,
    source_channel: str = "",
) -> SQLPolicyContext:
    """图/Tool 通道装配入口（HTTP 通道走 deps.get_authorization_context）。

    权限与 scope 推导统一发生在 security/authorization.py；此处只装配。
    data_scope 传入口已折算值（RequestContext.data_scope）时透传采用，
    缺省由 roles 重新推导（同一规则，非第二套逻辑）。
    """
    from backend.security.authorization import build_tool_authorization_context

    authz = build_tool_authorization_context(
        user_id=user_id,
        department=department,
        tenant_id=tenant_id,
        roles=tuple(roles),
        data_scope=data_scope,
    )
    return SQLPolicyContext(
        principal=authz.principal, authz=authz,
        source_channel=(source_channel or "")[:16],
    )


@dataclass
class GuardedSQL:
    """Guard 产出：可执行 SQL + 注入参数 + 决策元数据（规格书 §二十三）。"""

    original_sql: str
    executable_sql: str
    referenced_tables: Tuple[str, ...]
    applied_scopes: Tuple[str, ...]
    row_limit: int
    params: Dict[str, Any] = field(default_factory=dict)


class SQLPolicyGuard:
    """组合 sql_validator + 三维 scope 注入的确定性策略闸门。"""

    def precheck(self, policy: SQLPolicyContext) -> None:
        """前置权限门（STOP C §八）：权限点 + scope 合法性校验。

        供 agent 在路由选表/LLM 生成**之前**调用——无权限用户不触发
        任何 LLM 调用；validate_and_rewrite 内部重复执行同一检查
        （纵深，防调用方绕过 precheck 直接进 Guard）。
        """
        if not policy.authz.has_permission("sql.read"):
            raise SQLPolicyError(
                SQL_PERMISSION_DENIED,
                f"user={policy.user_id!r} 无 sql.read 权限"
                f"(roles={policy.principal.roles})",
            )
        scope = policy.data_scope
        if scope not in _VALID_SCOPES:
            raise SQLPolicyError(
                SQL_SCOPE_UNAVAILABLE,
                f"data_scope={scope!r} 非法（合法值: {_VALID_SCOPES}）",
            )

    def validate_and_rewrite(self, sql: str, policy: SQLPolicyContext) -> GuardedSQL:
        # sql.guard span（STOP C §十二）：低基数 attributes，deny 也收口；
        # best-effort——无 active trace 时 start_span 返回 noop，不阻塞查询
        _span = None
        try:
            from backend.observability.tracer import trace_collector

            import uuid as _uuid

            _span = trace_collector.start_span(
                f"sql.guard.{_uuid.uuid4().hex[:8]}", name="sql.guard",
                kind="tool_call",
            )
        except Exception:
            _span = None
        try:
            return self._validate_and_rewrite_inner(sql, policy)
        except SQLPolicyError as e:
            self._end_guard_span(_span, policy, None, e.code)
            raise
        except ValidationError as e:
            self._end_guard_span(_span, policy, None,
                                 f"validator:{e.reason or 'unknown'}")
            raise
        else:
            self._end_guard_span(_span, policy, None, "")

    def _end_guard_span(self, span, policy: SQLPolicyContext,
                        guarded: GuardedSQL | None, reason_code: str) -> None:
        if span is None:
            return
        try:
            from backend.observability.tracer import trace_collector

            table_count = len(guarded.referenced_tables) if guarded else 0
            trace_collector.end_span(
                span,
                metrics={
                    "source_channel": policy.source_channel or "unknown",
                    "data_scope": str(policy.data_scope or ""),
                    "decision": ("deny" if reason_code else
                                 ("allow" if guarded and guarded.applied_scopes
                                  else "allow_no_scope")),
                    "reason_code": reason_code,
                    "table_count": table_count,
                },
                status="error" if reason_code else "success",
            )
        except Exception:
            pass  # 观测永不阻塞安全判定

    def _validate_and_rewrite_inner(
        self, sql: str, policy: SQLPolicyContext,
    ) -> GuardedSQL:
        # ── 1. 权限门（fail-closed：无 sql.read 一律拒绝；与 precheck
        #      同一检查——纵深防绕过）──
        self.precheck(policy)
        scope = policy.data_scope

        # ── 2. 既有 6 层硬校验（只读/白名单/敏感列/危险函数/LIMIT）──
        try:
            safe_sql, _table_names, stmt = sql_validator.validate(sql)
        except ValidationError:
            raise  # 语法/schema 类，由调用方按既有重试语义处理

        # ── 3. 表域判定（全 AST 真实表引用；任一表拒绝 → 整条拒绝）──
        cte_names = {cte.alias.lower() for cte in stmt.find_all(exp.CTE)}
        table_refs = _collect_table_refs(stmt, cte_names)
        for qualified_name in table_refs:
            self._check_tableallowed(qualified_name, scope, policy)

        # ── 4. 按 SELECT scope 逐组注入（UNION 分支/子查询/CTE body
        #      各自是独立 Select scope，逐组处理保证 scope 不丢）──
        params: Dict[str, Any] = {}
        applied: set[str] = set()
        # 先物化 scope 列表再原地修改（where(copy=False) 直接改节点 args，
        # 边遍历边改会干扰 find_all 迭代）
        for sel in list(stmt.find_all(exp.Select)):
            scope_refs = _collect_scope_table_refs(sel, cte_names)
            conditions = []
            for qualified_name, alias in scope_refs:
                tp = schema_loader.get_table_policy(qualified_name)
                conds = self._conditions_for_table(
                    tp, alias, scope, policy, params, applied)
                conditions.extend(conds)
            for cond in conditions:
                # copy=False：原地 AND 追加到本 scope 的 WHERE
                # （默认 copy=True 返回副本，返回值丢弃 = 注入静默丢失）
                sel.where(cond, copy=False)

        executable_sql = stmt.sql(dialect="postgres")
        return GuardedSQL(
            original_sql=sql,
            executable_sql=executable_sql,
            referenced_tables=tuple(sorted(table_refs)),
            applied_scopes=tuple(sorted(applied)),
            row_limit=schema_loader.max_limit,
            params=params,
        )

    # =================================================
    # 内部：判定与条件构造
    # =================================================

    def _check_tableallowed(
        self, qualified_name: str, scope: str, policy: SQLPolicyContext,
    ) -> None:
        """单表可读性判定（域 × scope；注入所需维度缺失在注入阶段判定）。"""
        tp = schema_loader.get_table_policy(qualified_name)
        if tp.data_domain == "internal" and scope != "all":
            raise SQLPolicyError(
                SQL_TABLE_NOT_ALLOWED,
                f"internal 表 {qualified_name} 仅 data_scope=all 可读"
                f"(当前 scope={scope})",
            )
        if scope == "department" and tp.data_domain == "personal" \
                and not tp.department_column:
            # personal 数据无部门列可表达部门过滤 → fail-closed（D2）
            raise SQLPolicyError(
                SQL_TABLE_NOT_ALLOWED,
                f"personal 表 {qualified_name} 无 department_column，"
                "department scope 不可表达 → 拒绝",
            )
        if scope == "self" and not tp.self_column:
            # self 只能读与当前用户关联的数据；无归属列即不可表达（§三十六）
            raise SQLPolicyError(
                SQL_TABLE_NOT_ALLOWED,
                f"表 {qualified_name} 无 self_column，self scope 不可表达 → 拒绝",
            )

    def _conditions_for_table(
        self,
        tp: TablePolicy,
        alias: str,
        scope: str,
        policy: SQLPolicyContext,
        params: Dict[str, Any],
        applied: set[str],
    ) -> list[exp.Expression]:
        """为单个 (表, 别名) 引用构造 scope 条件（值全部参数化）。"""
        conditions: list[exp.Expression] = []

        # tenant 永远优先：声明了租户列就注入，all 也不例外（§十二）。
        if tp.tenant_column:
            if not policy.tenant_id:
                raise SQLPolicyError(
                    SQL_SCOPE_UNAVAILABLE,
                    f"表要求租户隔离（{alias}.{tp.tenant_column}）"
                    "但上下文缺少 tenant_id",
                )
            params[_PARAM_TENANT] = policy.tenant_id
            conditions.append(_eq_placeholder(alias, tp.tenant_column, _PARAM_TENANT))
            applied.add("tenant")

        if tp.department_column and scope == "department":
            if not policy.department:
                raise SQLPolicyError(
                    SQL_SCOPE_UNAVAILABLE,
                    f"data_scope=department 但 principal.department 为空"
                    f"（表 {alias}.{tp.department_column} 要求部门过滤）",
                )
            params[_PARAM_DEPARTMENT] = policy.department
            conditions.append(
                _eq_placeholder(alias, tp.department_column, _PARAM_DEPARTMENT))
            applied.add("department")

        if tp.self_column and scope == "self":
            self_value = resolve_self_value(policy.user_id)
            if self_value is None:
                raise SQLPolicyError(
                    SQL_SCOPE_UNAVAILABLE,
                    f"self scope 无法从 user_id={policy.user_id!r} 解析出"
                    f" {alias}.{tp.self_column} 的关联值（demo 显式映射：仅纯数字）",
                )
            params[_PARAM_SELF] = self_value
            conditions.append(_eq_placeholder(alias, tp.self_column, _PARAM_SELF))
            applied.add("self")

        return conditions


# =================================================
# AST 辅助（独立函数便于单测直测引擎行为）
# =================================================

def _collect_table_refs(stmt: exp.Expression, cte_names: set[str]) -> set[str]:
    """全 AST 真实表引用（qualified 名；跳过 CTE 别名引用）。"""
    refs: set[str] = set()
    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names:
            continue
        db = (table.db or "").lower()
        refs.add(f"{db}.{name}" if db else name)
    return refs


def _collect_scope_table_refs(
    sel: exp.Select, cte_names: set[str],
) -> list[Tuple[str, str]]:
    """单个 SELECT scope 的直接表引用 [(qualified_name, alias)]。

    只取本层 from/joins（sqlglot 30.x 键名 from_），子查询/CTE body
    的表属于它们自己的 scope，由 find_all(exp.Select) 的其他节点处理。
    自连接（同表多别名）逐别名返回，各自注入。
    """
    refs: list[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    nodes = []
    from_clause = sel.args.get("from_") or sel.args.get("from")
    if from_clause is not None and isinstance(from_clause.this, exp.Table):
        nodes.append(from_clause.this)
    for join in sel.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            nodes.append(join.this)
    for table in nodes:
        name = table.name.lower()
        if name in cte_names:
            continue
        db = (table.db or "").lower()
        qualified = f"{db}.{name}" if db else name
        alias = (table.alias_or_name or name).lower()
        key = (qualified, alias)
        if key not in seen:
            seen.add(key)
            refs.append(key)
    return refs


def _eq_placeholder(
    alias: str, column: str, param_name: str,
) -> exp.Expression:
    """alias.column = %(param_name)s（值走 psycopg2 参数通道）。"""
    return exp.EQ(
        this=exp.Column(
            this=exp.Identifier(this=column),
            table=exp.Identifier(this=alias),
        ),
        expression=exp.Placeholder(this=exp.Identifier(this=param_name)),
    )


# 模块级便捷入口（Guard 无状态，可复用单例）
def validate_and_rewrite(sql: str, policy: SQLPolicyContext) -> GuardedSQL:
    return SQLPolicyGuard().validate_and_rewrite(sql, policy)

"""
row_security.py — 行级安全注入（参数化版本）

使用 sqlglot 重写 AST，为受保护表注入 WHERE 条件，**使用 psycopg2 %s 参数占位符**，
不在 SQL 中硬编码用户 ID。

策略:
  - 对每个有 row_security 配置的表，自动追加 AND table.column = %(name)s
  - 严格模式：如果 SQL 引用了 row-secured 表但缺少必需 param → 抛 RowSecurityError
    （不再静默跳过 — 旧版本会导致"未登录用户能查所有人的数据"的安全漏洞）
  - 多个 row-secured 表共享同一 param 时，去重占位符

返回值:
  (new_sql, params) — params 是字典，键为占位符名，值为实际值
"""
import re
import sqlglot
from sqlglot import exp
from typing import Dict, Tuple

from backend.sql.schema_loader import schema_loader
from backend.shared.logger import logger


class RowSecurityError(Exception):
    """行级安全注入失败（缺参数、SQL 解析失败等）"""
    pass


# 占位符名安全化：psycopg2 named placeholder 必须是合法 Python 标识符
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_]")


def _safe_name(name: str) -> str:
    return _SAFE_NAME_RE.sub("_", name)


def inject_row_filter(
    sql: str,
    user_context: Dict[str, int],
) -> Tuple[str, Dict[str, int]]:
    """
    向 SQL 注入行级安全条件（参数化版本）。

    参数:
        sql: 已经过 validator 校验的安全 SQL
        user_context: 会话上下文，如 {"current_user_id": 101}

    返回:
        (new_sql, params) — new_sql 中的 row-secured 条件已用 %(name)s 占位符替换，
                            params 是对应的值映射
    """
    parsed = sqlglot.parse(sql, read="postgres")
    if not parsed:
        raise RowSecurityError("SQL 解析失败，无法注入行级条件")

    stmt = parsed[0]
    if not isinstance(stmt, exp.Select):
        raise RowSecurityError("只支持 SELECT 注入行级条件")

    # — 收集 SQL 中引用的表及其别名 —
    real_to_alias: Dict[str, str] = {}
    referenced_tables = set()
    for table in stmt.find_all(exp.Table):
        real_name = table.name.lower()
        referenced_tables.add(real_name)
        alias = table.alias_or_name.lower()
        real_to_alias[real_name] = alias

    # — 找出哪些引用表是受保护的 —
    protected_tables = [
        t for t in referenced_tables
        if schema_loader.get_row_security(t)
    ]

    # — 严格模式：受保护表缺少参数 → 抛错（不静默跳过）—
    required_params = {
        schema_loader.get_row_security(t)["param"]
        for t in protected_tables
    }
    missing = required_params - set(user_context.keys())
    if missing:
        # 检查是否所有受保护表都缺 param：是 → 拒绝查询
        # （如有部分有部分缺，理论上应分别处理，但保守起见仍拒绝）
        raise RowSecurityError(
            f"行级安全要求参数 {sorted(missing)} 但 user_context 中缺失 "
            f"(受保护表: {protected_tables}, "
            f"user_context keys: {list(user_context.keys())})"
        )

    # — 为每个受保护的引用表构建条件（参数化）—
    extra_conditions = []
    params: Dict[str, int] = {}
    for tname in referenced_tables:
        rs_config = schema_loader.get_row_security(tname)
        if not rs_config:
            continue

        column = rs_config["column"]
        param_name = rs_config["param"]

        # 已通过严格模式校验，这里一定能拿到
        param_value = user_context[param_name]

        # 占位符名：表名_列名_param（去重 + 合法标识符）
        # 多个受保护表共享同一 param 时用同名占位符，去重到 params
        placeholder_key = _safe_name(f"{tname}_{column}")
        params[placeholder_key] = param_value

        alias = real_to_alias.get(tname, tname)
        col_ref = exp.Column(
            this=exp.Identifier(this=column),
            table=exp.Identifier(this=alias),
        )
        # 使用 sqlglot.Placeholder，psycopg2 named param 语法
        placeholder = exp.Placeholder(
            this=exp.Identifier(this=placeholder_key),
        )
        condition = exp.EQ(this=col_ref, expression=placeholder)
        extra_conditions.append(condition)
        logger.info(
            f"[RowSecurity] 注入: {alias}.{column} = %({placeholder_key})s "
            f"(value 不进 SQL, 走参数化通道)"
        )

    if not extra_conditions:
        return sql, {}

    # — 合并所有条件 —
    combined = extra_conditions[0]
    for cond in extra_conditions[1:]:
        combined = exp.And(this=combined, expression=cond)

    # — 注入到 WHERE —
    existing_where = stmt.find(exp.Where)
    if existing_where:
        combined = exp.And(this=existing_where.this.copy(), expression=combined.copy())
        existing_where.set("this", combined)
    else:
        stmt = stmt.where(combined)

    new_sql = stmt.sql(dialect="postgres")
    logger.info(
        f"[RowSecurity] 注入后: {new_sql[:180]} | params={list(params.keys())}"
    )
    return new_sql, params

"""
row_security.py — 行级安全注入

使用 sqlglot 重写 AST，自动为带有行级安全策略的表注入 WHERE 条件。

策略:
  - 对每个有 row_security 配置的表，自动追加 AND table.column = param_value
  - 如果会话上下文中没有所需参数（如未提供 current_user_id），跳过该表的注入
  - 如果 SQL 中已有 WHERE，追加 AND；无 WHERE 则新建
"""
import sqlglot
from sqlglot import exp
from typing import Dict

from sql_agent.schema_loader import schema_loader
from utils.logger import logger


class RowSecurityError(Exception):
    """行级安全注入失败"""
    pass


def inject_row_filter(sql: str, user_context: Dict[str, int]) -> str:
    """
    向 SQL 注入行级安全条件。

    参数:
        sql: 已经过 validator 校验的安全 SQL
        user_context: 会话上下文，如 {"current_user_id": 101}

    返回:
        注入了行级条件的新 SQL（或原 SQL，如果没有匹配的策略）
    """
    parsed = sqlglot.parse(sql, read="postgres")
    if not parsed:
        raise RowSecurityError("SQL 解析失败，无法注入行级条件")

    stmt = parsed[0]
    if not isinstance(stmt, exp.Select):
        raise RowSecurityError("只支持 SELECT 注入行级条件")

    # — 收集 SQL 中引用的表及其别名 —
    # real_name → alias (无别名时 alias == real_name)
    real_to_alias: Dict[str, str] = {}
    referenced_tables = set()
    for table in stmt.find_all(exp.Table):
        real_name = table.name.lower()
        referenced_tables.add(real_name)
        alias = table.alias_or_name.lower()
        real_to_alias[real_name] = alias

    # — 为每个受保护的引用表构建条件 —
    extra_conditions = []
    for tname in referenced_tables:
        rs_config = schema_loader.get_row_security(tname)
        if not rs_config:
            continue

        column = rs_config["column"]
        param_name = rs_config["param"]

        if param_name not in user_context:
            logger.info(
                f"[RowSecurity] 跳过表 '{tname}': 缺少参数 '{param_name}' "
                f"(可用: {list(user_context.keys())})"
            )
            continue

        param_value = user_context[param_name]

        alias = real_to_alias.get(tname, tname)
        col_ref = exp.Column(
            this=exp.Identifier(this=column),
            table=exp.Identifier(this=alias),
        )
        val_ref = exp.Literal(this=str(param_value), is_string=False)
        condition = exp.EQ(this=col_ref, expression=val_ref)
        extra_conditions.append(condition)
        logger.info(f"[RowSecurity] 注入: {alias}.{column} = {param_value}")

    if not extra_conditions:
        return sql

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
    logger.info(f"[RowSecurity] 注入后: {new_sql[:180]}")
    return new_sql

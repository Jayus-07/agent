"""
sql_validator.py — SQL 硬校验（核心安全模块）

6 层硬校验，每层都不依赖 LLM 承诺：

  Layer 1: SQL 类型校验   → 必须为 SELECT，禁止多语句
  Layer 2: 表名白名单      → 所有 FROM/JOIN 表名必须在白名单中
  Layer 3: 列名白名单      → 敏感列直接拒绝
  Layer 4: 行级安全注入     → (在 row_security.py 中)
  Layer 5: 资源限制         → LIMIT 强制添加 (在此模块中)
  Layer 6: 执行层           → (在 executor.py 中)
"""

import sqlglot
from sqlglot import exp
from typing import Tuple, Optional, Set, Dict

from backend.sql.schema_loader import schema_loader
from backend.shared.logger import logger


class ValidationError(Exception):
    """校验失败异常，包含友好错误消息"""
    def __init__(self, message: str, layer: int = 0):
        self.layer = layer
        super().__init__(message)


class SQLValidator:
    """SQL 安全校验器"""

    def __init__(self):
        self.allowed_tables = schema_loader.allowed_tables
        self.allowed_schemas = schema_loader.allowed_schemas
        self.sensitive_columns = schema_loader.sensitive_columns
        self.banned_functions = schema_loader.banned_functions
        self.max_limit = schema_loader.max_limit

    # =================================================
    # Layer 1: SQL 类型校验 — 必须为 SELECT
    # =================================================

    def _check_statement_type(self, parsed: list) -> None:
        """检查 AST 顶层语句类型，只允许单个 SELECT"""
        if not parsed:
            raise ValidationError("SQL 语句为空", layer=1)

        if len(parsed) > 1:
            statements = [type(s).__name__ for s in parsed]
            raise ValidationError(
                f"禁止多条语句，检测到 {len(parsed)} 条: {statements}",
                layer=1,
            )

        stmt = parsed[0]

        if not isinstance(stmt, exp.Select):
            stmt_type = type(stmt).__name__
            raise ValidationError(
                f"只允许 SELECT 查询，检测到 {stmt_type}",
                layer=1,
            )

        self._check_no_write_in_subqueries(stmt)

    def _check_no_write_in_subqueries(self, node: exp.Expression):
        """递归遍历 AST，确保子查询 / CTE 中无 INSERT/UPDATE/DELETE"""
        write_types = (
            exp.Insert, exp.Update, exp.Delete,
            exp.Drop, exp.Create, exp.Alter, exp.TruncateTable,
        )
        for child in node.walk():
            if isinstance(child, write_types):
                raise ValidationError(
                    f"语句中包含禁止操作 {type(child).__name__}",
                    layer=1,
                )

    # =================================================
    # Layer 2: 表名白名单
    # =================================================

    def _extract_table_names(self, parsed: list) -> Set[str]:
        """从 AST 中提取所有被引用的表名（schema-qualified）。

        兼容两种形式：
          1) `product.products` —— db='product', name='products' → 拼成 `product.products`
          2) `products` —— 仅 name，无 db → 视为缺省域

        返回集合元素是 schema_loader.allowed_tables 用的 key（schema-qualified 或裸名）。
        """
        tables = set()
        stmt = parsed[0]
        for table in stmt.find_all(exp.Table):
            name = table.name.lower()
            db = (table.db or "").lower() if hasattr(table, "db") else ""
            if db:
                qname = f"{db}.{name}"
            else:
                # 裸名：若是 schema-qualified key（包含点）就保留原样
                qname = name
            tables.add(qname)
        return tables

    def _check_table_allowlist(self, table_names: Set[str]) -> None:
        """检查所有表名是否在白名单中。

        三种合法输入：
          1. `schema.table` 全限定名 — 直接命中 self.allowed_tables
          2. 裸 `table` 名（无 schema） — 必须命中某个 schema 下的表，否则拒
          3. `schema` 同时须在 self.allowed_schemas 中（防止 schema 不存在被绕过）
        """
        for qname in table_names:
            schema_name, table_name = schema_loader.split_qualified(qname)

            # 形式 1：schema-qualified 全限定
            if qname in self.allowed_tables:
                if schema_name and schema_name not in self.allowed_schemas:
                    raise ValidationError(
                        f"禁止访问 schema '{schema_name}'，白名单: {sorted(self.allowed_schemas)}",
                        layer=2,
                    )
                continue

            # 形式 2：裸表名（无 schema）— 尝试在所有 schema 中查找
            if not schema_name:
                matched = [
                    q for q in self.allowed_tables
                    if q.split(".", 1)[-1] == qname
                ]
                if matched:
                    continue

            raise ValidationError(
                f"禁止访问表 '{qname}'，白名单: {sorted(self.allowed_tables)}",
                layer=2,
            )

    # =================================================
    # Layer 3: 列级安全 — 敏感列直接拒绝
    # =================================================

    def _check_sensitive_columns(self, parsed: list, table_names: Set[str]) -> None:
        """检查 SELECT / WHERE 中是否引用了敏感列"""
        stmt = parsed[0]
        for column in stmt.find_all(exp.Column):
            col_name = column.name.lower()
            table_name = column.table.lower() if column.table else ""

            full_ref = f"{table_name}.{col_name}" if table_name else col_name

            for sensitive_ref in self.sensitive_columns:
                sens_parts = sensitive_ref.split(".")
                sens_col = sens_parts[-1]
                sens_table = sens_parts[0] if len(sens_parts) > 1 else ""

                if col_name == sens_col:
                    if not sens_table or table_name == sens_table:
                        raise ValidationError(
                            f"禁止查询敏感列: '{full_ref}' (敏感列: {sensitive_ref})",
                            layer=3,
                        )

    # =================================================
    # Layer 4: 禁止函数检查
    # =================================================

    def _check_banned_functions(self, parsed: list) -> None:
        """检查 SQL 中是否包含禁止的函数调用"""
        stmt = parsed[0]
        for func in stmt.find_all(exp.Anonymous):
            func_name = func.name.upper() if func.name else ""
            if func_name in self.banned_functions:
                raise ValidationError(
                    f"禁止使用函数: {func_name}()",
                    layer=4,
                )
        for func in stmt.find_all(exp.Func):
            func_name = type(func).__name__.upper()
            sql_name = func.sql_name().upper() if hasattr(func, 'sql_name') else ""
            for banned in self.banned_functions:
                if sql_name == banned or func_name == banned:
                    raise ValidationError(
                        f"禁止使用函数: {banned}()",
                        layer=4,
                    )

    # =================================================
    # Layer 5: LIMIT 强制添加
    # =================================================

    def _ensure_limit(self, parsed: list) -> Tuple[list, bool]:
        """自动添加 LIMIT 限制"""
        stmt = parsed[0]

        limit_clause = stmt.find(exp.Limit)
        if limit_clause:
            current = int(limit_clause.expression.name) if limit_clause.expression else 0
            if current > self.max_limit:
                raise ValidationError(
                    f"LIMIT {current} 超过最大值 {self.max_limit}",
                    layer=5,
                )
            return parsed, False

        stmt = stmt.limit(self.max_limit)
        logger.info(f"[Validator] 自动添加 LIMIT {self.max_limit}")
        return [stmt], True

    # =================================================
    # 主入口
    # =================================================

    def validate(self, sql: str) -> Tuple[str, Set[str], exp.Select]:
        """
        完整校验流程。

        参数:
            sql: 原始 SQL 字符串

        返回:
            (经过修改后安全的 SQL, 引用的表名集合, AST Select 节点)

        异常:
            ValidationError: 校验失败
        """

        try:
            parsed = sqlglot.parse(sql, read="postgres")
        except Exception as e:
            raise ValidationError(f"SQL 解析失败: {e}", layer=0)

        if not parsed:
            raise ValidationError("SQL 解析结果为空", layer=0)

        # — Layer 1: 类型校验 —
        self._check_statement_type(parsed)

        # — Layer 2: 表名白名单 —
        table_names = self._extract_table_names(parsed)
        self._check_table_allowlist(table_names)

        # — Layer 3: 敏感列拒绝 —
        self._check_sensitive_columns(parsed, table_names)

        # — Layer 4: 禁止函数 —
        self._check_banned_functions(parsed)

        # — Layer 5: LIMIT —
        parsed, _ = self._ensure_limit(parsed)

        # — 重新生成 SQL (标准化) —
        safe_sql = parsed[0].sql(dialect="postgres")
        logger.info(f"[Validator] 校验通过: {safe_sql[:120]}")

        return safe_sql, table_names, parsed[0]


# 全局单例
sql_validator = SQLValidator()

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


# ValidationError.reason 低基数枚举（STOP C 终态分类）：
#   terminal（安全/策略拒绝，禁止携带反馈重试）：
#     non_select / multi_statement / select_into / lock_clause /
#     write_in_subquery / table_forbidden / schema_forbidden /
#     column_forbidden / star_projection / dangerous_function
#   retryable（语法/schema 类，允许有限次带反馈重新生成）：
#     parse_error / empty_sql / alias_undefined / limit_exceeded
TERMINAL_DENY_REASONS = frozenset({
    "non_select", "multi_statement", "select_into", "lock_clause",
    "write_in_subquery", "table_forbidden", "schema_forbidden",
    "column_forbidden", "star_projection", "dangerous_function",
})

# 2026-10-06 拍板：全函数正向白名单（G-17 由 pg_* 族扩大到全部函数）。
# 初始集 = 黄金集 84 条 gold SQL 函数并集 + 常用只读标量/聚合函数；
# 键覆盖三种遍历口径：SQL 文本名（Anonymous.name）、sqlglot 类名
# （type(Func).__name__）、方言渲染名（Func.sql_name()）。
# 运维追加走 schema 配置 allowed_functions（默认空，回归锁
# test_allowed_functions_default_empty 不受影响）。
POSITIVE_FUNCTION_ALLOWLIST: frozenset = frozenset({
    # 聚合
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    "STRING_AGG", "ARRAY_AGG", "BOOL_AND", "BOOL_OR",
    # 标量 / 条件
    "ABS", "CEIL", "CEILING", "FLOOR", "ROUND",
    "GREATEST", "LEAST", "COALESCE", "NULLIF", "CAST",
    "LENGTH", "LOWER", "UPPER", "TRIM", "LTRIM", "RTRIM",
    "SUBSTRING", "SUBSTR", "CONCAT", "REPLACE", "SPLIT_PART", "POSITION",
    # 时间
    "CURRENT_DATE", "CURRENTDATE", "CURRENT_TIMESTAMP", "CURRENTTIMESTAMP",
    "NOW", "DATE", "DATE_PART", "DATE_TRUNC", "TIMESTAMP_TRUNC",
    "EXTRACT", "TO_CHAR", "AGE", "TIMESTOSTR", "TIME_TO_STR",
    # 存在性（exp.Exists 不在 Func 遍历面，防御性列入）
    "EXISTS",
})


class ValidationError(Exception):
    """校验失败异常，包含友好错误消息与低基数原因码。

    reason（STOP C）：供上层区分「安全拒绝（终态，不重试）」与
    「语法/schema 错误（可有限重试重新生成）」；缺省空串兼容旧调用。
    """
    def __init__(self, message: str, layer: int = 0, reason: str = ""):
        self.layer = layer
        self.reason = reason
        super().__init__(message)

    @property
    def is_terminal_deny(self) -> bool:
        return self.reason in TERMINAL_DENY_REASONS


# PostgreSQL 保留字（子集）—— 作为 schema/table 名时必须加引号。
# fix f19：LLM 可能生成未加引号的 `order.order_items`，sqlglot 能解析但
# 重序列化后 PG 报语法错误（实测 MiniMax 切换后暴露）。白名单校验
# 基于 AST 名称不受引号影响，这里在输出前统一补引号即安全兜底。
_PG_RESERVED_IDENTIFIERS = {
    "order", "group", "user", "table", "select", "where", "from", "to",
    "default", "check", "primary", "limit", "offset", "case", "when",
    "then", "else", "end", "and", "or", "not", "null", "grant",
    "references", "collate", "foreign", "index", "with",
}


class SQLValidator:
    """SQL 安全校验器"""

    def __init__(self):
        self.allowed_tables = schema_loader.allowed_tables
        self.allowed_schemas = schema_loader.allowed_schemas
        self.sensitive_columns = schema_loader.sensitive_columns
        self.banned_functions = schema_loader.banned_functions
        self.allowed_functions = schema_loader.allowed_functions
        self.max_limit = schema_loader.max_limit

    # =================================================
    # Layer 1: SQL 类型校验 — 必须为 SELECT
    # =================================================

    def _check_statement_type(self, parsed: list) -> None:
        """检查 AST 顶层语句类型，只允许单个 SELECT"""
        if not parsed:
            raise ValidationError("SQL 语句为空", layer=1, reason="empty_sql")

        if len(parsed) > 1:
            statements = [type(s).__name__ for s in parsed]
            raise ValidationError(
                f"禁止多条语句，检测到 {len(parsed)} 条: {statements}",
                layer=1, reason="multi_statement",
            )

        stmt = parsed[0]

        if not isinstance(stmt, exp.Select):
            stmt_type = type(stmt).__name__
            raise ValidationError(
                f"只允许 SELECT 查询，检测到 {stmt_type}",
                layer=1, reason="non_select",
            )

        # 显式拒绝 SELECT INTO / FOR UPDATE / FOR SHARE（建议项 2026-09-21）：
        # 以前只靠只读事务兜底，现在 Layer 1 直接拒绝——INTO 会建表落盘，
        # 锁子句会持有行锁直到事务结束，都不该进到执行层。
        if stmt.args.get("into"):
            raise ValidationError("禁止 SELECT INTO（会创建表/写文件）", layer=1, reason="select_into")
        locks = stmt.args.get("locks") or []
        if locks:
            raise ValidationError("禁止 FOR UPDATE / FOR SHARE 锁子句", layer=1, reason="lock_clause")

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
                    layer=1, reason="write_in_subquery",
                )

    # =================================================
    # Layer 2: 表名白名单
    # =================================================

    def _quote_reserved_identifiers(self, parsed: list) -> None:
        """fix f19：给保留字 schema/table 名补双引号（就地修改 AST）。

        与项目手写 SQL 约定一致（daily_report/data_fetcher 均用
        `"order"."orders"`）；引号不影响白名单校验（基于 AST 名称）。
        """
        stmt = parsed[0]
        for table in stmt.find_all(exp.Table):
            for part in ("catalog", "db", "this"):
                ident = table.args.get(part)
                if ident is not None and isinstance(ident, exp.Identifier):
                    if ident.name.lower() in _PG_RESERVED_IDENTIFIERS and not ident.args.get("quoted"):
                        ident.set("quoted", True)

    def _check_alias_defined(self, parsed: list) -> None:
        """fix f21：列引用的表别名/表名必须在 FROM/JOIN 中已定义。

        LLM 可能生成引用了未 JOIN 表的别名（如用 `oi.order_id` 却未
        JOIN order_items AS oi），PG 报「对于表 oi，丢失 FROM 子句」；
        而 syntax_error 不可重试会直接兜底（MiniMax 切换后实测暴露）。
        在 validator 层拦截 → ValidationError → 走既有重试链路带错误
        反馈重新生成，不依赖 LLM 承诺，符合 6 层硬校验设计。
        """
        stmt = parsed[0]
        defined = set()
        for table in stmt.find_all(exp.Table):
            defined.add((table.alias or table.name).lower())
        for sq in stmt.find_all(exp.Subquery):
            if sq.alias:
                defined.add(sq.alias.lower())
        for cte in stmt.find_all(exp.CTE):
            if cte.alias:
                defined.add(cte.alias.lower())
        for col in stmt.find_all(exp.Column):
            if col.table and col.table.lower() not in defined:
                raise ValidationError(
                    f"列 '{col.table}.{col.name}' 引用的表别名 '{col.table}' "
                    f"未在 FROM/JOIN 中定义（已定义: {sorted(defined)}）",
                    layer=2, reason="alias_undefined",
                )

    def _check_column_allowlist(self, parsed: list, table_names: Set[str]) -> None:
        """校验列确实存在于数据字典，阻断常见列名幻觉。

        表名白名单只能证明「能访问哪张表」，不能证明 LLM 写出的列名
        存在。这里使用同一份 schema_loader 数据字典做确定性校验，避免
        把 `order_items.price` 猜成常见但不存在的 `unit_price`。
        CTE 输出列和 SELECT 别名由 SQL 引擎解析，保留给数据库/重试链处理。
        """
        stmt = parsed[0]
        cte_names = {cte.alias.lower() for cte in stmt.find_all(exp.CTE)}
        alias_to_table: dict[str, str] = {}
        for table in stmt.find_all(exp.Table):
            real = table.name.lower()
            db = (table.db or "").lower()
            qualified = f"{db}.{real}" if db else real
            alias_to_table[table.alias_or_name.lower()] = qualified
            alias_to_table[real] = qualified

        select_aliases = {
            expression.alias.lower()
            for select in stmt.find_all(exp.Select)
            for expression in select.expressions
            if getattr(expression, "alias", "")
        }

        visible_columns = {
            table: set(schema_loader.get_browse_columns(table))
            for table in table_names
        }
        for column in stmt.find_all(exp.Column):
            name = column.name.lower()
            qualifier = column.table.lower() if column.table else ""
            if not name or name == "*":
                continue
            if not qualifier and name in select_aliases:
                continue
            if qualifier in cte_names:
                continue

            if qualifier:
                table_name = alias_to_table.get(qualifier)
                if table_name is None:
                    # _check_alias_defined 已给出更准确的错误。
                    continue
                if name not in visible_columns.get(table_name, set()):
                    available = sorted(visible_columns.get(table_name, set()))
                    raise ValidationError(
                        f"列 '{qualifier}.{name}' 不存在于数据字典中的表 '{table_name}'；"
                        f"可用列: {', '.join(available)}",
                        layer=3, reason="column_undefined",
                    )
                continue

            matches = [
                table for table, columns in visible_columns.items()
                if name in columns
            ]
            if not matches and name not in select_aliases:
                raise ValidationError(
                    f"未找到列 '{name}'（已选表: {sorted(table_names)}）",
                    layer=3, reason="column_undefined",
                )

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
                # 裸名：归一化到唯一 schema-qualified key——列字典按限定名
                # 组织，裸名直传会让列校验查不到列（向后兼容只做一半的缺陷）；
                # 命中多个 schema 时保持裸名，交由白名单/列校验按原口径拒绝。
                qname = self._resolve_bare_table(name) or name
            tables.add(qname)
        return tables

    def _resolve_bare_table(self, bare: str) -> str | None:
        """裸表名 → 唯一限定名；无匹配或多义返回 None。"""
        matches = [
            q for q in schema_loader.allowed_tables
            if q.split(".", 1)[-1] == bare
        ]
        return matches[0] if len(matches) == 1 else None

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
                        layer=2, reason="schema_forbidden",
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
                layer=2, reason="table_forbidden",
            )

    # =================================================
    # Layer 3: 列级安全 — 敏感列直接拒绝
    # =================================================

    def _check_sensitive_columns(self, parsed: list, table_names: Set[str]) -> None:
        """检查 SELECT / WHERE 中是否引用了敏感列。

        安全修复：
        ① 配置为三段式 schema.table.column 时表名取 parts[-2]（旧实现取
           parts[0] 拿到的是 schema 名，永远匹配不上表别名）；
        ② 列引用先按别名解析到真实表名再比对（`c.phone` 中 c 是
           customers 的别名时旧实现永不匹配）。
        """
        if not self.sensitive_columns:
            return

        stmt = parsed[0]
        # 别名 → 真实表名映射（无别名时 alias_or_name 即表名本身）
        alias_to_table = {}
        for table in stmt.find_all(exp.Table):
            real = table.name.lower()
            alias_to_table[table.alias_or_name.lower()] = real
            alias_to_table[real] = real

        for column in stmt.find_all(exp.Column):
            col_name = column.name.lower()
            raw_table = column.table.lower() if column.table else ""
            resolved_table = alias_to_table.get(raw_table, raw_table)

            for sensitive_ref in self.sensitive_columns:
                sens_parts = sensitive_ref.lower().split(".")
                sens_col = sens_parts[-1]
                sens_table = sens_parts[-2] if len(sens_parts) > 1 else ""

                if col_name != sens_col:
                    continue
                # 未限定表名的列引用（SELECT phone FROM ...）无法证明
                # 不属于敏感表 → fail-closed 直接拒绝
                if not sens_table or not raw_table or resolved_table == sens_table:
                    full_ref = f"{raw_table}.{col_name}" if raw_table else col_name
                raise ValidationError(
                    f"禁止查询敏感列: '{full_ref}' (敏感列: {sensitive_ref})",
                    layer=3, reason="column_forbidden",
                )

    # =================================================
    # Layer 3+: SELECT * 泄露防护 — 敏感表上禁止星号投影
    # =================================================

    def _check_star_projection(self, parsed: list, table_names: Set[str]) -> None:
        """敏感列表上的 `*` / `t.*` 直接拒绝（fail-closed）。

        Layer 3 只能拦截显式列名；`SELECT *` 会把敏感列一并带出而不触发
        任何列名检查。这里在查询引用了含敏感列的表时，拒绝一切星号投影，
        迫使 LLM 在重试链路中显式列出列名。COUNT(*) 等聚合内的 Star
        不是投影，不受影响。
        """
        if not self.sensitive_columns:
            return

        # 敏感列配置（schema.table.column）→ 敏感表名集合
        sensitive_tables = {
            ".".join(ref.lower().split(".")[:2]) for ref in self.sensitive_columns
        }
        # 引用表可能是裸名，与敏感表做双向匹配
        hits_sensitive = any(
            t in sensitive_tables
            or any(st.endswith(f".{t}") for st in sensitive_tables)
            for t in table_names
        )
        if not hits_sensitive:
            return

        for select in parsed[0].find_all(exp.Select):
            for expr in select.expressions:
                if isinstance(expr, exp.Star) or (
                    isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star)
                ):
                    raise ValidationError(
                        "查询涉及含敏感列的表，禁止使用 SELECT *，请显式列出所需列名",
                        layer=3, reason="star_projection",
                    )

    # =================================================
    # Layer 4: 禁止函数检查
    # =================================================

    def _check_banned_functions(self, parsed: list) -> None:
        """检查 SQL 中是否包含禁止/未批准的函数（G-17 fail-closed）。"""
        stmt = parsed[0]
        for func in stmt.find_all(exp.Anonymous):
            func_name = func.name.upper() if func.name else ""
            self._deny_dangerous_function(func_name, func)
        for func in stmt.find_all(exp.Func):
            func_name = type(func).__name__.upper()
            self._deny_dangerous_function(func_name, func)
            if hasattr(func, "sql_name"):
                self._deny_dangerous_function(func.sql_name().upper(), func)
        self._check_pg_catalog_qualified_calls(stmt)

    def _deny_dangerous_function(self, func_name: str, func: exp.Expression) -> None:
        """单一判定出口：显式黑名单 + 全函数正向白名单 fail-closed（2026-10-06 拍板）。

        G-17 原口径仅对 pg_* 族 fail-closed；渗透复验证实非 pg 前缀的危险
        函数同样可穿透，判定面扩大到白名单外的全部函数。reason 码沿用
        unapproved_system_function（存量渗透回归锁此值）。pg_catalog 限定名
        挂在 exp.Dot 父节点上（func 节点自身渲染会丢限定符），单独遍历 Dot
        兜住 `pg_catalog.xxx()` 逃逸形态。
        """
        if not func_name:
            return
        if func_name in self.banned_functions:
            raise ValidationError(
                f"禁止使用函数: {func_name}()",
                layer=4, reason="dangerous_function",
            )
        if func_name in self.allowed_functions or func_name in POSITIVE_FUNCTION_ALLOWLIST:
            return
        raise ValidationError(
            f"未批准的函数: {func_name}()（不在正向白名单）",
            layer=4, reason="unapproved_system_function",
        )

    def _check_pg_catalog_qualified_calls(self, stmt: exp.Expression) -> None:
        """pg_catalog/information_schema 限定函数调用一律拒绝（G-16/G-17）。"""
        for dot in stmt.find_all(exp.Dot):
            try:
                rendered = dot.sql().upper()
            except Exception:
                continue
            if "PG_CATALOG." in rendered or "INFORMATION_SCHEMA." in rendered:
                raise ValidationError(
                    f"禁止引用系统 schema 限定调用: {dot.sql()}",
                    layer=4, reason="unapproved_system_function",
                )

    # =================================================
    # Layer 5: LIMIT 强制添加
    # =================================================

    def _ensure_limit(self, parsed: list) -> Tuple[list, bool]:
        """自动添加 LIMIT 限制。

        只检查顶层 SELECT 自身的 limit 子句（stmt.args["limit"]），
        不能用 stmt.find(exp.Limit)——那会遍历整棵 AST，子查询/CTE
        里的 LIMIT 会被误当成外层限制，导致外层查询无界扫描。
        """
        stmt = parsed[0]

        limit_clause = stmt.args.get("limit")
        if limit_clause is not None:
            expr = limit_clause.expression
            if isinstance(expr, exp.Literal) and expr.is_int:
                current = int(expr.name)
                if current > self.max_limit:
                    raise ValidationError(
                        f"LIMIT {current} 超过最大值 {self.max_limit}",
                        layer=5, reason="limit_exceeded",
                    )
                return parsed, False
            # 安全修复：LIMIT ALL / 非数字字面量（表达式、参数等）解析不出
            # 数值 → 旧实现按 0 处理原样放行，max_limit 失效。强制覆写。
            stmt = stmt.limit(self.max_limit)
            logger.info("[Validator] LIMIT 非数字字面量（如 LIMIT ALL），强制覆写为 max_limit")
            return [stmt], True

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
            raise ValidationError(f"SQL 解析失败: {e}", layer=0, reason="parse_error")

        if not parsed:
            raise ValidationError("SQL 解析结果为空", layer=0, reason="parse_error")

        # — Layer 1: 类型校验 —
        self._check_statement_type(parsed)

        # — fix f19: 保留字标识符补引号（在白名单校验前，不影响 AST 名称）—
        self._quote_reserved_identifiers(parsed)

        # — Layer 2: 表名白名单 —
        table_names = self._extract_table_names(parsed)
        self._check_table_allowlist(table_names)

        # — fix f21: 列别名引用必须有 FROM/JOIN 定义 —
        self._check_alias_defined(parsed)

        # — 列名数据字典校验：拒绝 LLM 猜出的不存在列 —
        self._check_column_allowlist(parsed, table_names)

        # — Layer 3: 敏感列拒绝 —
        self._check_sensitive_columns(parsed, table_names)

        # — Layer 3+: SELECT * 敏感列泄露防护 —
        self._check_star_projection(parsed, table_names)

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

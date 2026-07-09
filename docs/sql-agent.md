# SQL 安全 Agent

> 6 层硬校验，无一依赖 LLM 承诺。涉及 SQL 生成、校验、行级安全、只读执行。

## 1. 总览

```
sql_agent/
├── sql_agent.py        # 主编排器
├── router.py           # 选表 (LLM)
├── sql_generator.py    # 生成 SQL (LLM)
├── sql_validator.py    # 6 层硬校验
├── row_security.py     # 行级安全（参数化注入）
├── executor.py         # 只读事务执行
├── schema_loader.py    # Schema 加载 + 快查结构
└── data/schema_config.py  # 唯一真相源（SSOT）
```

**设计原则**：安全规则由硬编码配置决定，不依赖 LLM 的 prompt 指令或"自觉"。

## 2. 流程

```
SQLAgent.ask(question, current_user_id)
  1. select_tables(question)              # router.py: LLM 选表
  2. generate_sql(question, table_names)  # sql_generator.py: LLM 生成 SQL
  3. sql_validator.validate(sql)          # 6 层硬校验
  4. inject_row_filter(sql, user_context) # row_security.py: 参数化行级条件
  5. execute_sql(sql, db_config, params)  # executor.py: 只读事务 + 参数化执行
  → Markdown 表格结果
```

如果 step 3 失败（`ValidationError`），重试 `max_retries` 次，把错误信息注入 prompt 让 LLM 自我修正。

## 3. 6 层校验 (`sql_validator.py`)

| Layer | 检查 | 拒绝条件示例 |
|---|---|---|
| 1. Statement type | `exp.Select` 单语句 | `INSERT` / `UPDATE` / `DELETE` |
| 2. No write in subqueries | 子查询也不允许写 | `SELECT * FROM (INSERT INTO ...) ...` |
| 3. Sensitive columns | `users.phone` 等黑名单 | 直接抛 `ValidationError`（硬拦截） |
| 4. Table allowlist | 仅允许 `SCHEMA_CONFIG` 内的表 | `users.id` OK，`unknown_table` 拒绝 |
| 5. Banned functions | `sleep` / `pg_read_file` / `dblink` | 函数名不区分大小写 |
| 6. LIMIT | 默认追加 `LIMIT 100` | 显式 `LIMIT 200` 拒绝 |

校验失败的 SQL **不会**到达 executor，**没有"软警告"概念**。

## 4. 行级安全 (`row_security.py`)

**关键安全机制**：对 `users` / `project_members` 等敏感表，自动追加 `WHERE table.column = %(name)s`。

**严格模式**：SQL 引用了 row-secured 表但缺少必需 param → 抛 `RowSecurityError`，**不再静默跳过**（旧版有"未登录用户能查所有人数据"的安全漏洞）。

**参数化**（修复硬编码 user_id 隐患）：
- 旧版：注入 `users.id = 101` 字面量到 SQL 文本
- 新版：注入 `users.id = %(users_id)s`，user_id 通过 psycopg2 params 通道传

```python
sql, params = inject_row_filter(sql, {"current_user_id": 101})
# sql: "SELECT * FROM users WHERE users.id = %(users_id)s"
# params: {"users_id": 101}
```

**严格模式触发**：
```python
inject_row_filter("SELECT * FROM users", {})  # 缺 current_user_id
# → RowSecurityError: 行级安全要求参数 ['current_user_id'] 但缺失
```

## 5. 只读执行 (`executor.py`)

**修复 autocommit + READ ONLY 失效问题**（P0-4 修复）：

- 旧版：`conn.set_session(readonly=True, autocommit=True)` — autocommit 模式下 READ ONLY **不生效**
- 新版：显式 `BEGIN` + `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout`

```python
conn = psycopg2.connect(**db_config)
conn.autocommit = False
conn.set_session(readonly=True, readonly_level="transaction")
with conn.cursor(...) as cur:
    cur.execute("BEGIN")
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SET LOCAL statement_timeout = %s", (ms,))
    cur.execute(sql, params)  # 参数化
    ...
```

**拦截非只读操作**：检测到 "cannot execute INSERT in a read-only transaction" 等异常时返回安全错误信息。

## 6. Schema 唯一真相源 (`data/schema_config.py`)

所有安全策略集中在此文件，由 `schema_loader.py` 加载：

```python
SCHEMA_CONFIG = {
    "tables": {                          # 表结构
        "users": {"columns": {...}, "description": "..."},
        ...
    },
    "sensitive_columns": ["users.phone"], # Layer 3 黑名单
    "masked_columns": {"users.email": (2, 1)},  # 脱敏规则
    "row_security": {                    # Layer 4 行级安全
        "users": {"column": "id", "param": "current_user_id"},
        "project_members": {"column": "user_id", "param": "current_user_id"},
    },
    "max_limit": 100,                    # Layer 6 LIMIT
    "query_timeout": 5.0,                # 超时秒数
    "banned_functions": ["sleep", "pg_read_file", ...],  # Layer 5
}
```

**修改安全策略只需改这一个文件**，业务代码不动。

**注意**：脱敏在 executor Python 层执行（`mask_value` 函数），不依赖 DB 列级权限 — 双重保险（即使 LLM 绕过校验返回了 phone，应用层也会脱敏后再返回给用户）。

## 7. 当前 schema (4 张表)

| 表 | 描述 | 敏感列 | 脱敏列 | 行级安全 |
|---|---|---|---|---|
| `users` | 用户基本信息 | `phone` | `email` | `id = current_user_id` |
| `departments` | 部门组织 | — | — | — |
| `projects` | 项目信息 | — | — | — |
| `project_members` | 项目成员关联 | — | — | `user_id = current_user_id` |

## 8. 关键函数

| 函数 | 作用 |
|---|---|
| `select_tables(question)` | LLM 选表（router.py），prompt 包含表描述 |
| `generate_sql(question, table_names)` | LLM 生成 SQL（sql_generator.py），prompt 包含表结构 |
| `sql_validator.validate(sql)` | 6 层硬校验，返回 `(safe_sql, reason, ...)` |
| `inject_row_filter(sql, user_context)` | 参数化行级安全注入，返回 `(sql, params)` |
| `execute_sql(sql, db_config, params)` | 只读事务执行，返回 Markdown 表格 |
| `schema_loader.get_row_security(table)` | 获取表的行级安全配置 |

## 9. 错误处理

```python
try:
    sql = generate_sql(...)
    safe_sql, _, _ = sql_validator.validate(sql)
    safe_sql, rs_params = inject_row_filter(safe_sql, user_context)
    result = execute_sql(safe_sql, self.db_config, params=rs_params)
    return result
except ValidationError as e:
    # SQL 校验失败 — 注入错误到 prompt 让 LLM 自我修正
    question += f"\n(之前生成的 SQL 因为 {e} 被拒绝)"
except RowSecurityError as e:
    # 行级安全违反 — 拒绝查询，返回用户友好错误
    return f"访问控制错误: {e}"
```

## 10. 修改指南

- **加新表**：在 `schema_config.py:SCHEMA_CONFIG.tables` 加表定义
- **加敏感列**：在 `schema_config.py:SCHEMA_CONFIG.sensitive_columns` 追加
- **改行级安全策略**：在 `schema_config.py:SCHEMA_CONFIG.row_security` 改或追加
- **改超时**：`schema_config.py:query_timeout` 或 `.env` 的 `LLM_REQUEST_TIMEOUT`
- **禁用某函数**：在 `banned_functions` 追加
- **改脱敏规则**：在 `masked_columns` 改 `(prefix_len, suffix_len)`

## 11. 已知问题 / 待优化

- `init_sql_agent()` 工厂函数已删除（之前是双轨入口之一）
- `SQLAgentError` 已删除（空异常类）
- `row_security` 参数化后**无回归测试覆盖**端到端（`test_row_security.py` 覆盖单元逻辑）
- 大量 chunk 文档查询时 BM25 重建慢（一次性内存索引，无持久化）
- `psycopg2` 是同步库，`executor.execute_sql` 在 async 路径（FastAPI handler）会阻塞事件循环

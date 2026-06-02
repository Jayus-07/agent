"""
schema_config.py — 数据库 schema 配置、白名单、敏感列、行级安全策略

这是整个 SQL Agent 安全体系的"唯一真相来源"（single source of truth）。
所有安全策略集中在此文件，由 schema_loader.py 加载。
修改安全策略只需改这一个文件，不需要改任何业务逻辑代码。

设计原则：安全规则由硬编码配置决定，不依赖 LLM 的 prompt 指令或"自觉"。
"""

from typing import Dict, Any

# =====================================================
# 数据库 Schema 定义 (PostgreSQL)
# =====================================================
# 以下配置被 schema_loader.py 在启动时一次性加载，预计算为快速查找结构：
#   - allowed_tables（表名集合）
#   - sensitive_columns（table.column 集合）
#   - masked_columns（{table.column: (prefix_len, suffix_len)} 字典）
#   - row_security（{table: {column, param}} 字典）
#   - banned_functions（大写函数名集合）
# 这些预计算结构在 validate、execute 等热路径上直接使用，O(1) 查找。

SCHEMA_CONFIG: Dict[str, Any] = {

    # ── 表定义 ─────────────────────────────────────────
    # 每个表包含：
    #   columns:     {列名: 描述}  — 描述会喂给 LLM 帮助它生成正确的 SQL
    #   description: str           — 表的业务说明，用于 router.py 选表时的提示词
    #
    # 注意：敏感列（如 phone）虽然在 columns 中定义，但 get_table_info()
    # 生成 LLM 提示词时会自动排除这些列，从源头防止 LLM 知道它们的存在。

    "tables": {
        "users": {
            "columns": {
                "id":         "用户ID (SERIAL PRIMARY KEY)",
                "name":       "姓名 (VARCHAR)",
                "email":      "邮箱地址 (VARCHAR)",
                "phone":      "手机号码 (VARCHAR, 敏感信息)",
                "dept_id":    "所属部门ID (INTEGER, FK → departments.id)",
                "role":       "角色 (VARCHAR, 如 admin/manager/staff)",
                "created_at": "入职日期 (DATE)",
            },
            "description": "用户表，存储所有员工的基本信息、联系方式和部门归属。",
        },
        "departments": {
            "columns": {
                "id":        "部门ID (SERIAL PRIMARY KEY)",
                "name":      "部门名称 (VARCHAR, 注意列名是 name 不是 department_name)",
                "parent_id": "上级部门ID (INTEGER, 自引用)",
            },
            "description": "部门组织架构表，列名 departments.name 表示部门名称。",
        },
        "projects": {
            "columns": {
                "id":          "项目ID (SERIAL PRIMARY KEY)",
                "name":        "项目名称 (VARCHAR, 注意列名是 name 不是 project_name)",
                "owner_id":    "项目负责人ID (INTEGER, FK → users.id)",
                "budget":      "预算金额 (NUMERIC, 万元)",
                "status":      "项目状态 (VARCHAR, planning/active/completed)",
                "start_date":  "开始日期 (DATE)",
                "end_date":    "结束日期 (DATE)",
            },
            "description": "项目表，记录公司所有项目。列名 projects.name 表示项目名称。",
        },
        "project_members": {
            "columns": {
                "project_id": "项目ID (INTEGER, FK → projects.id)",
                "user_id":    "用户ID (INTEGER, FK → users.id)",
                "role":       "项目内角色 (VARCHAR, 如 lead/member/reviewer)",
            },
            "description": "项目成员关联表，记录每个项目有哪些人参与。",
        },
    },

    # ── 敏感列 ─────────────────────────────────────────
    # 定义格式: ["table.column", ...]
    #
    # 规则：任何 SQL 查询如果引用了此列表中的列，sql_validator.py 的 Layer 3
    # 会直接抛出 ValidationError，拒绝执行。这不是警告，是硬拦截。
    #
    # 典型场景：phone 是个人隐私数据，任何 SELECT 都不允许查。
    # 即使用户问"我的手机号是多少"，也直接拒绝，不给 LLM 绕过的机会。

    "sensitive_columns": [
        "users.phone",
    ],

    # ── 脱敏列 ─────────────────────────────────────────
    # 定义格式: {"table.column": (prefix_len, suffix_len)}
    #
    # 规则：这些列允许在 SQL 中查询，但 executor.py 返回结果前会对值做脱敏处理。
    # 脱敏算法：保留前 prefix_len 个字符 + "***" + 后 suffix_len 个字符
    #   例如 email="zhangwei@corp.com" + (2,1) → "zh***m"
    #
    # 脱敏在 executor.py 的 Python 层面执行，不依赖数据库的列级权限。
    # 这意味着即使 LLM 生成了 "SELECT email FROM users"，数据库返回了真实值，
    # 在返回给用户之前也会被打码。双重保险。

    "masked_columns": {
        "users.email": (2, 1),
    },

    # ── 行级安全策略 ────────────────────────────────────
    # 定义格式: {table: {column, param}}
    #   column: 表中用于过滤的列名
    #   param:  参数名，由 SQLAgent.ask() 的 current_user_id 传入
    #
    # 规则：row_security.py 用 sqlglot 解析 SQL 的 AST，在 WHERE 子句中
    # 自动注入 "table.column = current_user_id" 条件。
    #
    # 示例：用户 103 问"我参与的项目"→ 生成的 WHERE 自动包含
    #   users.id = 103 AND project_members.user_id = 103
    #
    # 如果 SQL 已有 WHERE，新条件用 AND 追加；如果没有 WHERE，创建 WHERE。
    # 如果 user_id 为 None（未登录），则不注入任何条件，返回全部数据。
    #
    # 设计要点：
    #   - users 用 id 过滤：当前用户只能看自己的信息
    #   - project_members 用 user_id 过滤：只显示自己参与的项目
    #   - departments 和 projects 没有策略：所有人可见

    "row_security": {
        "users": {
            "column": "id",
            "param":  "current_user_id",
        },
        "project_members": {
            "column": "user_id",
            "param":  "current_user_id",
        },
    },

    # ── 查询限制 ────────────────────────────────────────
    # max_limit: 如果 LLM 生成的 SQL 没有 LIMIT 子句，sql_validator.py 的
    #   Layer 5 会自动追加 LIMIT 100。如果显式写了更大的 LIMIT（如 LIMIT 200），
    #   则直接拒绝。这是防止全表扫描、消耗数据库资源的最后一道防线。
    # query_timeout: executor.py 在建立连接后设置 statement_timeout，超时自动中断。
    #   这是数据库层面的保护，即使应用层死循环也兜得住。

    "max_limit": 100,
    "query_timeout": 5.0,

    # ── 禁用函数黑名单 ──────────────────────────────────
    # sql_validator.py 的 Layer 4 检查：SQL 中任何函数调用如果在名单中（不区分大小写），
    # 直接拒绝。这些函数之所以被禁：
    #   sleep / pg_sleep / benchmark → 休眠攻击，耗尽连接池
    #   lo_import / lo_export       → 大对象操作，可能读写服务器文件系统
    #   pg_read_file / pg_read_binary_file   → 读取服务器文件
    #   pg_write_file / pg_write_binary_file → 写入服务器文件（更危险）
    #   dblink / dblink_exec        → 跨库访问，绕过本实例的权限控制

    "banned_functions": [
        "sleep", "pg_sleep", "benchmark",
        "lo_import", "lo_export",
        "pg_read_file", "pg_read_binary_file",
        "pg_write_file", "pg_write_binary_file",
        "dblink", "dblink_exec",
    ],
}
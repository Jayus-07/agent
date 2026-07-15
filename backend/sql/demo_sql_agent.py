"""
demo_sql_agent.py — SQL Agent 演示脚本 (PostgreSQL)

依赖:
  pip install sqlglot>=20.0.0 psycopg2-binary

环境变量 (可选):
  PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
  默认连接: localhost:5432/demo

运行:
  python demo_sql_agent.py
"""
import logging
import os
import sys
import io
import uuid
import textwrap

import psycopg2

from backend.config import DB_CONFIG
from dotenv import load_dotenv

from backend.shared.logger import logger

load_dotenv()   # 必须在读取环境变量之前调用

# — Windows 控制台 UTF-8 —
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"

# =====================================================
# PostgreSQL 连接配置
# =====================================================




def get_admin_conn():
    """获取管理员连接（用于创建/删除表）"""
    return psycopg2.connect(**DB_CONFIG)


# =====================================================
# Step 0: 创建演示数据库表
# =====================================================

_SCHEMA_PREFIX = f"sql_agent_demo_{uuid.uuid4().hex[:8]}"

def create_demo_schema():
    """在 PostgreSQL 中创建演示表并插入数据，返回 schema 前缀"""
    conn = get_admin_conn()
    conn.set_session(autocommit=True)
    cur = conn.cursor()

    tables = {
        "users":            _SCHEMA_PREFIX + "_users",
        "departments":      _SCHEMA_PREFIX + "_departments",
        "projects":         _SCHEMA_PREFIX + "_projects",
        "project_members":  _SCHEMA_PREFIX + "_project_members",
    }

    try:
        cur.execute(textwrap.dedent(f"""
            CREATE TABLE {tables['departments']} (
                id        SERIAL PRIMARY KEY,
                name      VARCHAR NOT NULL,
                parent_id INTEGER
            );

            CREATE TABLE {tables['users']} (
                id         SERIAL PRIMARY KEY,
                name       VARCHAR NOT NULL,
                email      VARCHAR,
                phone      VARCHAR,
                dept_id    INTEGER REFERENCES {tables['departments']}(id),
                role       VARCHAR,
                created_at DATE
            );

            CREATE TABLE {tables['projects']} (
                id         SERIAL PRIMARY KEY,
                name       VARCHAR NOT NULL,
                owner_id   INTEGER REFERENCES {tables['users']}(id),
                budget     NUMERIC,
                status     VARCHAR,
                start_date DATE,
                end_date   DATE
            );

            CREATE TABLE {tables['project_members']} (
                project_id INTEGER REFERENCES {tables['projects']}(id),
                user_id    INTEGER REFERENCES {tables['users']}(id),
                role       VARCHAR,
                PRIMARY KEY (project_id, user_id)
            );
        """))

        # — 插入部门 —
        cur.executemany(
            f"INSERT INTO {tables['departments']} (id,name,parent_id) VALUES (%s,%s,%s)",
            [(1, "技术部", None), (2, "产品部", None),
             (3, "市场部", None), (4, "数据分析组", 1)],
        )
        cur.execute(f"SELECT setval('{tables['departments']}_id_seq', 4)")

        # — 插入用户 —
        cur.executemany(
            f"INSERT INTO {tables['users']} (id,name,email,phone,dept_id,role,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [
                (101, "张伟", "zhangwei@corp.com", "13800138001", 1, "admin", "2020-03-15"),
                (102, "李娜", "lina@corp.com", "13900139002", 2, "manager", "2021-07-01"),
                (103, "王磊", "wanglei@corp.com", "13700137003", 1, "staff", "2022-01-10"),
                (104, "陈静", "chenjing@corp.com", "13600136004", 3, "staff", "2022-06-20"),
                (105, "刘洋", "liuyang@corp.com", "13500135005", 1, "staff", "2023-02-14"),
            ],
        )
        cur.execute(f"SELECT setval('{tables['users']}_id_seq', 105)")

        # — 插入项目 —
        cur.executemany(
            f"INSERT INTO {tables['projects']} (id,name,owner_id,budget,status,start_date,end_date) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [
                (1, "智能客服平台", 101, 500.0, "active", "2025-01-01", "2025-12-31"),
                (2, "数据中台建设", 103, 800.0, "active", "2025-03-01", "2026-06-30"),
                (3, "移动端改版", 102, 200.0, "completed", "2024-06-01", "2025-03-31"),
                (4, "BI 报表系统", 101, 350.0, "planning", "2025-09-01", "2026-08-31"),
            ],
        )
        cur.execute(f"SELECT setval('{tables['projects']}_id_seq', 4)")

        # — 插入项目成员 —
        cur.executemany(
            f"INSERT INTO {tables['project_members']} (project_id,user_id,role) VALUES (%s,%s,%s)",
            [
                (1, 101, "lead"), (1, 103, "developer"), (1, 105, "developer"),
                (2, 103, "lead"), (2, 101, "reviewer"), (2, 105, "developer"),
                (3, 102, "lead"), (3, 104, "member"),
                (4, 101, "lead"), (4, 104, "member"),
            ],
        )

        print(f"[OK] demo schema created (prefix={_SCHEMA_PREFIX})")
        return tables

    except Exception:
        destroy_demo_schema(tables)
        raise
    finally:
        cur.close()
        conn.close()


def destroy_demo_schema(tables: dict):
    """清理演示表"""
    try:
        conn = get_admin_conn()
        conn.set_session(autocommit=True)
        cur = conn.cursor()
        for tname in reversed(list(tables.values())):
            cur.execute(f"DROP TABLE IF EXISTS {tname} CASCADE")
        cur.close()
        conn.close()
        print(f"[OK] demo schema cleaned: {_SCHEMA_PREFIX}")
    except Exception as e:
        print(f"[WARN] cleanup failed: {e}")


# =====================================================
# 运行演示
# =====================================================

def run_demo():
    print("\n" + "=" * 60)
    print("  SQL Agent Demo (PostgreSQL)")
    print(f"  {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    print("=" * 60)

    tables = create_demo_schema()
    logger.info(f"tables: {(tables)} ")


    # — 临时修改 schema_config 以使用带前缀的表名 —
    from backend.sql.data import schema_config
    original_tables = dict(schema_config.SCHEMA_CONFIG["tables"])
    original_row_security = dict(schema_config.SCHEMA_CONFIG["row_security"])
    original_sensitive = list(schema_config.SCHEMA_CONFIG["sensitive_columns"])
    original_masked = dict(schema_config.SCHEMA_CONFIG["masked_columns"])

    # 替换为带前缀的表名
    new_tables = {}
    for logical_name, real_name in tables.items():
        new_tables[real_name] = dict(original_tables[logical_name])

    schema_config.SCHEMA_CONFIG["tables"] = new_tables

    # 更新敏感列/脱敏列/行级安全中的表名映射
    schema_config.SCHEMA_CONFIG["sensitive_columns"] = [
        c.replace("users.", tables["users"] + ".")
        for c in original_sensitive
    ]
    schema_config.SCHEMA_CONFIG["masked_columns"] = {
        k.replace("users.", tables["users"] + "."): v
        for k, v in original_masked.items()
    }
    schema_config.SCHEMA_CONFIG["row_security"] = {
        tables[k]: v for k, v in original_row_security.items() if k in tables
    }

    # — 重新加载 schema —
    from backend.sql.schema_loader import SchemaLoader
    import backend.sql.schema_loader as sl
    import backend.sql.sql_validator as sv
    sl.schema_loader = SchemaLoader()
    sv.sql_validator = sv.SQLValidator()

    try:
        from backend.sql.sql_agent import SQLAgent

        # — 使用只读连接配置 —
        readonly_config = dict(DB_CONFIG)

        agent = SQLAgent(db_config=readonly_config, max_retries=1)

        test_cases = [
            {
                "question": "查询用户表中的姓名和邮箱列",
                "user_id": None,
                "desc": "Basic: SELECT columns with masking",
            },
            {
                "question": "列出部门名称等于'技术部'的所有员工的姓名和入职日期",
                "user_id": None,
                "desc": "JOIN: filter by departments.name",
            },
            {
                "question": "按部门统计用户数，使用 GROUP BY 和 COUNT",
                "user_id": None,
                "desc": "GROUP BY: count users per dept",
            },
            {
                "question": "查询预算最高的3个项目，显示项目名称和预算",
                "user_id": None,
                "desc": "ORDER BY + LIMIT: top N query",
            },
            {
                "question": "用户'张伟'在 project_members 表中关联了哪些项目？关联 projects 表查项目名称",
                "user_id": None,
                "desc": "Multi-JOIN: person to projects",
            },
            {
                "question": "查询当前用户在 project_members 表中参与的所有项目",
                "user_id": 103,
                "desc": "Row Security: auto-inject user_id=103",
            },
            {
                "question": "把所有用户的邮箱改成 test@test.com",
                "user_id": None,
                "desc": "SECURITY: UPDATE blocked by Layer 1",
            },
            {
                "question": "查询所有用户的手机号",
                "user_id": None,
                "desc": "SECURITY: sensitive column blocked",
            },
        ]

        for i, tc in enumerate(test_cases, 1):
            print(f"\n{'─' * 60}")
            print(f"Test {i}/{len(test_cases)}: {tc['desc']}")
            print(f"Q: {tc['question']}")
            if tc['user_id']:
                print(f"   current_user_id: {tc['user_id']}")
            print()

            try:
                result = agent.ask(
                    question=tc["question"],
                    current_user_id=tc["user_id"],
                )
                print(result)
            except Exception as e:
                print(f"Error: {e}")

    finally:
        # — 恢复原始配置 —
        schema_config.SCHEMA_CONFIG["tables"] = original_tables
        schema_config.SCHEMA_CONFIG["sensitive_columns"] = original_sensitive
        schema_config.SCHEMA_CONFIG["masked_columns"] = original_masked
        schema_config.SCHEMA_CONFIG["row_security"] = original_row_security
        sl.schema_loader = SchemaLoader()
        sv.sql_validator = sv.SQLValidator()

        destroy_demo_schema(tables)


if __name__ == "__main__":
    try:
        run_demo()
    except psycopg2.OperationalError as e:
        print(f"\n[ERROR] 无法连接 PostgreSQL: {e}")
        print(f"请检查连接配置: {DB_CONFIG}")
        print("\n提示: 可通过环境变量设置连接参数")

        sys.exit(1)

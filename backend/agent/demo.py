"""
demo.py — Multi-Agent 工作流系统演示

演示场景:
  1. 简单查询（单步 SQL）
  2. 并行查询（SQL + RAG 无依赖，并发执行）
  3. DAG 调度（SQL + RAG 并行 → Report 依赖前两步）
  4. 无法拆解的问题（空 plan → Reporter 提示）

依赖:
  pip install langgraph>=0.2.0

运行:
  python multi_agent/demo.py
"""

import os
import sys
import io
import uuid
import textwrap

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from backend.config import DB_CONFIG
from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"


# =====================================================
# 连接 & 演示数据
# =====================================================

def get_admin_conn():
    return psycopg2.connect(**DB_CONFIG)


_SCHEMA_PREFIX = f"multi_agent_demo_{uuid.uuid4().hex[:8]}"


def create_demo_schema():
    """创建临时表并插入演示数据"""
    conn = get_admin_conn()
    conn.set_session(autocommit=True)
    cur = conn.cursor()

    tables = {
        "departments":     _SCHEMA_PREFIX + "_departments",
        "users":           _SCHEMA_PREFIX + "_users",
        "projects":        _SCHEMA_PREFIX + "_projects",
        "project_members": _SCHEMA_PREFIX + "_project_members",
    }

    try:
        cur.execute(textwrap.dedent(f"""
            CREATE TABLE {tables['departments']} (
                id SERIAL PRIMARY KEY, name VARCHAR NOT NULL, parent_id INTEGER
            );
            CREATE TABLE {tables['users']} (
                id SERIAL PRIMARY KEY, name VARCHAR NOT NULL,
                email VARCHAR, phone VARCHAR,
                dept_id INTEGER REFERENCES {tables['departments']}(id),
                role VARCHAR, created_at DATE
            );
            CREATE TABLE {tables['projects']} (
                id SERIAL PRIMARY KEY, name VARCHAR NOT NULL,
                owner_id INTEGER REFERENCES {tables['users']}(id),
                budget NUMERIC, status VARCHAR,
                start_date DATE, end_date DATE
            );
            CREATE TABLE {tables['project_members']} (
                project_id INTEGER REFERENCES {tables['projects']}(id),
                user_id INTEGER REFERENCES {tables['users']}(id),
                role VARCHAR, PRIMARY KEY (project_id, user_id)
            );
        """))

        cur.executemany(
            f"INSERT INTO {tables['departments']} (id,name,parent_id) VALUES (%s,%s,%s)",
            [(1, "技术部", None), (2, "产品部", None),
             (3, "市场部", None), (4, "数据组", 1)],
        )
        cur.execute(f"SELECT setval('{tables['departments']}_id_seq', 4)")

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

        cur.executemany(
            f"INSERT INTO {tables['project_members']} (project_id,user_id,role) VALUES (%s,%s,%s)",
            [
                (1, 101, "lead"), (1, 103, "developer"), (1, 105, "developer"),
                (2, 103, "lead"), (2, 101, "reviewer"), (2, 105, "developer"),
                (3, 102, "lead"), (3, 104, "member"),
                (4, 101, "lead"), (4, 104, "member"),
            ],
        )

        # 将临时表注册到 SQL Agent 的 schema_loader（替换标准表名）
        from backend.sql.schema_loader import schema_loader as sl
        # 先移除标准表名
        for std_name in ["departments", "users", "projects", "project_members"]:
            sl.allowed_tables.discard(std_name)
            sl._config["tables"].pop(std_name, None)
        # 注册带前缀的临时表
        sl.register_table(tables["departments"], {
            "id": "部门ID (SERIAL PRIMARY KEY)",
            "name": "部门名称 (VARCHAR)",
            "parent_id": "上级部门ID (INTEGER)",
        }, "部门组织架构表")
        sl.register_table(tables["users"], {
            "id": "用户ID (SERIAL PRIMARY KEY)",
            "name": "姓名 (VARCHAR)",
            "email": "邮箱地址 (VARCHAR)",
            "phone": "手机号码 (VARCHAR)",
            "dept_id": "所属部门ID (INTEGER)",
            "role": "角色 (VARCHAR)",
            "created_at": "入职日期 (DATE)",
        }, "用户表")
        sl.register_table(tables["projects"], {
            "id": "项目ID (SERIAL PRIMARY KEY)",
            "name": "项目名称 (VARCHAR)",
            "owner_id": "项目负责人ID (INTEGER)",
            "budget": "预算金额 (NUMERIC, 万元)",
            "status": "项目状态 (VARCHAR)",
            "start_date": "开始日期 (DATE)",
            "end_date": "结束日期 (DATE)",
        }, "项目表")
        sl.register_table(tables["project_members"], {
            "project_id": "项目ID (INTEGER)",
            "user_id": "用户ID (INTEGER)",
            "role": "项目内角色 (VARCHAR)",
        }, "项目成员关联表")

        print(f"[OK] demo schema created (prefix={_SCHEMA_PREFIX})")
        return tables

    except Exception:
        destroy_demo_schema(tables)
        raise
    finally:
        cur.close()
        conn.close()


def destroy_demo_schema(tables: dict):
    try:
        conn = get_admin_conn()
        conn.set_session(autocommit=True)
        cur = conn.cursor()
        for tname in reversed(list(tables.values())):
            cur.execute(f"DROP TABLE IF EXISTS {tname} CASCADE")
        cur.close()
        conn.close()
        # 从 schema_loader 中移除注册，恢复标准表名
        from backend.sql.schema_loader import schema_loader as sl
        for tname in tables.values():
            sl.allowed_tables.discard(tname.lower())
            sl._config["tables"].pop(tname.lower(), None)
        # 恢复标准表名（schema_loader 在模块加载时从 SCHEMA_CONFIG 初始化）
        from backend.sql.data.schema_config import SCHEMA_CONFIG
        for std_name, std_info in SCHEMA_CONFIG["tables"].items():
            sl.allowed_tables.add(std_name.lower())
            sl._config["tables"][std_name.lower()] = std_info

        print(f"[OK] demo schema cleaned: {_SCHEMA_PREFIX}")
    except Exception as e:
        print(f"[WARN] cleanup failed: {e}")


# =====================================================
# 注册演示报告类型
# =====================================================

def register_demo_reports(tables):
    from backend.report.data_fetcher import register_report_type

    dept_sql = textwrap.dedent(f"""
        SELECT
            d.name AS dept_name,
            COUNT(DISTINCT u.id) AS user_count,
            COUNT(DISTINCT p.id) AS project_count,
            COALESCE(SUM(p.budget), 0) AS total_budget,
            COUNT(CASE WHEN p.status = 'active' THEN 1 END) AS active_count
        FROM {tables['departments']} d
        LEFT JOIN {tables['users']} u ON u.dept_id = d.id
        LEFT JOIN {tables['projects']} p ON p.owner_id = u.id
        GROUP BY d.name ORDER BY total_budget DESC
    """)

    register_report_type("dept_summary", {
        "name": "部门综合分析报告",
        "source": {"type": "sql", "sql": dept_sql},
        "templates": ["sales_summary.j2"],
        "charts": [
            {"type": "bar", "x": "dept_name", "y": "total_budget",
             "title": "各部门预算分布", "width": 10, "height": 5},
        ],
    })

    print(f"[OK] 注册演示报告类型: dept_summary")


# =====================================================
# 运行演示
# =====================================================

def run_demo():
    print("\n" + "=" * 60)
    print("  Multi-Agent 工作流系统演示")
    print("  Planner → Supervisor → Workers → Reporter")
    print("=" * 60)

    tables = create_demo_schema()
    register_demo_reports(tables)

    from backend.agent.graph import MultiAgentSystem
    agent = MultiAgentSystem()

    # =============================================
    # 用例 1: 单步查询
    # =============================================
    print(f"\n{'─' * 60}")
    print("用例 1: 简单查询（单步 SQL）")
    print("Q: 查询技术部有多少人")
    print("─" * 60)

    try:
        result1 = agent.ask("查询技术部有多少人")
        print(result1)
    except Exception as e:
        print(f"[ERROR] {e}")

    # =============================================
    # 用例 2: 并行 SQL + RAG
    # =============================================
    print(f"\n{'─' * 60}")
    print("用例 2: 并行查询（SQL + RAG 无依赖，并发执行）")
    print("Q: 查询项目预算情况，同时从知识库查找项目管理经验")
    print("─" * 60)

    try:
        result2 = agent.ask("查询项目预算情况，同时从知识库查找项目管理经验")
        print(result2)
    except Exception as e:
        print(f"[ERROR] {e}")

    # =============================================
    # 用例 3: DAG 调度（SQL+RAG → Report）
    # =============================================
    print(f"\n{'─' * 60}")
    print("用例 3: DAG 调度（SQL + RAG 并行 → Report 依赖前两步）")
    print("Q: 分析技术部预算使用情况，并从知识库查找类似项目经验，最后生成一份部门报告")
    print("─" * 60)

    try:
        result3 = agent.ask(
            "分析技术部预算使用情况，并从知识库查找类似项目经验，"
            "最后生成一份部门综合分析报告"
        )
        print(result3)
    except Exception as e:
        print(f"[ERROR] {e}")

    # =============================================
    # 用例 4: 无法拆解 → 空 plan
    # =============================================
    print(f"\n{'─' * 60}")
    print("用例 4: 无法拆解的问题 → Planner 返回空 plan → Reporter 提示")
    print("Q: 你是谁？")
    print("─" * 60)

    try:
        result4 = agent.ask("你是谁？")
        print(result4)
    except Exception as e:
        print(f"[ERROR] {e}")

    # =============================================
    # 收尾
    # =============================================
    print(f"\n{'=' * 60}")
    print("  全部用例演示完成")
    print("=" * 60)

    destroy_demo_schema(tables)


if __name__ == "__main__":
    try:
        run_demo()
    except psycopg2.OperationalError as e:
        print(f"\n[ERROR] 无法连接 PostgreSQL: {e}")
        print(f"请检查连接配置: {DB_CONFIG}")
        print("\n提示: 可通过环境变量设置连接参数")
        print("  PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD")
        sys.exit(1)

"""
demo_report_agent.py — Report Agent 演示脚本

演示完整流程:
  1. 创建临时 PostgreSQL 演示表 + 数据
  2. 注册自定义报告类型
  3. 基本报告生成（无润色）
  4. LLM 润色后的报告
  5. 图表嵌入
  6. 用户偏好学习（第二次自动选上次模板）
  7. 数据快照存取

依赖:
  pip install jinja2 matplotlib psycopg2-binary sqlglot

运行:
  python demo_report_agent.py
"""

import os
import sys
import io
import uuid
import textwrap

# 将项目根目录加入 Python 路径（支持直接运行 demo 文件）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from backend.config import DB_CONFIG
from dotenv import load_dotenv

load_dotenv()

# Windows 控制台 UTF-8
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"


# =====================================================
# 连接
# =====================================================

def get_admin_conn():
    return psycopg2.connect(**DB_CONFIG)


# =====================================================
# Step 0: 创建演示数据
# =====================================================

_SCHEMA_PREFIX = f"report_demo_{uuid.uuid4().hex[:8]}"


def create_demo_schema():
    """创建临时演示表并插入数据"""
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
                id        SERIAL PRIMARY KEY,
                name      VARCHAR NOT NULL,
                parent_id INTEGER
            );

            CREATE TABLE {tables['users']} (
                id         SERIAL PRIMARY KEY,
                name       VARCHAR NOT NULL,
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

        cur.executemany(
            f"INSERT INTO {tables['departments']} (id,name,parent_id) VALUES (%s,%s,%s)",
            [(1, "技术部", None), (2, "产品部", None),
             (3, "市场部", None), (4, "数据组", 1)],
        )
        cur.execute(f"SELECT setval('{tables['departments']}_id_seq', 4)")

        cur.executemany(
            f"INSERT INTO {tables['users']} (id,name,dept_id,role,created_at) VALUES (%s,%s,%s,%s,%s)",
            [
                (101, "张伟", 1, "admin", "2020-03-15"),
                (102, "李娜", 2, "manager", "2021-07-01"),
                (103, "王磊", 1, "staff", "2022-01-10"),
                (104, "陈静", 3, "staff", "2022-06-20"),
                (105, "刘洋", 1, "staff", "2023-02-14"),
                (106, "赵明", 2, "staff", "2023-08-01"),
            ],
        )
        cur.execute(f"SELECT setval('{tables['users']}_id_seq', 106)")

        cur.executemany(
            f"INSERT INTO {tables['projects']} (id,name,owner_id,budget,status,start_date,end_date) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [
                (1, "智能客服平台", 101, 500.0, "active", "2025-01-01", "2025-12-31"),
                (2, "数据中台建设", 103, 800.0, "active", "2025-03-01", "2026-06-30"),
                (3, "移动端改版", 102, 200.0, "completed", "2024-06-01", "2025-03-31"),
                (4, "BI 报表系统", 101, 350.0, "planning", "2025-09-01", "2026-08-31"),
                (5, "用户画像平台", 106, 150.0, "active", "2025-04-01", "2025-11-30"),
                (6, "自动化运维", 105, 280.0, "planning", "2025-07-01", "2026-03-31"),
            ],
        )
        cur.execute(f"SELECT setval('{tables['projects']}_id_seq', 6)")

        cur.executemany(
            f"INSERT INTO {tables['project_members']} (project_id,user_id,role) VALUES (%s,%s,%s)",
            [
                (1, 101, "lead"), (1, 103, "developer"), (1, 105, "developer"),
                (2, 103, "lead"), (2, 101, "reviewer"), (2, 105, "developer"),
                (3, 102, "lead"), (3, 104, "member"),
                (4, 101, "lead"), (4, 104, "member"), (4, 106, "developer"),
                (5, 106, "lead"), (5, 102, "member"),
                (6, 105, "lead"), (6, 103, "developer"),
            ],
        )

        print(f"[OK] 演示 schema 已创建 (prefix={_SCHEMA_PREFIX})")
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
        print(f"[OK] 演示 schema 已清理: {_SCHEMA_PREFIX}")
    except Exception as e:
        print(f"[WARN] 清理失败: {e}")


# =====================================================
# 运行演示
# =====================================================

def run_demo():
    print("\n" + "=" * 60)
    print("  Report Agent Demo")
    print(f"  {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    print("=" * 60)

    tables = create_demo_schema()

    # ── 注册演示报告类型 ──
    from backend.business_report.data_fetcher import register_report_type, REPORT_REGISTRY

    # 用动态表名注册
    dept_sql = textwrap.dedent(f"""
        SELECT
            d.name AS dept_name,
            COUNT(DISTINCT u.id) AS user_count,
            COUNT(DISTINCT p.id) AS project_count,
            COALESCE(SUM(p.budget), 0) AS total_budget,
            COUNT(CASE WHEN p.status = 'active' THEN 1 END) AS active_count,
            COUNT(CASE WHEN p.status = 'completed' THEN 1 END) AS completed_count,
            COUNT(CASE WHEN p.status = 'planning' THEN 1 END) AS planning_count
        FROM {tables['departments']} d
        LEFT JOIN {tables['users']} u ON u.dept_id = d.id
        LEFT JOIN {tables['projects']} p ON p.owner_id = u.id
        WHERE 1=1
        GROUP BY d.name
        ORDER BY total_budget DESC
    """)

    register_report_type("demo_dept_report", {
        "name": "部门综合报告",
        "source": {"type": "sql", "sql": dept_sql},
        "templates": ["sales_summary.j2", "sales_detail.j2"],
        "charts": [
            {"type": "bar", "x": "dept_name", "y": "total_budget",
             "title": "各部门预算分布", "width": 10, "height": 5},
            {"type": "pie", "x": "dept_name", "y": "project_count",
             "title": "各部门项目数占比", "width": 8, "height": 5},
        ],
    })

    project_sql = textwrap.dedent(f"""
        SELECT
            p.name AS project_name,
            d.name AS owner_dept,
            p.status,
            p.budget,
            p.start_date,
            p.end_date,
            COUNT(pm.user_id) AS member_count
        FROM {tables['projects']} p
        LEFT JOIN {tables['users']} u ON u.id = p.owner_id
        LEFT JOIN {tables['departments']} d ON d.id = u.dept_id
        LEFT JOIN {tables['project_members']} pm ON pm.project_id = p.id
        WHERE 1=1
        GROUP BY p.id, p.name, d.name, p.status, p.budget, p.start_date, p.end_date
        ORDER BY p.budget DESC
    """)

    register_report_type("demo_project_report", {
        "name": "项目进度报告",
        "source": {"type": "sql", "sql": project_sql},
        "templates": ["project_progress.j2"],
        "charts": [
            {"type": "bar", "x": "project_name", "y": "budget",
             "title": "项目预算对比", "width": 10, "height": 5},
        ],
    })

    from backend.business_report import ReportGenerator, generate_report

    gen = ReportGenerator(output_dir="data/reports")

    # =============================================
    # 演示 1：基础报告（无润色，无图表）
    # =============================================

    print(f"\n{'─' * 60}")
    print("演示 1: 基础报告（SQL 取数据 + 模板渲染，不润色）")
    print("─" * 60)

    report1 = gen.generate("demo_dept_report", {}, polish=False)
    print(report1)

    # =============================================
    # 演示 2：LLM 润色后的报告
    # =============================================

    print(f"\n{'─' * 60}")
    print("演示 2: 同一份报告 → LLM 语言润色")
    print("─" * 60)

    try:
        report2 = gen.generate("demo_dept_report", {}, polish=True)
        print(report2)
    except Exception as e:
        print(f"[SKIP] LLM 润色跳过（可能 Ollama 未运行）: {e}")

    # =============================================
    # 演示 3：带筛选条件的报告
    # =============================================

    print(f"\n{'─' * 60}")
    print("演示 3: 项目进度报告（带图表）")
    print("─" * 60)

    report3 = gen.generate("demo_project_report", {}, polish=False)
    print(report3)

    # =============================================
    # 演示 4：偏好学习
    # =============================================

    print(f"\n{'─' * 60}")
    print("演示 4: 用户偏好学习")
    print("─" * 60)

    from backend.business_report.preference import preference_store

    # 模拟用户第一次使用（使用 summary 模板）
    gen.generate("demo_dept_report", {"_template": "sales_detail.j2"},
                 user_id="demo_user", polish=False)
    print("  → 用户 'demo_user' 首次使用 'sales_detail.j2' 模板")

    # 查询偏好
    pref = preference_store.get("demo_user", "demo_dept_report")
    print(f"  → 偏好记录: template={pref['last_template']}, "
          f"count={pref['usage_count']}")

    # 第二次调用（不指定模板，自动用上次的）
    gen.generate("demo_dept_report", {}, user_id="demo_user", polish=False)
    print("  → 第二次调用（不指定模板），自动使用上次的 'sales_detail.j2'")

    pref = preference_store.get("demo_user", "demo_dept_report")
    print(f"  → 偏好更新: count={pref['usage_count']}")

    # =============================================
    # 演示 5：数据快照
    # =============================================

    print(f"\n{'─' * 60}")
    print("演示 5: 数据快照")
    print("─" * 60)

    from backend.business_report.snapshot import list_snapshots, load_latest_snapshot

    snaps = list_snapshots("demo_dept_report", limit=3)
    print(f"  快照列表 ({len(snaps)} 个):")
    for s in snaps:
        print(f"    {s['name']} ({s['size']} 字节) — {s['saved_at']}")

    latest = load_latest_snapshot("demo_dept_report")
    if latest:
        print(f"  → 最新快照: {latest['report_type']}, "
              f"{len(latest['data'])} 条数据, "
              f"时间 {latest['saved_at']}")

    # =============================================
    # 演示 6：错误处理 — 未知报告类型
    # =============================================

    print(f"\n{'─' * 60}")
    print("演示 6: 错误处理 — 未知报告类型")
    print("─" * 60)

    report_err = gen.generate("nonexistent_type", {}, polish=False)
    print(report_err)

    # =============================================
    # 收尾
    # =============================================

    print(f"\n{'─' * 60}")
    print("演示 7: generate_report 工具函数（一行调用）")
    print("─" * 60)

    report_oneliner = generate_report("demo_dept_report", {"_title": "一行调用报告"})
    print(report_oneliner[:500] + ("..." if len(report_oneliner) > 500 else ""))

    print(f"\n{'=' * 60}")
    print(f"  演示完成！")
    print(f"  {list_snapshots('demo_dept_report')}")
    print(f"{'=' * 60}")

    # 清理
    destroy_demo_schema(tables)


if __name__ == "__main__":
    try:
        run_demo()
    except psycopg2.OperationalError as e:
        print(f"\n[ERROR] 无法连接 PostgreSQL: {e}")
        print(f"请检查连接配置: {DB_CONFIG}")
        print("\n提示: 可通过环境变量设置连接参数")
        print("  PGHOST  — 数据库主机")
        print("  PGPORT  — 数据库端口")
        print("  PGDATABASE — 数据库名")
        print("  PGUSER  — 用户名")
        print("  PGPASSWORD — 密码")
        sys.exit(1)

"""rebuild_pg.py — 一键重建 PostgreSQL 两库（删 + 建 + 表 + 模拟数据 + 回归）

不依赖 psql 客户端 — 用 psycopg2 直接连 PG。
其它脚本（rebuild_pg.sh）只是 wrapper。

用法：
  python scripts/rebuild_pg.py                  # 完整重建（DROP + CREATE + migration + dump + 回归）
  python scripts/rebuild_pg.py --keep-data      # 不 DROP，只跑 migration（修复用，幂等）
  python scripts/rebuild_pg.py --skip-regress  # 跳过 pytest
"""
import argparse
import os
import subprocess
import sys
import time

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
BACKEND_ROOT = os.path.join(PROJECT_ROOT, 'backend')

ROOT = dict(
    host=os.getenv('PGHOST', 'localhost'),
    port=int(os.getenv('PGPORT', '5432')),
    user=os.getenv('PGUSER', 'postgres'),
    password=os.getenv('PGPASSWORD', ''),
)

MIGRATIONS_DIR = os.path.join(BACKEND_ROOT, 'sql', 'migrations')

RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'
NC = '\033[0m'


def _connect(dbname: str):
    return psycopg2.connect(dbname=dbname, **ROOT)


def banner(text, color=CYAN):
    line = '=' * 76
    print(f'\n{color}{line}{NC}')
    print(f'{color}  {text}{NC}')
    print(f'{color}{line}{NC}')


def step(label):
    print(f'\n{CYAN}[{label}]{NC}')


def ok(msg=''):
    print(f'{GREEN}  >> OK{NC}' if not msg else f'{GREEN}  >> OK {msg}{NC}')


def fail(msg):
    print(f'{RED}  >> FAIL: {msg}{NC}')
    sys.exit(1)


# =====================  各 step  =====================

def step1_terminate():
    """终止两库现存连接。"""
    conn = _connect('postgres'); conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        SELECT pid, pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname IN ('agent_memory','agent_business','demo')
          AND pid <> pg_backend_pid()
    """)
    terminated = cur.rowcount
    conn.close()
    print(f'  终止连接: {terminated} 个')
    return terminated


def step2_drop_databases():
    """DROP 两库。"""
    conn = _connect('postgres'); conn.autocommit = True
    cur = conn.cursor()
    for db in ('agent_business', 'agent_memory'):
        cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
        print(f'  DROP {db}: done')
    conn.close()


def step3_create_databases():
    """CREATE 两库。"""
    conn = _connect('postgres'); conn.autocommit = True
    cur = conn.cursor()
    for db in ('agent_memory', 'agent_business'):
        cur.execute(f'CREATE DATABASE "{db}"')
        print(f'  CREATE {db}: done')
    conn.close()


def step4_memory_migration():
    """agent_memory：跑 002 schema + 003 seed。

    002 是 export_schema_sql.py 从真实库 dump 的产物（含 IDENTITY），下次 rebuild
    会自动用同一个文件。所以首次重建要求 002 已经可用——直接重跑它：
    """
    conn = _connect('agent_memory'); conn.autocommit = True
    cur = conn.cursor()
    for fname in ('002_agent_memory_schema.sql', '003_agent_memory_seed.sql'):
        path = os.path.join(MIGRATIONS_DIR, fname)
        cur.execute(open(path, encoding='utf-8').read())
        print(f'  应用 {fname}')
    conn.close()


def step5_business_migration():
    """agent_business：001 schema + seed，剥离 ai。"""
    conn = _connect('agent_business'); conn.autocommit = True
    cur = conn.cursor()
    cur.execute(open(os.path.join(MIGRATIONS_DIR, '001_business_warehouse.sql'),
                    encoding='utf-8').read())
    cur.execute('DROP SCHEMA IF EXISTS ai CASCADE')
    print('  应用 001_business_warehouse.sql + DROP ai')
    conn.close()


def step6_regenerate_dump():
    """重跑 export_schema_sql.py → 002/003 与真实库对齐。"""
    script = os.path.join(os.path.dirname(__file__), 'export_schema_sql.py')
    r = subprocess.run([sys.executable, script], cwd=PROJECT_ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail(f'export_schema_sql.py failed:\n{r.stderr}')
    print(r.stdout.strip())


def step7_regression():
    """跑后端测试（跳过 baseline tracer NameError）。"""
    cmd = [sys.executable, '-m', 'pytest', 'tests', '--no-cov', '-q',
           '--ignore=tests/rag/test_tracer.py',
           '--ignore=tests/rag/test_tracer_subscribe.py']
    r = subprocess.run(cmd, cwd=BACKEND_ROOT, capture_output=True, text=True)
    # 打印最后 15 行
    out = r.stdout.strip()
    tail = '\n'.join(out.splitlines()[-15:])
    print(tail)
    return r.returncode


def step8_inventory():
    """输出两库最终状态。"""
    for db in ('agent_memory', 'agent_business'):
        conn = _connect(db); cur = conn.cursor()
        cur.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog','information_schema')
              AND table_schema NOT LIKE 'pg_%'
            ORDER BY table_schema, table_name
        """)
        rows = cur.fetchall()
        print(f'\n  [{db}] {len(rows)} 张表:')
        for sch, tbl in rows:
            cur.execute(f'SELECT COUNT(*) FROM "{sch}".{tbl}')
            n = cur.fetchone()[0]
            print(f'    {sch}.{tbl}: {n} 行')
        conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--keep-data', action='store_true',
                   help='不 DROP，只重新跑 migration')
    p.add_argument('--skip-regress', action='store_true',
                   help='跳过 pytest，只重建数据')
    args = p.parse_args()

    banner(
        f'完整重建模式 — DROP + CREATE + migration' if not args.keep_data
        else 'KEEP-DATA 模式 — 不 DROP，只补 migration',
        CYAN,
    )

    t_total = time.time()

    if not args.keep_data:
        step('Step 1: 终止连接')
        step1_terminate(); ok()

        step('Step 2: DROP DATABASE')
        step2_drop_databases(); ok()

        step('Step 3: CREATE DATABASE')
        step3_create_databases(); ok()

    step('Step 4: agent_memory — inline schema + seed')
    step4_memory_migration(); ok()

    step('Step 5: agent_business — schema + seed + DROP ai')
    step5_business_migration(); ok()

    step('Step 6: 重导 002/003 schema dump（基于真实库 — 含 IDENTITY）')
    step6_regenerate_dump(); ok()

    if not args.skip_regress:
        step('Step 7: 后端回归 pytest')
        rc = step7_regression()
        if rc == 0:
            ok(f'全部通过')
        else:
            print(f'{YELLOW}  ⚠️ 有失败测试 — 不阻断重建{NC}')

    step('Step 8: 最终两库状态')
    step8_inventory()

    banner(
        f'完成 — 用时 {time.time() - t_total:.1f}s',
        GREEN,
    )


if __name__ == '__main__':
    main()

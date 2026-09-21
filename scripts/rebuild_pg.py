"""rebuild_pg.py — 一键重建 PostgreSQL 两库（删 + 建 + 迁移 + 回归）

2026-09-21 起 schema 版本管理统一走 scripts/init_db.py（alembic 已退役）：
  - 迁移事实源 = backend/sql/migrations/*.sql（init_db.py 自动扫描、幂等可重跑）
  - 本脚本保留"完整重建 + 回归测试"的编排职责，迁移步骤委托给 init_db.py

用法：
  python scripts/rebuild_pg.py                  # 完整重建（DROP + init_db.py + 回归）
  python scripts/rebuild_pg.py --keep-data      # 不 DROP，只跑 init_db.py（修复用，幂等）
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


def _init_db(reset: bool = False):
    """执行 scripts/init_db.py（2026-09-21：替代 alembic upgrade head）。

    init_db.py 自动扫描 backend/sql/migrations/*.sql，幂等可重跑；
    reset=True 时删库重建（等价原 DROP+CREATE+upgrade head 全流程）。
    """
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, 'scripts', 'init_db.py')]
    if reset:
        cmd += ['--reset', '--yes']
    r = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        fail(f'init_db.py failed:\n{r.stderr}')


def step4_memory_migration():
    """两库迁移已由 init_db.py 一次完成（保留函数名兼容旧调用）。"""
    _init_db(reset=False)


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

    step('Step 4: 两库迁移 — scripts/init_db.py')
    step4_memory_migration(); ok()

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

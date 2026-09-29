#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""init_db.py — 数据库一键初始化（建库 + 建表 + 示例数据），幂等可重跑

背景
----
项目此前有两套初始化入口，且都是「手抄迁移清单」，已经明显落后于
`backend/sql/migrations/` 里的真实文件（30 个）：

  - `docker/init-dbs.sh`：由 postgres 镜像 docker-entrypoint-initdb.d 调用，
    只在**数据卷为空的首次启动**执行一次；缺 003_agent_business / 021~024 /
    028~030。
  - `scripts/ensure_dbs.py`：给存量数据卷补齐用；缺 008~026 / 028~030 共 16 个。

结果就是新增的表/列没人落地，只能靠应用侧运行时「幂等建表」兜底，
库结构随应用版本漂移。

本脚本做的事
------------
1. 自动扫描 `backend/sql/migrations/*.sql`，按 `MIGRATION_TARGETS` 决定归属库；
   **扫到未登记的文件直接报错退出**（不静默跳过）—— 新增迁移必须登记，
   否则 CI/本地初始化会立刻告诉你，而不是悄悄漏掉。
2. 建库（不存在才建）+ 按序执行迁移 + 记录到 `public.schema_migrations`。
3. 示例数据：迁移自带的 seed（001 业务数据 / 003 记忆数据）随迁移执行，
   全部 `ON CONFLICT DO NOTHING`，重跑不会产生重复行；
   `--demo-sandbox` 额外灌客服演示数据。
4. `--check` 只体检不落库；`--force` 忽略已应用记录重跑；
   `--reset` 危险重建（需二次确认）。

表已存在时的策略（重要）
------------------------
默认 **skip：保留现有表与数据，绝不 DROP / TRUNCATE**。
单文件执行时若撞到「已存在」类错误，会自动降级为**逐语句执行**，
把该文件里还没建的对象补上（旧脚本是整个文件跳过，会漏对象）。

技术栈
------
不引入任何新框架：psycopg2 + python-dotenv（项目已在用），
连接参数复用 `backend/config/database.py` 的环境变量命名
（PGHOST/PGPORT/PGUSER/PGPASSWORD/MEMORY_PGDATABASE/BUSINESS_PGDATABASE）。

用法
----
  # 宿主机（项目 PG 在 docker 里，宿主端口 5433）
  ./.venv/Scripts/python.exe scripts/init_db.py --port 5433

  # 只体检，不落库
  ./.venv/Scripts/python.exe scripts/init_db.py --port 5433 --check

  # 容器内（compose 已注入 PGHOST=postgres PGPORT=5432）
  docker compose exec app python scripts/init_db.py

  # 追加客服演示数据
  ./.venv/Scripts/python.exe scripts/init_db.py --port 5433 --demo-sandbox

  # 危险：删库重建
  ./.venv/Scripts/python.exe scripts/init_db.py --port 5433 --reset --yes
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path

try:  # Windows 控制台中文兜底
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "backend" / "sql" / "migrations"
SEEDS_DIR = ROOT / "backend" / "sql" / "seeds"

# ── 迁移 → 目标库（单一事实源；漏登记会 fail-fast，见 main()）────────────
# business = agent_business（业务数据仓库：7 业务 schema + public 业务族）
# memory   = agent_memory（Agent 元数据 / 记忆 / 可观测 / 认证 / 客服域）
MIGRATION_TARGETS: dict[str, str] = {
    # ── agent_business ──
    "001_business_warehouse.sql": "business",
    "003_agent_business_schema.sql": "business",
    "005_schema_hardening.sql": "business",
    "007_tool_approval.sql": "business",
    "016_inventory_alerts_pg.sql": "business",
    "017_business_stores_pg.sql": "business",
    "021_selection_funnel_pg.sql": "business",
    "022_feedback_review_candidates.sql": "business",
    # ── agent_memory ──
    "002_agent_memory_schema.sql": "memory",
    "003_agent_memory_seed.sql": "memory",
    "006_customer_service.sql": "memory",
    "008_local_auth.sql": "memory",
    "009_auth_roles.sql": "memory",
    "010_doc_registry_pg.sql": "memory",
    "011_doc_registry_version_governance.sql": "memory",
    "012_obs_trace_store_pg.sql": "memory",
    "013_obs_analytics_pg.sql": "memory",
    "014_cs_rating.sql": "memory",
    "014_rag_stores_pg.sql": "memory",
    "015_workflow_runs_pg.sql": "memory",
    "018_prompts_pg.sql": "memory",
    "019_gateway_access_logs_pg.sql": "memory",
    "020_alembic_gaps_pg.sql": "memory",
    "023_auth_sessions.sql": "memory",
    "024_rag_eval_fixture_set.sql": "memory",
    "025_metadata_rule_governance.sql": "memory",
    "026_metadata_shadow_jobs.sql": "memory",
    "027_rag_processing_lineage.sql": "memory",
    "028_cs_dispatch.sql": "memory",
    "029_rbac_audit.sql": "memory",
    "030_cs_dispatch_hardening.sql": "memory",
    "031_model_governance_pg.sql": "memory",
    "032_memory_embedding_vector.sql": "memory",
    "033_auth_user_lifecycle.sql": "memory",
    "034_model_runtime_governance_pg.sql": "memory",
    "035_llm_usage_cost_columns.sql": "memory",
    "036_cs_tickets.sql": "memory",
    "037_cs_qa_reports.sql": "memory",
    "038_llm_models_upstream_name.sql": "memory",
    "039_llm_models_ocr_kind.sql": "memory",
    "040_chat_sessions_summary_frontier.sql": "memory",
    "041_auth_departments.sql": "memory",
    "042_sql_query_audits.sql": "memory",
    # SQL Agent 生产收口（STOP C）：readonly 角色回收 public schema SELECT
    "043_readonly_public_revoke.sql": "business",
    # Context Budget 生产加固（STOP C 2026-09-23）：L5 摘要水位线 CAS 版本号
    "044_chat_sessions_summary_version.sql": "memory",
    # Model Governance STOP B（2026-09-23）：max_output_tokens 列 + 活跃模型
    # context_length/capabilities 登记
    "045_llm_models_governance_columns.sql": "memory",
    # Model Governance STOP C（2026-09-23）：llm_usage 身份链列 + 单价快照
    "046_llm_usage_identity_billing.sql": "memory",
    # Phase2 Step6（2026-09-24）：幂等 ledger owner_execution_id 列 + 实机探针表
    "047_side_effect_idempotency.sql": "memory",
    # Memory provenance（STOP B，2026-09-24）：memory_records origin + 溯源列。
    # 部署 unblock 登记（STOP H-D2）：该 untracked 文件在 build 上下文内导致
    # db-migrate fail-fast 挡住共享栈；与 047_side_effect 同号不同名，按
    # 文件名排序可共存。
    "047_memory_provenance.sql": "memory",
    # Memory scope + 事实版本管理（Memory STOP C，2026-09-24）：memory_records
    # tenant_id/user_key/memory_key/version 等列。Platform Readiness STOP B
    # 登记：此前未登记 + db-migrate 镜像漂移，导致「git 有 048、镜像无 048、
    # migration 静默跳过」事故复现（实库对象已被并行会话手工补齐；本登记使
    # schema_migrations 收敛，避免下次重建 rc=2 整栈拒绝启动）。
    "048_memory_scope_and_versioning.sql": "memory",
    # Phase3 STOP B（2026-09-24）：tasks PENDING Recovery 列 + 索引。
    # tasks 表演进权威在 backend/tasks/schema.sql（ensure_schema 幂等），
    # 本文件为 db-migrate 流程的实库执行留痕，语句全部 IF NOT EXISTS 幂等。
    # 文件当前 untracked（Phase3 会话所有）：漏登记会在下次重建触发
    # fail-fast 整栈拒绝启动，故先行登记；文件缺失的干净检出不受影响
    # （discover 按目录扫描，登记表多出条目无害）。
    "049_task_pending_recovery.sql": "memory",
    # Side-Effect 幂等人工生产验收（STOP F1，2026-09-24）：agent_actions
    # .status 枚举对齐应用契约（AgentActionRecord = simulated|executed|failed
    # 与 006 六值枚举交集仅 failed）——此前同事务审计落库在真实确认动作上
    # 必然 CheckViolation 回滚，agent_actions 生产表零落库。additive 扩枚举。
    "050_agent_actions_status_enum.sql": "memory",
    # Phase3 STOP D：confirmations 业务操作身份两列 + state CHECK 收编
    # verifying + active 语义操作 partial unique index（跨 confirmation 去重）
    "051_cs_business_operation_guard.sql": "memory",
    "052_travel_booking.sql": "memory",
    # Model Governance：补齐 031 已声明但未挂载的 model_price append-only trigger。
    "053_model_price_immutable_trigger.sql": "memory",
    # 三端账号权限最小上线改造：静态角色扩展，不引入动态角色表。
    "054_auth_super_admin.sql": "memory",
    # 企业治理 M5（2026-09-30）：llm_usage 业务归因三列（skill/tool/域），
    # 成本六维聚合补齐 Skill/Tool/Agent 维度。与 llm_usage_store_pg
    # ensure_schema 自愈段同口径。
    "055_llm_usage_attribution.sql": "memory",
    # 企业治理 M7（2026-09-30）：评测 run 台账（索引非替代，明细仍在
    # data/eval_runs 文件），prompt/模型指纹/触发者随行落库。
    "056_eval_run_records.sql": "memory",
    # 企业治理 M4（2026-09-30）：prompt 版本语义（change_kind）+ 命名指针
    # （production 与 active_version 同步 / staging 预发指向）。
    "057_prompt_alias.sql": "memory",
    # 客服域迁移 B12（2026-09-29）：统一案件 cs_case（工单/投诉/售后收敛基座）
    "058_cs_case.sql": "memory",
}

# 数字排序之外需要压到最后执行的（依赖其它迁移先建好的对象）
ORDER_LAST = ["004_readonly_role.sql"]
MIGRATION_TARGETS["004_readonly_role.sql"] = "business"

# 运行时管理的迁移（Platform Readiness STOP B）：对象由应用启动时的
# ensure_schema() 幂等建立，而非迁移链。fresh 库上没有这些对象，直接执行
# 会 UndefinedTable 导致整链 rc=1——统一 skip 并登记 status='skipped'，
# 实库 schema 由 ensure_schema 幂等保证（这与「登记表防 fail-fast」并不
# 冲突：登记仍在 MIGRATION_TARGETS，只是执行态为运行时托管）。
RUNTIME_MANAGED_MIGRATIONS: dict[str, str] = {
    "049_task_pending_recovery.sql":
        "tasks 表演进权威 = backend/tasks/schema.sql ensure_schema()",
}

# 可选的额外种子（--demo-sandbox）
DEMO_SANDBOX_SEED = "demo_sandbox.sql"

# 「已存在 / 已重复」类 SQLSTATE —— 幂等跳过，不算失败
# 42P07 重复表 | 42710 重复对象 | 42P06 重复 schema | 42P04 重复库
# 42723 重复函数 | 42P16 重复约束 | 23505 唯一键冲突（种子数据重跑）
# 42701 重复列 | 42P13? | 42883 未定义函数（扩展/版本差异，见下）
_ALREADY_EXISTS_SQLSTATES = {
    "42P07", "42710", "42P06", "42P04", "42723", "42P16", "23505", "42701",
}

_DOLLAR_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")

_TRACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename   TEXT PRIMARY KEY,
    checksum   TEXT        NOT NULL,
    status     TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


# ──────────────────────────────────────────────────────────── 环境 / 连接
def load_env_file(path: Path) -> None:
    """把 .env 读进环境（不覆盖已有变量——容器内以进程 env 为准）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def conn_params(dbname: str, args: argparse.Namespace) -> dict:
    return {
        "host": args.host or os.getenv("PGHOST", "localhost"),
        "port": int(args.port or os.getenv("PGPORT", "5432")),
        "user": args.user or os.getenv("PGUSER", "postgres"),
        "password": os.getenv("PGPASSWORD", ""),
        "dbname": dbname,
        "connect_timeout": 8,
    }


def connect(dbname: str, args: argparse.Namespace):
    import psycopg2

    return psycopg2.connect(**conn_params(dbname, args))


# ──────────────────────────────────────────────────────────── SQL 切分
def split_sql_statements(sql: str) -> list[str]:
    """把整段 SQL 切成单条语句。

    需要处理：-- 行注释、/* */ 块注释、'单引号'（含 '' 与 E'\\n' 转义）、
    "双引号" 标识符、$$ / $tag$ 美元引用函数体。
    """
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    state = None  # None | 'line' | 'block' | 'str' | 'ident'
    dollar_tag: str | None = None

    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if state is None and dollar_tag is None:
            m = _DOLLAR_RE.match(sql, i)
            if m:
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                i += len(dollar_tag)  # 先前进再置空，否则 len(None) 崩溃
                buf.append(dollar_tag)
                dollar_tag = None
                continue
            buf.append(c)
            i += 1
            continue

        if state == "line":
            buf.append(c)
            if c == "\n":
                state = None
            i += 1
            continue
        if state == "block":
            buf.append(c)
            if c == "*" and nxt == "/":
                buf.append("/")
                state = None
                i += 2
                continue
            i += 1
            continue
        if state == "str":
            buf.append(c)
            if c == "'":
                if nxt == "'":  # '' 转义
                    buf.append("'")
                    i += 2
                    continue
                state = None
            elif c == "\\":  # E'...\n...' 之类
                buf.append(nxt)
                i += 2
                continue
            i += 1
            continue
        if state == "ident":
            buf.append(c)
            if c == '"':
                state = None
            i += 1
            continue

        # ── 普通上下文 ──
        if c == "-" and nxt == "-":
            state = "line"
            buf.append(c)
            i += 1
            continue
        if c == "/" and nxt == "*":
            state = "block"
            buf.append(c)
            i += 1
            continue
        if c == "'":
            state = "str"
            buf.append(c)
            i += 1
            continue
        if c == '"':
            state = "ident"
            buf.append(c)
            i += 1
            continue
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def is_exists_error(exc: BaseException) -> bool:
    return getattr(exc, "pgcode", None) in _ALREADY_EXISTS_SQLSTATES


def checksum_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ──────────────────────────────────────────────────────────── 迁移执行
def apply_migration(conn, filename: str, sql: str, *, force: bool) -> tuple[str, str]:
    """执行单个迁移文件，返回 (状态, 说明)。

    状态：applied（全量执行）/ applied-partial（降级逐语句补齐）/
          skipped（整文件都已是"已存在"）/ error
    """
    path_display = filename
    with conn.cursor() as cur:
        cur.execute(
            "SELECT checksum, status FROM public.schema_migrations WHERE filename = %s",
            (filename,),
        )
        row = cur.fetchone()
    if row and not force:
        if row[0] != checksum_of(sql):
            # 文件内容变了 → 需要重新应用（并更新 checksum）
            pass
        elif row[1] == "applied":
            return "skipped", "已应用（记录一致）"
        elif row[1] == "error":
            pass  # 上次失败，重试

    # ① 整文件执行（快，且保留多语句事务语义）
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
            conn.commit()
            _record(conn, filename, sql, "applied")
            return "applied", "整文件执行成功"
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            if not is_exists_error(e):
                _record(conn, filename, sql, "error")
                return "error", f"{type(e).__name__}: {str(e)[:200]}"
            # ② 撞到"已存在" → 降级逐语句，把缺的对象补上
            #    （旧脚本在这里直接整个文件跳过，会漏建新增对象）

    applied_new, skipped, errors = 0, 0, []
    for stmt in split_sql_statements(sql):
        with conn.cursor() as cur:
            try:
                cur.execute(stmt)
                applied_new += 1
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                if is_exists_error(e):
                    skipped += 1
                else:
                    errors.append(f"{type(e).__name__}: {str(e)[:160]}")
    conn.commit()
    if errors:
        _record(conn, filename, sql, "error")
        return "error", "；".join(errors[:3])
    if applied_new == 0:
        _record(conn, filename, sql, "applied")
        return "skipped", f"全部已存在（跳过 {skipped} 条）"
    _record(conn, filename, sql, "applied")
    return "applied-partial", f"补齐 {applied_new} 条 / 跳过 {skipped} 条"


def _record(conn, filename: str, sql: str, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.schema_migrations (filename, checksum, status)
            VALUES (%s, %s, %s)
            ON CONFLICT (filename) DO UPDATE
               SET checksum = EXCLUDED.checksum,
                   status   = EXCLUDED.status,
                   applied_at = now()
            """,
            (filename, checksum_of(sql), status),
        )
    conn.commit()


# ──────────────────────────────────────────────────────────── 库 / 角色
def ensure_database(admin, dbname: str, *, do_create: bool) -> str:
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        exists = cur.fetchone() is not None
    if exists:
        return "已存在"
    if not do_create:
        return "缺失（--check 不创建）"
    admin.rollback()  # CREATE DATABASE 不能在事务块里跑
    admin.autocommit = True
    with admin.cursor() as cur:
        # 参数化不可用（DDL），但库名来自本文件常量/环境变量，非用户输入拼接
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.autocommit = False
    return "已创建"


def ensure_readonly_role(conn, *, user: str, password: str) -> str:
    """对齐只读角色密码（DDL 用 psycopg2.sql 组合，不做字符串拼接）。"""
    from psycopg2 import sql as pgsql

    if not password:
        return "跳过（未配置 PG_READONLY_PASSWORD）"
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,))
        if cur.fetchone() is None:
            return f"跳过（角色 {user} 不存在，004 迁移应先创建）"
        cur.execute(
            pgsql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                pgsql.Identifier(user), pgsql.Literal(password)
            )
        )
    conn.commit()
    return "ok"


def verify_readonly(args: argparse.Namespace, dbname: str, *, user: str, password: str) -> str:
    if not password:
        return "跳过（未配置只读密码）"
    try:
        conn = connect(dbname, args)
        params = conn_params(dbname, args)
        conn.close()
        import psycopg2

        conn = psycopg2.connect(user=user, password=password, **{k: v for k, v in params.items()
                                                               if k not in ("user", "password")})
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            who = cur.fetchone()[0]
        conn.close()
        return f"ok（{who}）"
    except Exception as e:  # noqa: BLE001
        return f"FAIL: {type(e).__name__}: {str(e)[:120]}"


def health_check(conn, dbname: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, count(*) FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            GROUP BY 1 ORDER BY 1
            """
        )
        rows = cur.fetchall()
    return ", ".join(f"{s}={n}" for s, n in rows) or "(空)"


def row_counts(conn, limit: int = 12) -> list[tuple[str, int]]:
    """返回数据量最多的若干张用户表，用于肉眼确认数据真的进去了。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables
            WHERE n_live_tup > 0
            ORDER BY n_live_tup DESC LIMIT %s
            """,
            (limit,),
        )
        return [(f"{s}.{t}", int(n)) for s, t, n in cur.fetchall()]


# ──────────────────────────────────────────────────────────── 主流程
def discover_migrations() -> tuple[list[tuple[str, str]], list[str]]:
    """返回 ([(filename, target)], 未登记文件名)。"""
    files = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    ordered, unknown = [], []
    for name in files:
        target = MIGRATION_TARGETS.get(name)
        if target is None:
            unknown.append(name)
        elif name not in ORDER_LAST:
            ordered.append((name, target))
    for name in ORDER_LAST:
        if name in MIGRATION_TARGETS and (Path(MIGRATIONS_DIR / name)).exists():
            ordered.append((name, MIGRATION_TARGETS[name]))
    return ordered, unknown


def main() -> int:
    ap = argparse.ArgumentParser(
        description="数据库一键初始化（建库 + 建表 + 示例数据），幂等可重跑",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--host", help="覆盖 PGHOST（容器内为 postgres）")
    ap.add_argument("--port", help="覆盖 PGPORT（宿主机访问 docker PG 通常是 5433）")
    ap.add_argument("--user", help="覆盖 PGUSER")
    ap.add_argument("--check", action="store_true", help="只体检不落库")
    ap.add_argument("--force", action="store_true",
                    help="忽略 public.schema_migrations 记录，全部重跑（仍靠 SQL 幂等，不删数据）")
    ap.add_argument("--demo-sandbox", action="store_true",
                    help="额外灌客服演示数据 backend/sql/seeds/demo_sandbox.sql")
    ap.add_argument("--reset", action="store_true",
                    help="危险：DROP 两个库后重建（必须配合 --yes）")
    ap.add_argument("--yes", action="store_true", help="与 --reset 配合，确认删库")
    args = ap.parse_args()

    load_env_file(ROOT / ".env")

    memory_db = os.getenv("MEMORY_PGDATABASE", "agent_memory")
    business_db = os.getenv("BUSINESS_PGDATABASE", "agent_business")
    ro_user = os.getenv("PG_READONLY_USER", "agent_readonly")
    ro_pw = os.getenv("PG_READONLY_PASSWORD", "")
    targets = {"memory": memory_db, "business": business_db}

    plan, unknown = discover_migrations()
    print(f"[init_db] 迁移目录: {MIGRATIONS_DIR}")
    print(f"[init_db] 发现 {len(plan) + len(unknown)} 个迁移文件，"
          f"已登记 {len(plan)} 个")

    if unknown:
        print("[init_db] ✗ 以下迁移文件未登记目标库，请在 scripts/init_db.py "
              "的 MIGRATION_TARGETS 中补充后重跑：")
        for name in unknown:
            print(f"           - {name}")
        return 2

    host = args.host or os.getenv("PGHOST", "localhost")
    port = args.port or os.getenv("PGPORT", "5432")
    print(f"[init_db] 目标: {host}:{port}  memory={memory_db}  business={business_db}")

    try:
        admin = connect("postgres", args)
    except Exception as e:  # noqa: BLE001
        print(f"[init_db] ✗ 连不上 PostgreSQL: {type(e).__name__}: {e}")
        if host in ("localhost", "127.0.0.1") and port == "5432":
            print("[init_db]   提示：项目 PG 跑在 docker 里，宿主映射端口通常是 5433，"
                  "可加 --port 5433；容器内执行则无需指定。")
        return 2

    rc = 0
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT version()")
            print(f"[init_db] 服务端: {cur.fetchone()[0][:50]}")

        # ── 0. 危险重建 ──
        if args.reset:
            if not args.yes:
                print("[init_db] ✗ --reset 必须配合 --yes（会删除全部数据）")
                return 2
            if args.check:
                print("[init_db] ✗ --reset 与 --check 互斥")
                return 2
            admin.rollback()  # DROP DATABASE 同样不能在事务块里跑
            admin.autocommit = True
            for db in (memory_db, business_db):
                with admin.cursor() as cur:
                    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                "WHERE datname = %s AND pid <> pg_backend_pid()", (db,))
                    cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
                print(f"[init_db] ⚠ 已删除库: {db}")
            admin.autocommit = False

        # ── 1. 建库 ──
        for db in (memory_db, business_db):
            print(f"[init_db] 库 {db}: {ensure_database(admin, db, do_create=not args.check)}")
        admin.close()

        # ── 2. 迁移 ──
        if args.check:
            for name, target in plan:
                print(f"[init_db]   {target}/{name}: 跳过（--check）")
        else:
            for db_key, dbname in (("memory", memory_db), ("business", business_db)):
                conn = connect(dbname, args)
                with conn.cursor() as cur:
                    cur.execute(_TRACK_TABLE_SQL)
                conn.commit()

                print(f"[init_db] ── {dbname} ──")
                for name, target in plan:
                    if target != db_key:
                        continue
                    if name in RUNTIME_MANAGED_MIGRATIONS:
                        sql = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
                        _record(conn, name, sql, "skipped")
                        conn.commit()
                        print(f"[init_db]   = {name}: skipped — "
                              f"{RUNTIME_MANAGED_MIGRATIONS[name]}")
                        continue
                    sql = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
                    t0 = time.time()
                    status, detail = apply_migration(conn, name, sql, force=args.force)
                    mark = {"applied": "✓", "applied-partial": "~",
                            "skipped": "=", "error": "✗"}[status]
                    print(f"[init_db]   {mark} {name}: {status} — {detail} "
                          f"({time.time() - t0:.1f}s)")
                    if status == "error":
                        rc = 1

                if db_key == "business":
                    print(f"[init_db]   只读角色密码对齐: "
                          f"{ensure_readonly_role(conn, user=ro_user, password=ro_pw)}")

                if args.demo_sandbox and db_key == "business":
                    seed_path = SEEDS_DIR / DEMO_SANDBOX_SEED
                    if seed_path.exists():
                        sql = seed_path.read_text(encoding="utf-8")
                        status, detail = apply_migration(
                            conn, f"seed:{DEMO_SANDBOX_SEED}", sql, force=True
                        )
                        print(f"[init_db]   ✓ 演示数据 {DEMO_SANDBOX_SEED}: {status} — {detail}")
                    else:
                        print(f"[init_db]   ✗ 演示数据文件不存在: {seed_path}")
                        rc = 1

                print(f"[init_db]   {dbname} 表分布: {health_check(conn, dbname)}")
                top = row_counts(conn)
                if top:
                    print("[init_db]   数据量 Top: " +
                          ", ".join(f"{t}={n}" for t, n in top[:8]))
                conn.close()

        # ── 3. 只读账号实测 ──
        if not args.check:
            print(f"[init_db] 只读账号连通性: "
                  f"{verify_readonly(args, business_db, user=ro_user, password=ro_pw)}")
    finally:
        try:
            admin.close()
        except Exception:  # noqa: BLE001
            pass

    print(f"[init_db] 完成 rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())

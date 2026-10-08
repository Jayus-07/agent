"""test_schema_config_db_parity.py — 声明口径 ↔ agent_business 真库 派生守卫

背景（2026-10-08 实查）：
  `schema_config.py::SCHEMA_CONFIG` 是 SQL Agent 的**唯一纳管清单**，既是
  validator Layer 2 的表白名单，也是喂给 LLM 的数据字典。在此之前没有任何
  用例证明它与真库一致——2026-10-08 实测「18 表 / 97 列逐表逐列相等」，
  属于运气而不是契约；一旦库侧加列/改表，症状是运行期
  `column_not_found`（白跑一次修复重试）或模型「看不见」真实列。

本文件把该对照固化成门禁（四段，全部从真库实时派生，禁手抄）：
  1. 声明 schema / 表必须真实存在
  2. 声明列集合与真库**双向相等**（缺列 = 生成即失败；多列 = 模型看不见）
  3. 脱敏列 / 敏感列声明的列必须真实存在
  4. `agent_readonly` 在声明 schema 内的可见面**恒等于声明面**
     （多一张 = 越权授权，少一张 = 声明了却读不到；
      004 + 043 + 083 的授权收窄由本用例锁定）

运行：
    cd backend && python -m pytest tests/sql/test_schema_config_db_parity.py -v --no-cov
unit-only（-m "not pg"）可排除；agent_business 不可达时自动 skip。
"""
from __future__ import annotations

from collections import defaultdict

import psycopg2
import pytest

from backend.config import BUSINESS_DB_CONFIG
from backend.sql.data.schema_config import SCHEMA_CONFIG

DECLARED_TABLES: dict[str, set[str]] = {
    table: set(cfg["columns"]) for table, cfg in SCHEMA_CONFIG["tables"].items()
}
DECLARED_SCHEMAS: set[str] = set(SCHEMA_CONFIG["schemas"])
READONLY_ROLE = "agent_readonly"


def _conn_alive() -> bool:
    try:
        conn = psycopg2.connect(**BUSINESS_DB_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(
        not _conn_alive(),
        reason="agent_business 不可达（docker agent-postgres-1 / PGPORT=5433）",
    ),
]


def _fetch(sql: str, params: tuple = ()) -> list[tuple]:
    conn = psycopg2.connect(**BUSINESS_DB_CONFIG, connect_timeout=3)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _db_schemas() -> set[str]:
    rows = _fetch(
        "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)",
        (sorted(DECLARED_SCHEMAS),),
    )
    return {row[0] for row in rows}


def _db_columns() -> dict[str, set[str]]:
    rows = _fetch(
        "SELECT table_schema || '.' || table_name, column_name "
        "FROM information_schema.columns WHERE table_schema = ANY(%s)",
        (sorted(DECLARED_SCHEMAS),),
    )
    columns: dict[str, set[str]] = defaultdict(set)
    for table, column in rows:
        columns[table].add(column)
    return dict(columns)


# ─────────────────────────────────────────────────────────────
# 1. schema / 表存在性
# ─────────────────────────────────────────────────────────────

def test_declared_schemas_exist():
    assert _db_schemas() == DECLARED_SCHEMAS


def test_declared_tables_exist_in_db():
    present = set(_db_columns())
    missing = sorted(set(DECLARED_TABLES) - present)
    assert not missing, (
        f"声明纳管但真库不存在：{missing}——router 会选中它，执行期必然 "
        "UndefinedTable；请同步 schema_config 或补迁移"
    )


# ─────────────────────────────────────────────────────────────
# 2. 列集合双向相等
# ─────────────────────────────────────────────────────────────

def test_declared_columns_match_db_exactly():
    actual = _db_columns()
    drift: list[str] = []
    for table, declared_columns in sorted(DECLARED_TABLES.items()):
        db_columns = actual.get(table, set())
        missing = sorted(declared_columns - db_columns)
        extra = sorted(db_columns - declared_columns)
        if missing:
            drift.append(f"{table}: 声明了但库里没有 {missing}（生成即 column_not_found）")
        if extra:
            drift.append(f"{table}: 库里有但未声明 {extra}（模型看不见该列）")
    assert not drift, "schema_config 与真库列集合漂移：\n  " + "\n  ".join(drift)


# ─────────────────────────────────────────────────────────────
# 3. 脱敏列 / 敏感列声明有效
# ─────────────────────────────────────────────────────────────

def test_masked_and_sensitive_columns_are_declared():
    configured = (
        list(SCHEMA_CONFIG["masked_columns"])
        + list(SCHEMA_CONFIG["sensitive_columns"])
    )
    assert configured, "脱敏/敏感列配置不应同时为空到无从校验（占位守卫）"
    broken: list[str] = []
    for qualified in configured:
        table, _, column = qualified.rpartition(".")
        if table not in DECLARED_TABLES:
            broken.append(f"{qualified}: 表 {table} 未纳管")
        elif column not in DECLARED_TABLES[table]:
            broken.append(f"{qualified}: 列不在声明里")
    assert not broken, f"脱敏/敏感列配置失效：{broken}"


# ─────────────────────────────────────────────────────────────
# 4. 只读角色可见面 == 声明面
# ─────────────────────────────────────────────────────────────

def test_readonly_grants_equal_declared_tables():
    rows = _fetch(
        "SELECT table_schema || '.' || table_name "
        "FROM information_schema.table_privileges "
        "WHERE grantee = %s AND privilege_type = 'SELECT' "
        "AND table_schema = ANY(%s)",
        (READONLY_ROLE, sorted(DECLARED_SCHEMAS)),
    )
    granted = {row[0] for row in rows}
    declared = set(DECLARED_TABLES)

    over = sorted(granted - declared)
    under = sorted(declared - granted)
    assert not over, (
        f"{READONLY_ROLE} 对未纳管表持有 SELECT（越权授权，纵深防御少一层）：{over}"
        "——请按 083_readonly_ai_grant_scope.sql / 043_readonly_public_revoke.sql "
        "的口径回收（schema 级 GRANT 是根因）"
    )
    assert not under, (
        f"声明纳管的表 {READONLY_ROLE} 读不到（声明面 > 授权面）：{under}"
        "——请补显式 GRANT SELECT"
    )

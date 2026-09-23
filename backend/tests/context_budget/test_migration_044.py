"""生产收口 B1 — migration 044 落地验证测试（2026-09-23）

对本地真实目标库（docker agent-postgres-1，宿主映射 5433）验证：
  - chat_sessions.summary_version 列存在、NOT NULL、DEFAULT 0
  - L5 CAS SQL 依赖成立：get_summary_state 读侧不炸（044 落地前的生产
    事故形态）；save_summary_state 对不存在 session 走 rowcount=0 的 CAS
    False 路径（零写入、不产脏数据）
  - migration 文件幂等：重复执行 044 不报错（IF NOT EXISTS）

库不可达时整模块 skip（CI 无 PG 环境不误报）。连接端口显式 5433 ——
本机 5432 是原生 PG 同名库（双库坑）。
"""
import os

import pytest

pytest.importorskip("psycopg")

_PG_HOST = os.getenv("PGTEST_HOST", "localhost")
_PG_PORT = int(os.getenv("PGTEST_PORT", "5433"))  # 显式 5433，勿用默认 5432
_PG_DB = "agent_memory"


def _memory_db_available() -> bool:
    try:
        import psycopg
        with psycopg.connect(
            host=_PG_HOST, port=_PG_PORT, dbname=_PG_DB,
            user="postgres", password=os.getenv("PGPASSWORD", ""),
            connect_timeout=3,
        ) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _memory_db_available(), reason="本地 agent_memory 库不可达")


def _conn():
    import psycopg
    return psycopg.connect(
        host=_PG_HOST, port=_PG_PORT, dbname=_PG_DB,
        user="postgres", password=os.getenv("PGPASSWORD", ""),
        connect_timeout=3,
    )


class TestSummaryVersionColumn:
    def test_column_exists_with_default(self):
        """044 已执行：列存在、NOT NULL、DEFAULT 0（B1 验收口径）。"""
        with _conn() as conn:
            row = conn.execute(
                "SELECT data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='chat_sessions' "
                "AND column_name='summary_version'").fetchone()
        assert row is not None, "summary_version 未落地——migration 044 未执行？"
        data_type, is_nullable, default = row
        assert data_type == "integer"
        assert is_nullable == "NO"
        assert default and "0" in default

    def test_migration_file_is_idempotent(self):
        """重复执行 044 全文不报错（ADD COLUMN IF NOT EXISTS）。"""
        from pathlib import Path

        sql_path = (Path(__file__).resolve().parents[2]
                    / "sql" / "migrations"
                    / "044_chat_sessions_summary_version.sql")
        sql = sql_path.read_text(encoding="utf-8")
        with _conn() as conn:
            conn.execute(sql)  # 第二次执行 = 幂等 no-op
            conn.commit()
            row = conn.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='chat_sessions' "
                "AND column_name='summary_version'").fetchone()
        assert row[0] == 1


class TestCasSqlPathWorkable:
    """经 SQLAlchemy 引擎会吃宿主机默认连接（localhost:5432 双库坑）——
    这里用 psycopg 直连（显式 5433）+ raw_connection 适配器，测的是
    真实 SQL 与真实库，与进程级 config/env 解耦。"""

    @staticmethod
    def _store_with_real_pg(session_id: str):
        from backend.context_budget.auto_compact import SyncMemorySummaryStore

        class _EngineAdapter:
            def raw_connection(self):
                return _conn()

        store = SyncMemorySummaryStore(session_id)
        store._engine = staticmethod(lambda: _EngineAdapter())
        return store

    def test_get_summary_state_read_side_works(self):
        """L5 读侧探针：044 落地前此调用抛 UndefinedColumn（生产事故形态）。"""
        state = self._store_with_real_pg(
            "migration-044-probe").get_summary_state()
        assert state == {"summary": None, "through_id": None,
                         "token_count": None, "version": None}

    def test_cas_write_nonexistent_session_returns_false(self):
        """CAS 写对不存在 session：rowcount=0 → False（零写入，无脏数据）。"""
        ok = self._store_with_real_pg(
            "migration-044-probe-missing").save_summary_state(
            summary="x", through_id=1, token_count=1, expected_through=0)
        assert ok is False

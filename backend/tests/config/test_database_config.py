"""数据库配置的双库归属回归测试。"""
from __future__ import annotations

import importlib
import os


def test_memory_database_does_not_follow_legacy_pgdatabase(monkeypatch):
    """PGDATABASE 可供旧业务使用，不能将记忆库误指向 demo。"""
    import backend.config.database as database
    original_pgdatabase = os.getenv("PGDATABASE")
    original_memory_database = os.getenv("MEMORY_PGDATABASE")
    try:
        monkeypatch.setenv("PGDATABASE", "demo")
        monkeypatch.delenv("MEMORY_PGDATABASE", raising=False)
        database = importlib.reload(database)

        assert database.MEMORY_DB_CONFIG["dbname"] == "agent_memory"
        assert database.DB_CONFIG["dbname"] == "agent_memory"
    finally:
        # reload() 改的是模块级配置，不会由 monkeypatch 自动回滚；
        # 显式恢复环境后再 reload，避免后续 PG 集成测试误连默认库。
        if original_pgdatabase is None:
            monkeypatch.delenv("PGDATABASE", raising=False)
        else:
            monkeypatch.setenv("PGDATABASE", original_pgdatabase)
        if original_memory_database is None:
            monkeypatch.delenv("MEMORY_PGDATABASE", raising=False)
        else:
            monkeypatch.setenv("MEMORY_PGDATABASE", original_memory_database)
        importlib.reload(database)

"""数据库配置的双库归属回归测试。"""
from __future__ import annotations

import importlib


def test_memory_database_does_not_follow_legacy_pgdatabase(monkeypatch):
    """PGDATABASE 可供旧业务使用，不能将记忆库误指向 demo。"""
    monkeypatch.setenv("PGDATABASE", "demo")
    monkeypatch.delenv("MEMORY_PGDATABASE", raising=False)

    import backend.config.database as database

    database = importlib.reload(database)

    assert database.MEMORY_DB_CONFIG["dbname"] == "agent_memory"
    assert database.DB_CONFIG["dbname"] == "agent_memory"

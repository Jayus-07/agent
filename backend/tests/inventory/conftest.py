"""tests/inventory/conftest.py — 共享 fixtures

2026-09-17 SQLite 轨删除：InventoryStore 唯一实现为 PostgresInventoryStore。
测试隔离统一走 pgtest_biz_ 前缀表（见 backend/tests/fixtures/pg_env.py），
每个用例独立构造实例（壳类 __new__ 返回新 PG 实例，无单例污染）。
"""
from __future__ import annotations

import pytest

from backend.orchestration.inventory import InventoryStore
from backend.tests.fixtures.pg_env import (  # noqa: F401
    pg_clean_tables,
    pg_iso_env,
)


@pytest.fixture
def fresh_store(pg_clean_tables):
    """每个测试用独立 PG 测试表（pgtest_biz_ 前缀，用例前后清空）"""
    return InventoryStore()

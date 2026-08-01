"""tests/inventory/conftest.py — 共享 fixtures

注意：InventoryStore 默认 db_path 是 data/inventory_alerts.db（单例）
如果测试直接用 get_inventory_store() 会跨用例污染
→ 每个测试用 tmp_path 单独创建新 store
"""
from __future__ import annotations

import pytest

from backend.orchestration.inventory import InventoryStore


@pytest.fixture
def fresh_store(tmp_path):
    """每个测试用 tmp_path 创建新 InventoryStore（避免单例污染）"""
    return InventoryStore(db_path=str(tmp_path / "inv.db"))
"""tests/orchestration/inventory/test_product_id_text_binding.py — product_id 文本绑定契约

背景（2026-10-08 ECS 走查实测）：inventory_alert_cases.product_id 列是
TEXT（迁移 016），而库存扫描侧传入 int——两处 `text = integer` /
`"1" == 1` 断点让工作流在 evaluate_thresholds 步骤必炸、sku 规则静默失效。
契约：所有 product_id 比较/绑定一律文本化。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from backend.orchestration.inventory.store import InventoryStore
from backend.orchestration.inventory.store_pg import PostgresInventoryStore


def _bare_store() -> PostgresInventoryStore:
    """跳过 __init__（不连 PG），手工补齐实例属性。"""
    store = PostgresInventoryStore.__new__(PostgresInventoryStore)
    store._db_path = "data/inventory_alerts.db"
    store._lock = threading.RLock()
    return store


class TestFindThresholdTextCompare:
    def test_sku_rule_matches_int_product_id(self):
        store = InventoryStore.__new__(InventoryStore)
        rules = [
            {"rule_type": "sku", "product_id": "1", "min_qty": 30},
            {"rule_type": "global", "product_id": None, "min_qty": 50},
        ]
        store.list_thresholds = lambda enabled_only=True: rules
        # PG 回来的 product_id 是 TEXT，扫描侧传入 int——必须命中 sku 规则
        hit = InventoryStore.find_threshold(store, product_id=1)
        assert hit is not None and hit["rule_type"] == "sku"

    def test_global_fallback_when_no_sku_match(self):
        store = InventoryStore.__new__(InventoryStore)
        rules = [{"rule_type": "sku", "product_id": "999", "min_qty": 30},
                 {"rule_type": "global", "product_id": None, "min_qty": 50}]
        store.list_thresholds = lambda enabled_only=True: rules
        hit = InventoryStore.find_threshold(store, product_id=1)
        assert hit["rule_type"] == "global"


class TestCaseBindingsAreText:
    def _capture_store(self, monkeypatch, fetchone=None, rows=None):
        store = _bare_store()
        captured: dict = {}

        def fake_exec(conn, sql, params=()):
            captured["sql"] = sql
            captured["params"] = params
            # SimpleNamespace + lambda：函数闭包链可靠（嵌套类体不吃外层函数作用域）
            return SimpleNamespace(fetchone=lambda: fetchone,
                                   fetchall=lambda: list(rows or []))

        monkeypatch.setattr(store, "_exec", fake_exec)
        monkeypatch.setattr(
            store, "_insert_returning_id", lambda conn, sql, params: 42)
        import contextlib

        @contextlib.contextmanager
        def fake_conn():
            yield SimpleNamespace()

        monkeypatch.setattr(store, "_conn", fake_conn)
        return store, captured

    def test_upsert_case_binds_text_pid(self, monkeypatch):
        store, captured = self._capture_store(monkeypatch, fetchone=None)
        inserts: list = []
        # 覆盖 _capture_store 的哑桩：INSERT 走 _insert_returning_id，单独记录
        monkeypatch.setattr(
            store, "_insert_returning_id",
            lambda conn, sql, params: inserts.append((sql, params)) or 42)
        store.upsert_case({"product_id": 3, "current_level": "warning"})
        # SELECT 守卫查询与 INSERT 的 product_id 都必须是文本（列类型 TEXT）
        assert "WHERE product_id = %s" in captured["sql"]
        assert captured["params"] == ("3",)
        assert "INSERT INTO" in inserts[0][0]
        assert inserts[0][1][0] == "3"

    def test_get_cases_by_products_binds_text_list(self, monkeypatch):
        store, captured = self._capture_store(
            monkeypatch, rows=[{"product_id": "1", "status": "open"}])
        out = store.get_cases_by_products([1, 2])
        assert all(isinstance(p, str) for p in captured["params"])
        assert captured["params"] == ("1", "2")
        assert "1" in out  # 返回键也是文本域

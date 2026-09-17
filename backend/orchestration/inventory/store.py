"""orchestration/inventory/store.py — 库存告警数据访问层（接口层，PG 实现）

设计：
- 4 张表：thresholds / cases / events / policies（PG 实现 store_pg.py）
- 模块级单例（get_inventory_store()）
- 所有时间戳 ISO 格式字符串（与 workflow_runs 保持一致）
- 2026-09-17 SQLite 轨已删除，唯一实现为 PostgresInventoryStore；
  本模块保留接口与共享匹配逻辑（find_threshold / find_matching_policies）。
"""
from __future__ import annotations

from typing import Any


class InventoryStore:
    """库存告警数据访问接口（4 张表共用 store；唯一实现：PostgresInventoryStore）"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.orchestration.inventory.store_pg import PostgresInventoryStore
        return super().__new__(PostgresInventoryStore)

    # ──────────── Thresholds ────────────

    def find_threshold(
        self,
        product_id: str | None = None,
        category: str | None = None,
    ) -> dict | None:
        """按优先级找最佳规则：sku > category > global

        优先：sku 匹配 → category 匹配 → global
        """
        rules = self.list_thresholds(enabled_only=True)
        # 1. sku 匹配
        if product_id:
            for r in rules:
                if r["rule_type"] == "sku" and r["product_id"] == product_id:
                    return r
        # 2. category 匹配
        if category:
            for r in rules:
                if r["rule_type"] == "category" and r["category"] == category:
                    return r
        # 3. global 兜底
        for r in rules:
            if r["rule_type"] == "global":
                return r
        return None

    # ──────────── Policies ────────────

    def find_matching_policies(
        self,
        alert_level: str,
        inventory_state: str,
        category: str | None = None,
    ) -> list[dict]:
        """多维 OR 匹配（决策 3 选 C）：命中所有满足条件的 Policy

        每个字段：相等 OR NULL（NULL=全部）
        """
        all_p = self.list_policies(enabled_only=True)
        matched = []
        for p in all_p:
            # alert_level: 相等 or NULL
            if p["alert_level"] is not None and p["alert_level"] != alert_level:
                continue
            # inventory_state: 相等 or NULL
            if p["inventory_state"] is not None and p["inventory_state"] != inventory_state:
                continue
            # category: 相等 or NULL
            if p["category"] is not None and p["category"] != category:
                continue
            matched.append(p)
        return matched


# ─────────────────────────────────────────────────────────────
# 模块级单例
# ─────────────────────────────────────────────────────────────

_store: InventoryStore | None = None


def get_inventory_store() -> InventoryStore:
    """获取 InventoryStore 单例（2026-09-17 SQLite 轨删除，直连 PG 实现）。"""
    global _store
    if _store is None:
        from backend.orchestration.inventory.store_pg import PostgresInventoryStore

        _store = PostgresInventoryStore()
    return _store

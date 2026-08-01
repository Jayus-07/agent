"""seed/demo/run.py — 演示数据导入脚本

运行：
    cd backend && python -m seed.demo.run
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

# 让脚本能 import backend
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND.parent))

from backend.seed.demo.data import (
    DEMO_SCENARIOS,
    POLICIES,
    PRODUCTS,
    THRESHOLDS,
    generate_sales_history,
)
from backend.shared.logger import logger


# ─────────────────────────────────────────────────────────────
# 数据存储位置（用后端 data/ 目录）
# ─────────────────────────────────────────────────────────────

def get_db_paths() -> dict[str, str]:
    """返回 demo 数据用到的所有 db 路径"""
    data_dir = _BACKEND / "data"
    data_dir.mkdir(exist_ok=True)
    return {
        # 库存阈值 + cases + events + policies（4 张表共用 1 个 db）
        "inventory": str(data_dir / "inventory_alerts.db"),
        # 销售历史 + 商品（Phase 3 demo 用，新 db）
        "sales": str(data_dir / "demo_sales.db"),
    }


# ─────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────

SALES_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id      TEXT PRIMARY KEY,
    product_name    TEXT NOT NULL,
    category        TEXT,
    supplier_grade  TEXT,
    current_qty     INTEGER,
    unit_price      REAL
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    product_id      TEXT NOT NULL,
    qty             INTEGER NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
CREATE INDEX IF NOT EXISTS idx_sales_product_date ON sales(product_id, date);
"""


# ─────────────────────────────────────────────────────────────
# Importers
# ─────────────────────────────────────────────────────────────

def import_products_and_sales(sales_db: str) -> dict[str, Any]:
    """导入商品 + 销售历史到 demo_sales.db"""
    conn = sqlite3.connect(sales_db)
    try:
        conn.executescript(SALES_SCHEMA)
        conn.commit()

        # 清空 + 重插
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM sales")

        for p in PRODUCTS:
            conn.execute(
                """INSERT INTO products
                   (product_id, product_name, category, supplier_grade, current_qty, unit_price)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (p["product_id"], p["product_name"], p["category"],
                 p["supplier_grade"], p["current_qty"], p["unit_price"]),
            )

        sales = generate_sales_history()
        for s in sales:
            conn.execute(
                "INSERT INTO sales (date, product_id, qty) VALUES (?, ?, ?)",
                (s["date"], s["product_id"], s["qty"]),
            )
        conn.commit()
        return {
            "products": len(PRODUCTS),
            "sales_records": len(sales),
        }
    finally:
        conn.close()


def import_thresholds_and_policies(inv_db: str) -> dict[str, Any]:
    """导入阈值规则 + 通知策略到 inventory_alerts.db"""
    # 用 InventoryStore（统一 API）
    from backend.orchestration.inventory import InventoryStore

    store = InventoryStore(db_path=inv_db)
    # 清空现有的 enabled 规则（避免重复导入）
    for t in store.list_thresholds(enabled_only=False):
        if t.get("rule_type") in ("sku", "category", "global"):
            store.save_threshold({**t, "enabled": False})
    for p in store.list_policies(enabled_only=False):
        store.save_policy({**p, "enabled": 0})

    # 重新导入
    threshold_ids = []
    for t in THRESHOLDS:
        tid = store.save_threshold(t)
        threshold_ids.append(tid)
    policy_ids = []
    for p in POLICIES:
        pid = store.save_policy(p)
        policy_ids.append(pid)
    return {
        "thresholds": len(threshold_ids),
        "policies": len(policy_ids),
    }


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def run_seed(verbose: bool = True) -> dict[str, Any]:
    """导入所有 demo 数据"""
    paths = get_db_paths()

    if verbose:
        logger.info("=" * 60)
        logger.info("Phase 3 演示数据导入")
        logger.info("=" * 60)
        logger.info(f"商品数量: {len(PRODUCTS)}")
        logger.info(f"阈值规则: {len(THRESHOLDS)}")
        logger.info(f"通知策略: {len(POLICIES)}")
        logger.info(f"演示场景: {len(DEMO_SCENARIOS)}")

    # 1. 商品 + 销售
    result1 = import_products_and_sales(paths["sales"])
    if verbose:
        logger.info(f"[1/2] 商品 + 销售: {result1}")

    # 2. 阈值 + 通知
    result2 = import_thresholds_and_policies(paths["inventory"])
    if verbose:
        logger.info(f"[2/2] 阈值 + 通知: {result2}")

    if verbose:
        logger.info("=" * 60)
        logger.info("导入完成")
        logger.info(f"  - data/demo_sales.db")
        logger.info(f"  - data/inventory_alerts.db")
        logger.info("=" * 60)

    return {**result1, **result2}


if __name__ == "__main__":
    run_seed()
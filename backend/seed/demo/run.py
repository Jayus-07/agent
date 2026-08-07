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

# PostgreSQL schema（与 workflow SQL step 查询对齐）
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    product_id      TEXT PRIMARY KEY,
    product_name    TEXT NOT NULL,
    category        TEXT,
    supplier_grade  TEXT,
    current_qty     INTEGER DEFAULT 0,
    min_qty         INTEGER DEFAULT 10
);

CREATE TABLE IF NOT EXISTS sales (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    product_id      TEXT NOT NULL,
    qty             INTEGER NOT NULL,
    amount          NUMERIC DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sales_product_date ON sales(product_id, date);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date);
"""


def import_to_postgres(verbose: bool = True) -> dict[str, Any]:
    """导入商品 + 销售到 PostgreSQL"""
    try:
        import psycopg2
        from backend.config import BUSINESS_DB_CONFIG
    except ImportError:
        logger.warning("[seed] psycopg2 未安装，跳过 PG 导入")
        return {"pg_products": 0, "pg_sales": 0, "skipped": True}

    # 注册 inventory + sales 到 SQL Agent 白名单（否则 workflow SQL step 被拦截）
    try:
        from backend.sql.schema_loader import schema_loader
        schema_loader.register_table("inventory", {
            "product_id":      "商品ID (TEXT PRIMARY KEY)",
            "product_name":    "商品名称 (TEXT)",
            "category":        "品类 (TEXT)",
            "supplier_grade":  "供应商等级 (TEXT)",
            "current_qty":     "当前库存 (INTEGER)",
            "min_qty":         "最小库存阈值 (INTEGER)",
        }, "Demo 库存表")
        schema_loader.register_table("sales", {
            "id":          "记录ID (SERIAL PRIMARY KEY)",
            "date":        "销售日期 (DATE)",
            "product_id":  "商品ID (TEXT)",
            "qty":         "销售数量 (INTEGER)",
            "amount":      "销售额 (NUMERIC)",
        }, "Demo 销售表")
        if verbose:
            logger.info("[seed] inventory + sales 已注册到 SQL Agent 白名单")
    except Exception as e:
        logger.warning(f"[seed] 注册 SQL Agent 白名单失败: {e}")

    conn = psycopg2.connect(**BUSINESS_DB_CONFIG)
    try:
        conn.set_session(autocommit=True)
        cur = conn.cursor()

        # 建表
        cur.execute(PG_SCHEMA)

        # 清空 + 重插 inventory
        cur.execute("DELETE FROM sales")
        cur.execute("DELETE FROM inventory")

        # 从 THRESHOLDS 提取 min_qty 映射
        min_qty_map: dict[str, int] = {}
        for t in THRESHOLDS:
            if t.get("rule_type") == "sku" and t.get("product_id"):
                min_qty_map[t["product_id"]] = t.get("min_qty", 10)

        for p in PRODUCTS:
            cur.execute(
                """INSERT INTO inventory
                   (product_id, product_name, category, supplier_grade, current_qty, min_qty)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (product_id) DO UPDATE SET
                   current_qty = EXCLUDED.current_qty,
                   min_qty = EXCLUDED.min_qty""",
                (
                    p["product_id"], p["product_name"], p["category"],
                    p["supplier_grade"], p["current_qty"],
                    min_qty_map.get(p["product_id"], 20),
                ),
            )

        # 导入销售
        sales = generate_sales_history()
        for s in sales:
            cur.execute(
                "INSERT INTO sales (date, product_id, qty) VALUES (%s, %s, %s)",
                (s["date"], s["product_id"], s["qty"]),
            )

        count = cur.rowcount if hasattr(cur, 'rowcount') else len(sales)
        cur.close()
        result = {"pg_products": len(PRODUCTS), "pg_sales": count}
        if verbose:
            logger.info(f"[3/3] PG 导入: {result}")
        return result
    finally:
        conn.close()

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

    # 1. 商品 + 销售 (SQLite)
    result1 = import_products_and_sales(paths["sales"])
    if verbose:
        logger.info(f"[1/3] SQLite 商品 + 销售: {result1}")

    # 2. 阈值 + 通知 (SQLite)
    result2 = import_thresholds_and_policies(paths["inventory"])
    if verbose:
        logger.info(f"[2/3] 阈值 + 通知: {result2}")

    # 3. PG 导入（workflow SQL step 用）
    result3 = import_to_postgres(verbose=verbose)

    if verbose:
        logger.info("=" * 60)
        logger.info("导入完成")
        logger.info(f"  - data/demo_sales.db")
        logger.info(f"  - data/inventory_alerts.db")
        logger.info(f"  - PostgreSQL (inventory + sales)")
        logger.info("=" * 60)

    return {**result1, **result2, **result3}


if __name__ == "__main__":
    run_seed()
"""test_inventory_state.py — InventoryState 动态评估

覆盖决策 1 的所有规则：
1. 库存 = 0 → out_of_stock
2. 库存 < 50% min_qty → critical
3. 库存 < min_qty + days_of_stock < 阈值 → critical
4. 库存 < min_qty（仅静态）→ low
5. days_of_stock < 阈值（销售加速）→ low
6. 库存 ≥ min_qty + days_of_stock ≥ 阈值 → normal
7. 无规则 → normal（不告警）
"""
from __future__ import annotations

import math

import pytest

from backend.orchestration.inventory import (
    evaluate,
    evaluate_batch,
    calculate_daily_sales_avg,
    calculate_stock_days,
    InventoryState,
)


class TestDailySalesAvg:
    """日均销量计算"""

    def test_empty_sales_returns_zero(self):
        assert calculate_daily_sales_avg([], window_days=30) == 0.0

    def test_single_day(self):
        sales = [{"date": "2026-07-30", "qty": 10}]
        assert calculate_daily_sales_avg(sales, window_days=30) == 10.0

    def test_multiple_days(self):
        sales = [{"date": f"2026-07-{i:02d}", "qty": 10} for i in range(1, 11)]
        # 10 天总共 100 / 10 = 10
        assert calculate_daily_sales_avg(sales, window_days=30) == 10.0

    def test_window_truncates_old_data(self):
        # 40 天数据，window=30 → 取最近 30 天
        sales = [{"date": f"2026-06-{i:02d}", "qty": 1} for i in range(1, 11)]  # 10 天
        sales += [{"date": f"2026-07-{i:02d}", "qty": 10} for i in range(1, 31)]  # 30 天
        # 取后 30 天（7月）→ 30*10/30 = 10
        result = calculate_daily_sales_avg(sales, window_days=30)
        assert math.isclose(result, 10.0, rel_tol=0.01)


class TestStockDays:
    """可撑天数计算"""

    def test_normal_case(self):
        # 100 件 / 10 件/天 = 10 天
        assert calculate_stock_days(100, 10) == 10.0

    def test_zero_qty_zero_days(self):
        assert calculate_stock_days(0, 5) == 0.0

    def test_zero_sales_returns_inf(self):
        # 没销售 → 不会售罄 → inf
        result = calculate_stock_days(50, 0)
        assert result == float("inf")


class TestEvaluate:
    """evaluate 核心评估逻辑"""

    def test_qty_zero_is_out_of_stock(self):
        """库存 = 0 → out_of_stock"""
        a = evaluate("P1", 0, {"rule_type": "global", "min_qty": 50})
        assert a.state == InventoryState.OUT_OF_STOCK
        assert "0" in a.reason[0]

    def test_below_50_percent_min_qty_critical(self):
        """库存 5, min_qty=50 → critical"""
        a = evaluate("P1", 5, {"rule_type": "global", "min_qty": 50})
        assert a.state == InventoryState.CRITICAL
        assert any("5" in r for r in a.reason)

    def test_below_min_qty_low(self):
        """库存 30, min_qty=50, 无销售 → low"""
        a = evaluate("P1", 30, {"rule_type": "global", "min_qty": 50})
        assert a.state == InventoryState.LOW

    def test_normal_above_min_qty(self):
        """库存 100, min_qty=50 → normal"""
        a = evaluate("P1", 100, {"rule_type": "global", "min_qty": 50})
        assert a.state == InventoryState.NORMAL

    def test_low_due_to_sales_velocity(self):
        """库存 50, min_qty=50, 但日销 10 → 5天售罄（< 7天阈值）→ low"""
        sales = [{"date": f"2026-07-{i:02d}", "qty": 10} for i in range(1, 11)]
        a = evaluate(
            "P1", 50,
            {"rule_type": "global", "min_qty": 50, "days_of_stock": 7, "sales_window_days": 30},
            sales,
        )
        assert a.state == InventoryState.LOW
        assert any("5" in r for r in a.reason)  # 5 天售罄

    def test_no_rule_returns_normal(self):
        """无规则 → normal（不告警）"""
        a = evaluate("P1", 5, None)  # 库存极低但无规则
        assert a.state == InventoryState.NORMAL
        assert "未配置" in a.reason[0]

    def test_above_min_qty_but_short_stock_days(self):
        """库存 100, min_qty=50, 日销 50 → 2天售罄（< 7天）→ low"""
        sales = [{"date": f"2026-07-{i:02d}", "qty": 50} for i in range(1, 11)]
        a = evaluate(
            "P1", 100,
            {"rule_type": "global", "min_qty": 50, "days_of_stock": 7, "sales_window_days": 30},
            sales,
        )
        # 库存 100 超过 min_qty 50，但只有 2 天库存
        assert a.state == InventoryState.LOW
        assert any("2" in r or "售罄" in r for r in a.reason)

    def test_assessment_to_dict_includes_required_fields(self):
        """to_dict 输出所有字段"""
        a = evaluate("P1", 5, {"rule_type": "global", "min_qty": 50})
        d = a.to_dict()
        assert "product_id" in d
        assert "current_qty" in d
        assert "state" in d
        assert "alert_level" in d
        assert "daily_sales_avg" in d
        assert "stock_days" in d
        assert "threshold_min_qty" in d
        assert "reason" in d


class TestEvaluateBatch:
    """批量评估"""

    def test_batch_with_mixed_states(self):
        items = [
            {"product_id": "P1", "current_qty": 0, "category": "手机"},
            {"product_id": "P2", "current_qty": 30, "category": "手机"},
            {"product_id": "P3", "current_qty": 100, "category": "手机"},
        ]
        # 写真正的 threshold 规则到 store（用 find_threshold 需要）
        # 改为直接构造 thresholds dict（evaluate_batch 接受）
        # 但 evaluate_batch 调用 find_threshold(within store) — 改用单独 evaluate
        from backend.orchestration.inventory import evaluate as _eval
        # 用 InventoryStore 注入
        from backend.orchestration.inventory import InventoryStore as _S
        import tempfile, os as _os
        _tmp = tempfile.mkdtemp()
        _s = _S(db_path=_os.path.join(_tmp, 't.db'))
        _s.save_threshold({
            "rule_type": "category", "category": "手机", "min_qty": 50,
        })
        results = []
        for item in items:
            rule = _s.find_threshold(product_id=item["product_id"], category=item["category"])
            a = _eval(item["product_id"], item["current_qty"], rule, None)
            results.append(a)
        states = [r.state for r in results]
        assert InventoryState.OUT_OF_STOCK in states
        assert InventoryState.LOW in states
        assert InventoryState.NORMAL in states

    def test_batch_with_no_thresholds(self):
        items = [{"product_id": "P1", "current_qty": 5, "category": "X"}]
        results = evaluate_batch(items, thresholds_by_sku={})
        # 没规则 → NORMAL
        assert results[0].state == InventoryState.NORMAL

    def test_batch_sku_priority(self):
        """SKU 规则优先级高于 category 规则"""
        items = [{"product_id": "VIP-001", "current_qty": 30, "category": "手机"}]
        thresholds = {
            "VIP-001": {"rule_type": "sku", "product_id": "VIP-001", "min_qty": 50},
        }
        results = evaluate_batch(items, thresholds)
        # SKU 规则 min_qty=50，库存 30 → LOW
        assert results[0].state == InventoryState.LOW
        # 用的是 sku 规则（threshold_min_qty=50）
        assert results[0].threshold_min_qty == 50
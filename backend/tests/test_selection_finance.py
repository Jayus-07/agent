"""finance 规则计算 + 有界优化循环测试"""
import pytest

from backend.selection_decision.finance import compute_model, run_finance


BASE_PARAMS = {
    "sell_price": 129.0,
    "unit_cost": 45.0,
    "platform_fee_rate": 0.05,
    "shipping_cost": 6.0,
    "marketing_cost": 10.0,
    "monthly_fixed_cost": 3000.0,
    "min_margin_rate": 0.25,
    "initial_inventory": 100,
    "buffer_rate": 0.15,
}


def test_compute_model_profitable():
    m = compute_model(BASE_PARAMS)
    # net = 129*0.95=122.55; margin = 122.55-45-6-10 = 61.55
    assert m["unit_margin"] == pytest.approx(61.55)
    assert m["margin_rate"] == pytest.approx(61.55 / 129)
    # break_even = ceil(3000/61.55) = 49
    assert m["break_even_units"] == 49
    assert m["first_batch_investment"] == pytest.approx(100 * (45 + 6))
    assert m["risk_buffer"] == pytest.approx(5100 * 0.15)


def test_compute_model_negative_margin():
    m = compute_model({**BASE_PARAMS, "sell_price": 40.0})
    assert m["unit_margin"] < 0
    assert m["break_even_units"] is None


def test_run_finance_pass_first_round():
    result = run_finance(BASE_PARAMS)
    assert result["verdict"] == "pass"
    assert len(result["rounds"]) == 1


def test_run_finance_fail_bounded_3_rounds():
    """明显亏损参数：每轮调价+降本后仍不达标，最多 3 轮后输出 fail"""
    result = run_finance({**BASE_PARAMS, "sell_price": 30.0, "unit_cost": 40.0})
    assert result["verdict"] == "fail"
    assert len(result["rounds"]) == 3
    assert len(result["suggestions"]) == 3


def test_run_finance_recovers_after_adjustment():
    """首轮不达标、调价+降本后达标 → pass 且 rounds>1"""
    # 首轮利润率约 14%，两轮优化后超过 25% 门槛
    result = run_finance({**BASE_PARAMS, "unit_cost": 88.0})
    assert result["verdict"] == "pass"
    assert len(result["rounds"]) >= 2


def test_validation_rejects_bad_params():
    with pytest.raises(ValueError):
        run_finance({**BASE_PARAMS, "sell_price": 0})
    with pytest.raises(ValueError):
        run_finance({**BASE_PARAMS, "unit_cost": -1})

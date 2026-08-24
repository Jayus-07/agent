"""report 组装测试"""
from backend.selection_decision.report import build_report

OUTPUTS = {
    "competitor_data": {"count": 5},
    "market_assess": {"verdict": "go", "metrics": {"candidate_count": 5,
                       "price_min": 59.0, "price_max": 199.0, "total_reviews": 120000},
                       "data_gaps": ["市场体量无免费数据源"]},
    "differentiation": {"verdict": "go", "gaps": ["续航虚标"], "reason": "痛点集中"},
    "finance_model": {"verdict": "pass",
                      "final_model": {"unit_margin": 61.55, "margin_rate": 0.4771,
                                      "break_even_units": 49, "risk_buffer": 765.0},
                      "suggestions": []},
    "review_panel": {"verdict": "pass", "go_count": 6, "size": 7, "avg_score": 78.2,
                     "votes": [{"role": "风控官", "score": 80, "verdict": "go",
                                "reason": "风险可控"}]},
}


def test_go_report_contains_key_sections():
    md = build_report({"category": "蓝牙耳机", "platforms": ["jd", "amazon"]},
                      OUTPUTS, verdict="go", failed_gates=[])
    assert "# 选品决策报告" in md
    assert "🚀 Go" in md
    assert "蓝牙耳机" in md
    assert "续航虚标" in md
    assert "61.55" in md          # 财务数字可溯源
    assert "风控官" in md
    assert "市场体量无免费数据源" in md  # 数据缺口声明


def test_no_go_report_lists_failed_gates():
    md = build_report({"category": "x", "platforms": []}, OUTPUTS,
                      verdict="no_go", failed_gates=["财务测算", "评审团"])
    assert "❌ No-Go" in md
    assert "财务测算" in md and "评审团" in md


def test_skipped_steps_handled():
    """被 run_if 跳过的 step 输出为 {"skipped": True}，报告不应崩溃"""
    outputs = dict(OUTPUTS)
    outputs["finance_model"] = {"skipped": True, "reason": "run_if 条件不满足"}
    outputs["review_panel"] = {"skipped": True, "reason": "run_if 条件不满足"}
    md = build_report({"category": "x", "platforms": []}, outputs,
                      verdict="no_go", failed_gates=["差异化分析"])
    assert "未执行" in md

"""report 组装测试（批次3 v2：证据评估与门控分离后的报告结构）"""
from backend.selection_decision.report import build_report

OUTPUTS = {
    "competitor_data": {"count": 5},
    "market_evidence_assess": {"evidence_verdict": "sufficient",
                               "data_gaps": ["市场体量无免费数据源"]},
    "selection_decision_gate": {"verdict": "go", "evidence_verdict": "sufficient",
                                "blocked_by_evidence": False,
                                "recommendation_cap": None, "reason": "",
                                "metrics": {"candidate_count": 5,
                                            "price_min": 59.0, "price_max": 199.0,
                                            "total_reviews": 120000}},
    "differentiation": {"verdict": "go", "gaps": ["续航虚标"], "reason": "痛点集中"},
    "finance_model": {"verdict": "pass",
                      "final_model": {"unit_margin": 61.55, "margin_rate": 0.4771,
                                      "break_even_units": 49, "risk_buffer": 765.0},
                      "suggestions": []},
    "review_panel": {"verdict": "pass", "go_count": 6, "size": 7, "avg_score": 78.2,
                     "votes": [{"role": "风控官", "score": 80, "verdict": "go",
                                "reason": "风险可控"}]},
    "review_pain": {"pain_points": ["续航虚标", "佩戴不适"], "source": "inferred"},
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
    assert "充分" in md            # 证据资格（批次3）
    assert "建议监控关键词" in md   # 痛点 → 监控关键词建议（批次3）


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
    assert "run_if 条件不满足" in md  # M2：渲染真实 reason


def test_malformed_none_fields_not_crash():
    """I1：metrics/final_model/rounds 显式 None 时不崩溃"""
    outputs = dict(OUTPUTS)
    outputs["selection_decision_gate"] = {"verdict": "go", "metrics": None}
    outputs["finance_model"] = {"verdict": "fail", "final_model": None,
                                "rounds": None}
    md = build_report({"category": "x", "platforms": []}, outputs,
                      verdict="no_go", failed_gates=["finance"])
    assert "证据资格" in md and "财务测算" in md


def test_failed_gates_key_mapping():
    """failed_gates 传 step key，经 GATE_LABELS 映射为中文"""
    md = build_report({"category": "x", "platforms": []}, OUTPUTS,
                      verdict="no_go", failed_gates=["finance", "panel"])
    assert "财务测算" in md and "评审团" in md


def test_missing_output_key_renders_not_executed():
    """M3：outputs 中键整体缺失时章节渲染「未执行」而非空白"""
    outputs = dict(OUTPUTS)
    del outputs["selection_decision_gate"]
    md = build_report({"category": "x", "platforms": []}, outputs,
                      verdict="no_go", failed_gates=["market"])
    assert "未执行" in md


def test_insufficient_evidence_renders_undecidable():
    """批次3：insufficient 证据 → 门控结论渲染「证据不足，无法决策」"""
    outputs = dict(OUTPUTS)
    outputs["market_evidence_assess"] = {"evidence_verdict": "insufficient",
                                         "data_gaps": ["候选/评价/价格维度缺失"]}
    outputs["selection_decision_gate"] = {
        "verdict": "no_go", "evidence_verdict": "insufficient",
        "blocked_by_evidence": True, "recommendation_cap": "reject",
        "reason": "证据不足，无法决策（候选/评价/价格维度缺失）",
        "metrics": {"candidate_count": 1, "total_reviews": 0}}
    md = build_report({"category": "x", "platforms": []}, outputs,
                      verdict="no_go", failed_gates=["market"])
    assert "证据不足，无法决策" in md
    assert "不足" in md


def test_cautious_recommendation_headline():
    """批次3：partial 证据下 go 报告标题应为「谨慎入场」"""
    outputs = dict(OUTPUTS)
    outputs["selection_decision_gate"] = dict(OUTPUTS["selection_decision_gate"],
                                              recommendation_cap="cautious")
    md = build_report({"category": "x", "platforms": []}, outputs,
                      verdict="go", failed_gates=[])
    assert "谨慎入场" in md


def test_panel_none_stats_render_dash():
    """M4：评审团统计字段显式 None 时渲染 '-'，go_count=0 是合法值需保留"""
    outputs = dict(OUTPUTS)
    outputs["review_panel"] = {"verdict": "fail", "go_count": None,
                               "size": None, "avg_score": None, "votes": []}
    md = build_report({"category": "x", "platforms": []}, outputs,
                      verdict="no_go", failed_gates=["panel"])
    assert "None/None" not in md and "均分 None" not in md
    outputs["review_panel"] = {"verdict": "fail", "go_count": 0, "size": 7,
                               "avg_score": 40.0, "votes": []}
    md = build_report({"category": "x", "platforms": []}, outputs,
                      verdict="no_go", failed_gates=["panel"])
    assert "0/7" in md

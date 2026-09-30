"""report 组装测试（批次3 v2：证据评估与门控分离后的报告结构）"""
import ast
from pathlib import Path

from backend.orchestration.workflows.selection_decision import gate_display_names
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
                      verdict="no_go", failed_gates=["finance"],
                      gate_labels={"finance": "财务测算"})
    assert "证据资格" in md and "财务测算" in md


def test_failed_gates_key_mapping():
    """failed_gates 传门控 key，显示名由 workflow 从 @step(name=) 派生后传入"""
    labels = gate_display_names()
    md = build_report({"category": "x", "platforms": []}, OUTPUTS,
                      verdict="no_go", failed_gates=["finance", "panel"],
                      gate_labels=labels)
    assert labels["finance"] in md and labels["panel"] in md
    # 字面量锁：派生值必须就是当前 step 声明的文案（两边一起改错也会被咬住）
    assert "财务测算" in md and "评审团" in md


def test_gate_labels_derived_from_step_declarations():
    """门控显示名唯一事实源 = 各 step 的 @step(name=)。

    旧实现是 report.py 自持一份 GATE_LABELS，已与 step 声明漂移
    （「证据评估与市场门控」vs「市场门控」、「AI 评审团」vs「AI评审团」）。
    """
    from backend.orchestration.workflow.meta import get_step_config
    from backend.orchestration.workflows.selection_decision import (
        SelectionDecision,
        _GATE_CHECKS,
        gate_display_names,
    )

    labels = gate_display_names()
    assert set(labels) == set(_GATE_CHECKS), "门控 key 集合与报告渲染口径不一致"

    for key, (method_name, _field, _ok) in _GATE_CHECKS.items():
        cfg = get_step_config(getattr(SelectionDecision, method_name))
        assert cfg is not None, f"门控 {key} 指向的 step {method_name} 没有 @step 声明"
        assert labels[key] == cfg.display_name, (
            f"门控 {key} 显示名 {labels[key]!r} != step {method_name} 声明 {cfg.display_name!r}"
        )

    # 锁字面量（防 M8 那种「两边同源所以恒真」的假测试）
    assert labels == {
        "market": "市场门控",
        "differentiation": "差异化分析",
        "finance": "财务测算",
        "panel": "AI评审团",
    }


def test_report_module_holds_no_gate_wording():
    """防回退：report.py 不得再自持门控文案（唯一出口是调用方传入的 gate_labels）。"""
    src = (Path(__file__).resolve().parent.parent
           / "selection_decision" / "report.py").read_text(encoding="utf-8")
    # 取字符串常量而非扫文本：注释/文档字符串里提旧名（说明病史）是允许的
    consts = [n.value for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    for stale in ("证据评估与市场门控", "市场门控", "差异化分析",
                  "财务测算", "AI 评审团", "AI评审团"):
        assert stale not in consts, f"report.py 回退到自持门控文案: {stale}"


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

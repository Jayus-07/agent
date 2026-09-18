"""test_metadata_baseline_eval.py — 评估器核心纯函数回归（阶段 1.2/1.3 脚手架）。

只测纯函数（P/R/F1、混淆、风险召回、golden 校验、预测回放），
不发起任何 LLM/DB 调用；强断言 + 手工核对期望值。
"""
import json

import pytest

from backend.eval.metadata_baseline.evaluate import (
    confusion,
    evaluate,
    latency_percentiles,
    load_jsonl,
    per_label_prf,
    risk_recall,
    run_file_predictions,
    validate_golden,
)

GOLD = ["legal", "legal", "faq", "faq", "policy", "policy", "policy", "general"]
PRED = ["legal", "policy", "faq", "faq", "policy", "legal", "policy", "general"]


class TestPerLabelPrf:
    def test_macro_f1_hand_checked(self):
        """手工核对：legal 1TP/1FP/1FN → F1=0.5；faq F1=1；
        policy 2TP（[4][6]）/1FP（[1] gold=legal）/1FN（[5]）→ P=R=F1=2/3；
        general F1=1。macro = (0.5+1+2/3+1)/4 ≈ 0.7917。"""
        r = per_label_prf(PRED, GOLD, ["legal", "faq", "policy", "general"])
        assert r["per_label"]["legal"] == {"precision": 0.5, "recall": 0.5, "f1": 0.5, "support": 2}
        assert r["per_label"]["faq"]["f1"] == 1.0
        assert r["per_label"]["policy"]["f1"] == pytest.approx(2 / 3, abs=1e-4)
        assert r["macro_f1"] == pytest.approx(0.7917, abs=1e-4)

    def test_unsupported_label_counts_zero_not_macro(self):
        """support=0 的标签 F1=0 且不进 macro 分母。"""
        r = per_label_prf(["a"], ["a"], ["a", "ghost"])
        assert r["per_label"]["ghost"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0}
        assert r["macro_f1"] == 1.0

    def test_all_wrong_macro_zero(self):
        assert per_label_prf(["x"], ["y"], ["x", "y"])["macro_f1"] == 0.0


class TestConfusion:
    def test_offdiagonal_pairs(self):
        c = confusion(PRED, GOLD, ["legal", "faq", "policy", "general"])
        pairs = {(m["gold"], m["pred"]): m["count"] for m in c["misclassifications"]}
        assert pairs == {("legal", "policy"): 1, ("policy", "legal"): 1}


class TestRiskRecall:
    def test_recall_over_positives_only(self):
        rows = [
            {"risk_gold": ["违约"], "pred": {"risk": {"level": "high", "signals": []}}},
            {"risk_gold": ["隐私"], "pred": {"risk": {"level": "none", "signals": []}}},
            {"risk_gold": [], "pred": {"risk": {"level": "none", "signals": []}}},
            {"pred": {"risk": {"level": "none", "signals": []}}},
        ]
        r = risk_recall(rows)
        assert r == {"positives": 2, "hit": 1, "recall": 0.5}

    def test_no_positives_returns_none(self):
        assert risk_recall([{"pred": {"risk": {"level": "none", "signals": []}}}]) is None


class TestLatency:
    def test_p50_p95(self):
        rows = [{"pred": {"latency_ms": v}} for v in (10, 20, 30, 40, 100)]
        r = latency_percentiles(rows)
        assert r["n"] == 5 and r["p50"] == 30 and r["p95"] == 100

    def test_empty_returns_none(self):
        assert latency_percentiles([{}]) is None


class TestGoldenContract:
    def test_validate_rejects_missing_fields_and_bad_enum(self):
        with pytest.raises(ValueError, match="缺少必填字段"):
            validate_golden([{"id": "x", "text": "t"}])
        with pytest.raises(ValueError, match="schema 演进流程"):
            validate_golden([{"id": "x", "text": "t", "doc_type_gold": "made_up"}])

    def test_validate_accepts_sample_file(self):
        import pathlib
        sample = pathlib.Path(__file__).parents[2] / "eval" / "metadata_baseline" / "golden_sample.jsonl"
        rows = load_jsonl(sample)
        assert len(rows) == 3
        validate_golden(rows)


class TestReplay:
    def test_run_file_predictions_join_and_missing_check(self, tmp_path):
        pred_file = tmp_path / "preds.jsonl"
        pred_file.write_text(json.dumps(
            {"id": "a1", "pred": {"doc_type": "faq", "business_domain": "order"}}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        gold = [{"id": "a1", "text": "t", "doc_type_gold": "faq"}]
        merged = run_file_predictions(gold, pred_file)
        assert merged[0]["pred"]["doc_type"] == "faq"

        with pytest.raises(ValueError, match="缺少"):
            run_file_predictions([{"id": "zz", "text": "t", "doc_type_gold": "faq"}], pred_file)


class TestEvaluate:
    def test_end_to_end_report_shape(self):
        rows = [
            {"id": "1", "text": "t", "doc_type_gold": "legal", "domain_gold": "order",
             "risk_gold": ["违约责任"],
             "pred": {"doc_type": "legal", "business_domain": "order",
                      "risk": {"level": "high", "signals": []}, "latency_ms": 12}},
            {"id": "2", "text": "t", "doc_type_gold": "faq", "domain_gold": "general",
             "risk_gold": [],
             "pred": {"doc_type": "policy", "business_domain": "general",
                      "risk": {"level": "none", "signals": []}, "latency_ms": 8}},
        ]
        from backend.rag.preprocessing.metadata_schema import DOMAINS, DOC_TYPES
        report = {
            "doc_type": evaluate(rows, "doc_type_gold", list(DOC_TYPES)),
            "business_domain": evaluate(rows, "domain_gold", list(DOMAINS)),
            "risk": risk_recall(rows),
            "latency_ms": latency_percentiles(rows),
        }
        assert report["doc_type"]["accuracy"] == 0.5
        assert report["business_domain"]["accuracy"] == 1.0
        assert report["risk"]["recall"] == 1.0
        assert report["latency_ms"]["p95"] == 12

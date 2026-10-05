"""EvidenceGate 阈值实验台回归（C9 / T5.3）。

只测纯函数（lab 模块 + 合成分数向量），零网络零 PG：
  - gate_pass 对生产 gate 函数的直调语义（分离完美→放行；全低分→拒）；
  - 网格回放指标定义（假拒=应答被拒 / 漏拒=应拒放行）；
  - 推荐工作点规则（达标取最优 / 无达标如实上报）；
  - 定标集完整性（数量、类别平衡）。
"""

import json
from pathlib import Path

from backend.rag.evidence_gate import lab

_DATASET = Path(lab.__file__).resolve().parent.parent.parent / "evaluation" / "datasets" / "rag" / "gate_calibration.json"
_MANIFEST = _DATASET.parent / "gate_calibration_manifest.json"


class TestGatePass:
    def test_high_scores_pass(self):
        assert lab.gate_pass([0.8, 0.75, 0.7], vec_min_score=0.2,
                             min_top1=0.35, min_avg=0.25, min_gap=0.05) is True

    def test_all_low_scores_rejected(self):
        assert lab.gate_pass([0.25, 0.2, 0.15], vec_min_score=0.2,
                             min_top1=0.35, min_avg=0.25, min_gap=0.05) is False

    def test_empty_scores_rejected(self):
        assert lab.gate_pass([], vec_min_score=0.2,
                             min_top1=0.35, min_avg=0.25, min_gap=0.05) is False

    def test_threshold_tightening_flips_borderline(self):
        scores = [0.42, 0.30, 0.28]
        loose = lab.gate_pass(scores, vec_min_score=0.2,
                              min_top1=0.35, min_avg=0.25, min_gap=0.05)
        tight = lab.gate_pass(scores, vec_min_score=0.2,
                              min_top1=0.45, min_avg=0.25, min_gap=0.05)
        assert loose is True and tight is False

    def test_missing_entity_is_rejected_before_rerank_thresholds(self):
        scores = [0.8, 0.75, 0.7]
        assert lab.gate_pass(
            scores,
            vec_min_score=0.2,
            min_top1=0.35,
            min_avg=0.25,
            min_gap=0.05,
            missing_entities=["出口退税"],
        ) is False
        assert lab.gate_pass(
            scores,
            vec_min_score=0.2,
            min_top1=0.35,
            min_avg=0.25,
            min_gap=0.05,
            missing_entities=[],
        ) is True

    def test_query_scope_is_rejected_before_rerank_thresholds(self):
        assert lab.gate_pass(
            [0.9, 0.8, 0.7],
            vec_min_score=0.2,
            min_top1=0.35,
            min_avg=0.25,
            min_gap=0.05,
            question="怎么查别人的工资",
        ) is False
        assert lab.gate_pass(
            [0.9, 0.8, 0.7],
            vec_min_score=0.2,
            min_top1=0.35,
            min_avg=0.25,
            min_gap=0.05,
            question="客户申请退款的处理流程是什么",
        ) is True


class TestGridAndRecommend:
    def _dataset(self) -> list[dict]:
        # 合成：应答高分（应放行）、应拒低分（应拒）——完美可分
        return (
            [{"id": f"A{i}", "klass": "answerable", "scores": [0.8, 0.7, 0.6]} for i in range(10)]
            + [{"id": f"R{i}", "klass": "unanswerable", "scores": [0.2, 0.1]} for i in range(10)]
        )

    def test_perfect_separation_all_targets_pass(self):
        report = lab.run_grid(self._dataset(), {
            "vec_min_score": [0.2], "min_top1": [0.35], "min_avg": [0.25],
        })
        combo = report["combos"][0]
        assert combo.false_reject_n == 0 and combo.miss_reject_n == 0
        assert combo.pass_targets is True

    def test_false_reject_counts_answerable_rejected(self):
        # 应答分数压到阈值下 → 全部计假拒
        cases = [{"id": f"A{i}", "klass": "answerable", "scores": [0.3, 0.2]} for i in range(5)]
        report = lab.run_grid(cases, {
            "vec_min_score": [0.2], "min_top1": [0.35], "min_avg": [0.25],
        })
        assert report["combos"][0].false_reject_n == 5
        assert report["combos"][0].false_reject_rate == 1.0

    def test_recommend_picks_feasible_best(self):
        # 应答两类（高分/压线）+ 应拒全低 → 达标组合应被选出且取假拒最低
        cases = (
            [{"id": f"A{i}", "klass": "answerable", "scores": [0.8, 0.7]} for i in range(8)]
            + [{"id": f"B{i}", "klass": "answerable", "scores": [0.5, 0.3]} for i in range(2)]
            + [{"id": f"R{i}", "klass": "unanswerable", "scores": [0.15]} for i in range(10)]
        )
        report = lab.run_grid(cases, {
            "vec_min_score": [0.1, 0.2], "min_top1": [0.35], "min_avg": [0.25],
        })
        rec = lab.recommend(report)
        assert rec["feasible_n"] >= 1
        assert rec["best"]["false_reject"]["rate"] < 0.10
        assert rec["best"]["miss_reject"]["rate"] < 0.05

    def test_recommend_reports_infeasible_honestly(self):
        # 全部应答低分（定标集噪声场景）→ 无达标组合，如实上报
        cases = [{"id": f"A{i}", "klass": "answerable", "scores": [0.1]} for i in range(5)]
        report = lab.run_grid(cases, {
            "vec_min_score": [0.05, 0.2], "min_top1": [0.35], "min_avg": [0.25],
        })
        rec = lab.recommend(report)
        assert rec["feasible_n"] == 0
        assert "near_optimal" in rec


class TestDatasetIntegrity:
    def test_calibration_set_shape(self):
        data = json.loads(_DATASET.read_text(encoding="utf-8"))
        cases = data["cases"]
        n_a = sum(1 for c in cases if c["klass"] == "answerable")
        n_r = sum(1 for c in cases if c["klass"] == "unanswerable")
        assert n_a + n_r == len(cases), "类别只有 answerable/unanswerable 两种"
        # 2026-10-04.2 三轮前置①复核：应答面 18 条可疑复核后 89/100（11 条
        # 剔除是语料缺口与出题伪影的真实反映，不再硬编码 100/100 平衡）
        assert n_a >= 85 and n_r == 100
        ids = [c["id"] for c in cases]
        assert len(ids) == len(set(ids)), "案例 id 必须唯一"
        # 复核已剔除的条目不得回流（review_log 为准）
        removed = {j["id"] for j in data["review_log"]["judgments"]
                   if j["verdict"] == "removed"}
        assert removed and not (removed & set(ids)), "剔除条目必须离开 cases"

    def test_manifest_targets_anchored(self):
        manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        data = json.loads(_DATASET.read_text(encoding="utf-8"))
        # counts 必须与数据集实况一致（禁手抄，G2）
        n_a = sum(1 for c in data["cases"] if c["klass"] == "answerable")
        n_r = len(data["cases"]) - n_a
        assert manifest["counts"] == {"answerable": n_a, "unanswerable": n_r,
                                      "total": n_a + n_r}
        assert manifest["targets"] == {"false_reject_max": 0.10, "miss_reject_max": 0.05}
        # manifest sha256 必须与当前数据集文件一致（锁版）
        import hashlib
        assert manifest["sha256"] == hashlib.sha256(_DATASET.read_bytes()).hexdigest()

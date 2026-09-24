"""STOP F：Memory Golden 数据集结构回归门（离线，无网络/无 DB）。

锁定：
- 数据集完整性与标签合法性（60 例、7 类别、label 口径）；
- 硬门指标的冻结基线（cross-user/cross-tenant/expired/escape = 0）；
- threshold 定稿值来自真实 sweep（0.35，见 STOPF 报告）。

全量真实评测（真实 embedding + 真实 PG）走
scripts/eval_memory_golden.py（需网络与 DB，默认不在单测内执行）；
其真实运行证据冻结在 datasets/memory_golden_results.json。
"""
from __future__ import annotations

import json
from pathlib import Path

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "memory_golden.jsonl"
RESULTS = DATASET.with_name("memory_golden_results.json")

CATEGORIES = {"relevant", "irrelevant", "conflict", "isolation", "safety", "global", "zero"}


def _cases() -> list[dict]:
    return [json.loads(line) for line in
            DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_dataset_completeness_and_labels():
    cases = _cases()
    assert len(cases) >= 50, f"Golden 至少 50 例，当前 {len(cases)}"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "case id 必须唯一"
    for c in cases:
        assert c["category"] in CATEGORIES, f"{c['id']} 非法类别"
        assert isinstance(c.get("expect_zero"), bool)
        for seed in c["seeds"]:
            assert isinstance(seed.get("expect_retrieved"), bool), \
                f"{c['id']} 种子必须显式标注 expect_retrieved"
            assert seed.get("content"), f"{c['id']} 种子内容为空"
    # 类别覆盖（F1 硬条件）
    present = {c["category"] for c in cases}
    assert present == CATEGORIES, f"缺类别: {CATEGORIES - present}"
    # 正确结果 = 0 条 memory 必须是正式合法标签（F2）
    zero_cases = [c for c in cases if c["expect_zero"]]
    assert len(zero_cases) >= 5, "无记忆可召回的用例（期望 0 注入）必须成类"


def test_frozen_hard_gates_from_real_run():
    """硬门指标取自真实运行结果（不可拿平均值掩盖的指标）。"""
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    final = results["final"]
    assert final["expired_leakage"] == 0
    assert final["cross_user_leakage"] == 0
    assert final["cross_tenant_leakage"] == 0
    for s in results["structural"]:
        assert s["structural_escape"] == [], f"{s['case_id']} 结构逃逸"
    assert results["final"]["global_injected"] >= 5, "global 偏好应跨主题召回"


def test_frozen_threshold_baseline():
    """threshold=0.35 是真实 sweep 的定稿值（STOP F 冻结）。"""
    from backend.config import MEMORY_MIN_RELEVANCE_SCORE
    assert abs(float(MEMORY_MIN_RELEVANCE_SCORE) - 0.35) < 1e-9, (
        "MEMORY_MIN_RELEVANCE_SCORE 应为 STOP F 实证定标值 0.35")
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    sweep = {s["threshold"]: s for s in results["sweep"]}
    t35 = sweep[0.35]
    t45 = sweep[0.45]
    # 定标依据冻结：0.35 平台 recall 完胜 0.45，precision 损失可忽略
    assert t35["recall_macro"] > t45["recall_macro"]
    assert t35["recall_macro"] >= 0.99
    assert t45["recall_macro"] <= 0.85

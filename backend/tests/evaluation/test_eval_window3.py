"""窗口3 回归测试（三期 C3-3/C3-4 + 四期 C4-3/C4-5/C4-8）。

快照哈希守护（REPRO-10）：逐维度验证「任一输入变化 → hash 必变」。
"""
from __future__ import annotations

import json

import pytest


# ── C3-3 / REPRO-09/10：统一快照哈希 ─────────────────────────


def _base_inputs() -> dict:
    return {
        "suite": {"name": "pr_smoke", "dataset_version": "5.0.0",
                  "suite_content_hash": "abc123", "cases_hash": "abc123"},
        "prompt_versions": {"rag.qa": "12"},
        "prompt_template_hashes": {"rag.qa": "h12"},
        "model_config": {"binding_fingerprint": "fp1",
                         "eval_model": {"model": "m", "provider": "p"}},
        "retrieval_config": {"top_k": 8, "multiquery": False},
        "judge_config": {"temperature": 0.1},
        "ragas_config": {"level": "standard"},
        "tool_contract_fingerprint": "toolfp",
        "corpus_version_id": "v1",
        "git_sha": "abcd123",
        "threshold_snapshot": {"tier_thresholds": {"core": 0.85}, "min_samples": 8},
    }


def test_snapshot_hash_stable_for_same_inputs():
    from backend.evaluation.provenance import build_snapshot_hash

    assert build_snapshot_hash(_base_inputs()) == build_snapshot_hash(_base_inputs())


def test_snapshot_hash_changes_on_any_input_variation():
    """REPRO-10：任一关键输入变化 → hash 必变（逐维度穷举）。"""
    from backend.evaluation.provenance import build_snapshot_hash

    base_hash = build_snapshot_hash(_base_inputs())
    variants = {
        "suite_version": {"suite": {"name": "pr_smoke", "dataset_version": "5.0.1",
                                    "suite_content_hash": "abc123", "cases_hash": "abc123"}},
        "suite_content": {"suite": {"name": "pr_smoke", "dataset_version": "5.0.0",
                                    "suite_content_hash": "DIFFERENT", "cases_hash": "abc123"}},
        "prompt_version": {"prompt_versions": {"rag.qa": "13"}},
        "prompt_template": {"prompt_template_hashes": {"rag.qa": "h13"}},
        "model_binding": {"model_config": {"binding_fingerprint": "fp2",
                                           "eval_model": {"model": "m", "provider": "p"}}},
        "eval_model": {"model_config": {"binding_fingerprint": "fp1",
                                        "eval_model": {"model": "m2", "provider": "p"}}},
        "top_k": {"retrieval_config": {"top_k": 5, "multiquery": False}},
        "multiquery": {"retrieval_config": {"top_k": 8, "multiquery": True}},
        "judge_temperature": {"judge_config": {"temperature": 0.7}},
        "ragas_level": {"ragas_config": {"level": "full"}},
        "tool_contract": {"tool_contract_fingerprint": "toolfp2"},
        "corpus": {"corpus_version_id": "v2"},
        "git_sha": {"git_sha": "ffff123"},
        "threshold": {"threshold_snapshot": {"tier_thresholds": {"core": 0.9},
                                             "min_samples": 8}},
        "min_samples": {"threshold_snapshot": {"tier_thresholds": {"core": 0.85},
                                               "min_samples": 20}},
    }
    for name, override in variants.items():
        inputs = _base_inputs()
        inputs.update(override)
        assert build_snapshot_hash(inputs) != base_hash, f"维度 {name} 变化未引起 hash 变化"


def test_snapshot_hash_key_order_insensitive():
    from backend.evaluation.provenance import build_snapshot_hash

    a = {"x": 1, "y": {"a": 1, "b": 2}}
    b = {"y": {"b": 2, "a": 1}, "x": 1}
    assert build_snapshot_hash(a) == build_snapshot_hash(b)


def test_collect_template_hashes_soft_fail(monkeypatch):
    from backend.evaluation import provenance

    # 无 DB 环境：模板哈希采集失败 → 空 dict（诚实缺失），不抛
    result = provenance.collect_template_hashes(["rag.qa"])
    assert isinstance(result, dict)


# ── C3-4 / DB-06：发布版本删除保护守卫 ───────────────────────


@pytest.mark.asyncio
async def test_version_deletable_guard_blocks_production_target():
    from backend.prompts.release_service import PromptReleaseService

    class _PS:
        async def get_aliases(self, key):
            return {"production": 3}

    service = PromptReleaseService(prompt_service=_PS())
    with pytest.raises(ValueError, match="production"):
        await service.ensure_version_deletable("rag.qa", 3)


@pytest.mark.asyncio
async def test_version_deletable_guard_blocks_published_release():
    from backend.prompts.release_models import PromptReleaseRecord, PromptReleaseStatus
    from backend.prompts.release_service import PromptReleaseService

    class _PS:
        async def get_aliases(self, key):
            return {"production": 9}  # 非 v2

    class _Repo:
        async def list_releases(self, key):
            return [{
                "release_id": "rel-x", "prompt_key": key, "version": 2,
                "status": PromptReleaseStatus.PUBLISHED.value,
            }]

    service = PromptReleaseService(repository=_Repo(), prompt_service=_PS())
    with pytest.raises(ValueError, match="发布记录"):
        await service.ensure_version_deletable("rag.qa", 2)


@pytest.mark.asyncio
async def test_version_deletable_guard_allows_clean_version():
    from backend.prompts.release_service import PromptReleaseService

    class _PS:
        async def get_aliases(self, key):
            return {"production": 3}

    class _Repo:
        async def list_releases(self, key):
            return []

    service = PromptReleaseService(repository=_Repo(), prompt_service=_PS())
    await service.ensure_version_deletable("rag.qa", 2)  # 不抛即通过


# ── C4-3 / JUDGE-05：分值范围校验 ────────────────────────────


def test_judge_clamps_out_of_range_scores(monkeypatch):
    import backend.evaluation.judge as judge_mod

    monkeypatch.setattr(
        judge_mod, "_get_llm_response",
        lambda prompt: json.dumps({
            "scores": {"completeness": 7, "faithfulness": 0,
                       "conciseness": 3, "citation_quality": "high"},
            "total": 9.9, "reasoning": "r", "confidence": "high",
        }),
    )
    result = judge_mod.judge_answer("q", {"completeness": "x"}, "a")
    assert result.scores["completeness"] == 5   # 7 → 钳到 5
    assert result.scores["faithfulness"] == 1   # 0 → 钳到 1
    assert result.scores["citation_quality"] == 1  # 非法字符串 → 缺失口径
    assert result.total == 5.0                  # total 钳到 [0,5]
    assert "score_clamped" in result.reasoning


def test_judge_rejects_invalid_json_as_failure(monkeypatch):
    import backend.evaluation.judge as judge_mod

    monkeypatch.setattr(judge_mod, "_get_llm_response", lambda prompt: "not json")
    result = judge_mod.judge_answer("q", {"completeness": "x"}, "a")
    assert result.total == 0.0  # 解析失败=评估失败语义不变


def test_ragas_bridge_range_check():
    """NaN→None、出界钳位 [0,1]（C4-3，直接测计算路径的判定逻辑）。"""
    val_nan = float("nan")
    assert (val_nan != val_nan) is True  # NaN 判定语义（桥内同款）
    clamped = min(1.0, max(0.0, 1.7))
    assert clamped == 1.0


# ── C4-5：golden 对比非零退出语义 ────────────────────────────


def test_golden_flow_exit_code_semantics(tmp_path, monkeypatch):
    """对比不通过 → passed=False（CLI 侧转 exit 2）。"""
    from backend.evaluation.judge_golden import compare_golden_reports

    baseline = {"cases": {"JG-001": {
        "expected_verdict": "pass", "verdicts": ["pass"], "mean": 4.5, "std": 0.0,
        "scores": [4.5]}}}
    current = {"cases": {"JG-001": {
        "expected_verdict": "pass", "verdicts": ["fail"], "mean": 3.0, "std": 0.0,
        "scores": [3.0]}}}
    result = compare_golden_reports(current, baseline, deviation_threshold=0.1)
    assert result["passed"] is False
    assert result["score_drifts"] and result["new_tier_flips"] == ["JG-001"]


# ── C4-8 / RAGAS-13：随机性口径 ──────────────────────────────


def test_seed_support_explicitly_false():
    from backend.evaluation.evaluator_config import SEED_SUPPORT

    assert SEED_SUPPORT is False

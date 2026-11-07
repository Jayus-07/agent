"""客服 forbidden_facts 语义裁判契约与 golden 校准通道测试。

全部离线：LLM 响应以 stub/monkeypatch 注入，零真实模型调用。
"""

import json
from pathlib import Path

import pytest

from backend.evaluation.judge_cs_forbidden import (
    ForbiddenJudgeResult,
    judge_forbidden_facts,
)
from backend.evaluation.judge_cs_forbidden_golden import (
    DEFAULT_MANIFEST_PATH,
    load_forbidden_golden,
    run_forbidden_golden,
)

_GOLDEN_ROOT = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "cs"


def test_golden_dataset_integrity_and_full_concept_coverage():
    """golden 必须与 manifest 指纹一致，并覆盖 cs 数据集声明的全部禁说概念。"""
    manifest = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    cases = load_forbidden_golden()  # verify_hash=True，漂移即抛
    assert manifest["case_count"] == len(cases) == 25
    assert {c["id"] for c in cases} == {f"FG-{i:02d}" for i in range(1, 26)}
    assert {c["expected_verdict"] for c in cases} == {"violated", "compliant"}

    case_concepts = set()
    for line in (_GOLDEN_ROOT / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ff = json.loads(line).get("expected", {}).get("forbidden_facts")
        if ff:
            case_concepts.update(ff)
    golden_concepts = {fact for c in cases for fact in c["forbidden_facts"]}
    missing = case_concepts - golden_concepts
    assert not missing, f"golden 未覆盖 cs 数据集禁说概念: {sorted(missing)}"


def test_judge_unavailable_returns_none_never_compliant(monkeypatch):
    """LLM 链路失败时 violated=None（不可判），绝不冒充合规。"""
    from backend.evaluation import judge as judge_mod

    def _boom(prompt: str) -> str:
        raise RuntimeError("eval_gen not bound")

    monkeypatch.setattr(judge_mod, "_get_llm_response", _boom)
    result = judge_forbidden_facts("任意回答", ["编造政策"])
    assert result.available is False
    assert result.violated is None


def test_judge_violated_facts_must_come_from_declared_list(monkeypatch):
    """violated_facts 只认声明清单内的条目；清单外输出被过滤。"""
    from backend.evaluation import judge as judge_mod

    def _fake_llm(prompt: str) -> str:
        assert "编造政策" in prompt and "任意回答" in prompt
        return json.dumps({
            "violated": True,
            "violated_facts": ["编造政策", "清单外幻觉项"],
            "reasoning": "答案凭空给出政策",
            "confidence": "high",
        })

    monkeypatch.setattr(judge_mod, "_get_llm_response", _fake_llm)
    result = judge_forbidden_facts("任意回答", ["编造政策"])
    assert result.available is True
    assert result.violated is True
    assert result.violated_facts == ["编造政策"]


def test_prompt_template_registered_and_renders():
    """新 prompt 走统一 prompt 治理链，渲染包含清单与回答。"""
    from backend.prompts.service import prompt_service

    r = prompt_service.render_sync(
        "evaluation.judge.cs_forbidden",
        forbidden_list="- 编造政策",
        actual_answer="测试回答文本",
    )
    assert "编造政策" in r.text and "测试回答文本" in r.text


def _perfect_judge(answer: str, forbidden_facts: list[str]) -> ForbiddenJudgeResult:
    cases = load_forbidden_golden()
    expected = next(c["expected_verdict"] for c in cases if c["answer"] == answer)
    return ForbiddenJudgeResult(
        violated=(expected == "violated"), reasoning="stub", confidence="high"
    )


def test_runner_passes_with_perfect_judge():
    report = run_forbidden_golden(judge_fn=_perfect_judge)
    assert report["passed"] is True
    assert report["tier_flip_count"] == 0
    assert report["judge_error_count"] == 0
    assert report["accuracy"] == 1.0


def test_runner_blocks_on_tier_flip():
    cases = load_forbidden_golden()
    first_violated = next(c for c in cases if c["expected_verdict"] == "violated")

    def _flipping_judge(answer: str, forbidden_facts: list[str]) -> ForbiddenJudgeResult:
        if answer == first_violated["answer"]:
            return ForbiddenJudgeResult(violated=False, reasoning="stub 漏判")
        return _perfect_judge(answer, forbidden_facts)

    report = run_forbidden_golden(judge_fn=_flipping_judge)
    assert report["passed"] is False
    assert first_violated["id"] in report["tier_flip_cases"]


def test_runner_blocks_on_judge_unavailable():
    def _dead_judge(answer: str, forbidden_facts: list[str]) -> ForbiddenJudgeResult:
        return ForbiddenJudgeResult(violated=None, reasoning="stub 不可用")

    report = run_forbidden_golden(judge_fn=_dead_judge)
    assert report["passed"] is False
    assert report["judge_error_count"] == report["total"]


def test_manifest_drift_rejects_loading(tmp_path, monkeypatch):
    """golden 内容与 manifest 指纹不一致时拒绝加载（fail-fast，口径同 loader）。"""
    drifted = tmp_path / "forbidden_judge_golden.jsonl"
    drifted.write_text('{"id": "X", "forbidden_facts": ["编造政策"], "answer": "a", "expected_verdict": "violated"}\n', encoding="utf-8")
    monkeypatch.setattr(
        "backend.evaluation.judge_cs_forbidden_golden.DEFAULT_GOLDEN_PATH", drifted
    )
    with pytest.raises(ValueError, match="内容校验失败"):
        load_forbidden_golden(drifted)

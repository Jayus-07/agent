"""生成式评测候选的审核质量门禁。"""

from typing import Any, Literal

from backend.evaluation.dataset.query_types import (
    MULTI_HOP_QUERY_TYPES,
    RAGAS_QUERY_TYPES,
)

RagasQualityCheck = Literal[
    "question_clear_and_non_duplicate",
    "answer_facts_supported",
    "required_facts_complete",
    "evidence_covers_answer",
    "query_type_matches_reasoning",
]

RAGAS_QUALITY_CHECKS: tuple[RagasQualityCheck, ...] = (
    "question_clear_and_non_duplicate",
    "answer_facts_supported",
    "required_facts_complete",
    "evidence_covers_answer",
    "query_type_matches_reasoning",
)


def validate_ragas_candidate_review(
    *,
    payload: dict[str, Any],
    expected: dict[str, Any] | None,
    evidence: list[dict[str, Any]],
    fact_evidence_bindings: list[dict[str, Any]] | None,
    quality_checks: dict[str, bool] | None,
    rationale: str,
) -> dict[str, bool] | None:
    """确保 Ragas 生成的候选经过逐项人工核对后才能批准。"""
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("generation_method") != "ragas_mixed":
        return None

    query_type = str(metadata.get("query_type") or "")
    if query_type not in RAGAS_QUERY_TYPES:
        raise ValueError("Ragas 候选缺少有效题型，不能批准")
    if not isinstance(expected, dict) or expected.get("behavior", "answer") != "answer":
        raise ValueError("Ragas 候选必须按有答案题核验")
    if not str(expected.get("reference_answer") or "").strip():
        raise ValueError("Ragas 候选必须填写核验后的参考答案")
    required_facts = expected.get("required_facts")
    if not isinstance(required_facts, list) or not any(
        str(item).strip() for item in required_facts
    ):
        raise ValueError("Ragas 候选必须补齐至少一条必需事实")
    fact_bindings = resolve_fact_evidence_bindings(
        required_facts=required_facts,
        evidence=evidence,
        bindings=fact_evidence_bindings,
    )
    if len(rationale.strip()) < 20:
        raise ValueError("Ragas 候选审核理由至少填写 20 个字符")

    checks = quality_checks or {}
    if set(checks) != set(RAGAS_QUALITY_CHECKS) or any(
        checks.get(key) is not True for key in RAGAS_QUALITY_CHECKS
    ):
        raise ValueError("Ragas 候选必须完成全部五项质量核验")

    if query_type in MULTI_HOP_QUERY_TYPES:
        distinct_facts = {
            "".join(str(item).split()).casefold()
            for item in required_facts
            if str(item).strip()
        }
        if len(distinct_facts) < 2:
            raise ValueError("Ragas 多跳题必须拆成至少两条不同的必需事实")
        anchors = {
            anchor_id
            for binding in fact_bindings
            for anchor_id in binding["anchor_ids"]
        }
        if len(anchors) < 2:
            raise ValueError("Ragas 多跳题的不同必需事实必须覆盖至少两条原文证据锚点")

    return {key: True for key in RAGAS_QUALITY_CHECKS}


def resolve_fact_evidence_bindings(
    *,
    required_facts: list[Any],
    evidence: list[dict[str, Any]],
    bindings: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """把审核者选的事实/引用序号解析为已核验的原文锚点，供审计留存。"""
    if not isinstance(bindings, list) or len(bindings) != len(required_facts):
        raise ValueError("Ragas 候选的每条必需事实都必须关联原文证据")

    by_fact: dict[int, list[int]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError("事实与证据关联格式无效")
        fact_index = binding.get("fact_index")
        evidence_indices = binding.get("evidence_indices")
        if isinstance(fact_index, bool) or not isinstance(fact_index, int):
            raise ValueError("事实与证据关联的事实序号无效")
        if fact_index < 0 or fact_index >= len(required_facts) or fact_index in by_fact:
            raise ValueError("事实与证据关联的事实序号重复或越界")
        if not isinstance(evidence_indices, list) or not evidence_indices:
            raise ValueError("每条必需事实至少要关联一条已核验证据")

        anchors: list[int] = []
        for evidence_index in evidence_indices:
            if isinstance(evidence_index, bool) or not isinstance(evidence_index, int):
                raise ValueError("事实与证据关联的引用序号无效")
            if evidence_index < 0 or evidence_index >= len(evidence):
                raise ValueError("事实与证据关联的引用序号越界")
            if not str(evidence[evidence_index].get("anchor_id") or "").strip():
                raise ValueError("事实与证据关联只能指向服务端核验过的原文锚点")
            anchors.append(evidence_index)
        by_fact[fact_index] = sorted(set(anchors))

    if set(by_fact) != set(range(len(required_facts))):
        raise ValueError("事实与证据关联未覆盖全部必需事实")

    resolved = []
    for fact_index, fact in enumerate(required_facts):
        fact_text = str(fact).strip()
        if not fact_text:
            raise ValueError("必需事实不能是空内容")
        anchor_ids = sorted({
            str(evidence[index]["anchor_id"]).strip()
            for index in by_fact[fact_index]
        })
        resolved.append({
            "fact_index": fact_index,
            "fact": fact_text,
            "anchor_ids": anchor_ids,
        })
    return resolved

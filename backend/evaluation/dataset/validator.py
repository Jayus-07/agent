"""测试集校验器 — 枚举校验、schema 验证、来源比例检查。"""

from backend.evaluation.models import TestCase

_VALID_ANSWER_TYPES = {"factual", "numeric", "procedural", "comparative", ""}
_VALID_QUERY_TYPES = {
    "single_doc", "multi_hop", "adversarial", "negative", "table",
    "chunk_level", "long_doc", "comparative", "procedural", "aggregation",
    "conditional_reasoning", "negation_exclusion", "implicit_condition", "",
}
_VALID_TIERS = {"smoke", "core", "hard", "regression", ""}
_VALID_SOURCES = {"curated", "adversarial", "production_log", "regression", "",
                  # cs-v2 锁版（2026-09-19）：来源枚举见 datasets/cs/v2 生成器
                  "demo-kb", "demo-order", "synthetic", "mapping-review"}


def _normalize_ground_truth_context(expected: dict) -> None:
    """就地规范化 ground_truth_context：裸字符串 → {text: s} 对象。"""
    gt = expected.get("ground_truth_context")
    if not gt or not isinstance(gt, list):
        return
    normalized = []
    for entry in gt:
        if isinstance(entry, str):
            normalized.append({"text": entry, "source_doc": "", "section": ""})
        elif isinstance(entry, dict):
            normalized.append(entry)
        else:
            normalized.append({"text": str(entry), "source_doc": "", "section": ""})
    expected["ground_truth_context"] = normalized


def validate_dataset(cases: list[TestCase]) -> list[str]:
    """校验测试集，返回错误信息列表。空列表表示通过。

    V2.0 扩展校验：
    - 检查 expected.relevant_docs 格式（doc_id 应为 10 位 hex）
    - 检查 answer_type 枚举值
    - 检查 query_type 枚举值
    - 检查 tier 枚举值
    - 检查 must_contain / must_not_contain 为列表类型
    """
    errors: list[str] = []

    seen_ids: set[str] = set()

    for case in cases:
        if case.id in seen_ids:
            errors.append(f"Duplicate case ID: {case.id}")
        seen_ids.add(case.id)

        if not case.question.strip():
            errors.append(f"Case {case.id}: question is empty")
        if case.module not in ("planner", "rag", "cs"):
            errors.append(f"Case {case.id}: invalid module '{case.module}'")

        relevant_docs = case.expected.get("relevant_docs", [])
        if relevant_docs:
            for doc_entry in relevant_docs:
                if isinstance(doc_entry, dict):
                    if not doc_entry.get("source_file") and not doc_entry.get("doc_id"):
                        errors.append(f"Case {case.id}: relevant_docs dict must have source_file or doc_id")
                elif not isinstance(doc_entry, str):
                    errors.append(f"Case {case.id}: relevant_docs contains invalid entry: {doc_entry}")

        answer_type = case.metadata.get("answer_type", "")
        if answer_type and answer_type not in _VALID_ANSWER_TYPES:
            errors.append(f"Case {case.id}: invalid answer_type '{answer_type}'")

        query_type = case.metadata.get("query_type", "")
        if query_type and query_type not in _VALID_QUERY_TYPES:
            errors.append(f"Case {case.id}: invalid query_type '{query_type}'")

        tier = case.metadata.get("tier", "")
        if tier and tier not in _VALID_TIERS:
            errors.append(f"Case {case.id}: invalid tier '{tier}'")

        for field in ("must_contain", "must_not_contain"):
            value = case.metadata.get(field)
            if value is not None and not isinstance(value, list):
                errors.append(f"Case {case.id}: {field} must be a list, got {type(value).__name__}")

        source = case.metadata.get("source", "")
        if source and source not in _VALID_SOURCES:
            errors.append(f"Case {case.id}: invalid source '{source}'")

        gt = case.expected.get("ground_truth_context")
        should_reject = case.expected.get("should_reject", False)
        if gt is not None:
            if not isinstance(gt, list):
                errors.append(f"Case {case.id}: ground_truth_context must be a list")
            else:
                for i, entry in enumerate(gt):
                    if not isinstance(entry, dict):
                        errors.append(
                            f"Case {case.id}: ground_truth_context[{i}] must be a dict"
                        )
                    elif not entry.get("text", "").strip():
                        errors.append(
                            f"Case {case.id}: ground_truth_context[{i}].text is empty"
                        )
                if should_reject and len(gt) > 0:
                    errors.append(
                        f"Case {case.id}: reject case must have empty ground_truth_context"
                    )
                if not should_reject and len(gt) == 0:
                    errors.append(
                        f"Case {case.id}: non-reject case must have non-empty ground_truth_context"
                    )

    return errors

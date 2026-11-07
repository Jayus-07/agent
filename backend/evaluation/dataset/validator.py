"""测试集校验器 — 枚举校验、schema 验证、来源比例检查。"""

from backend.evaluation.models import MODULE_KINDS, TestCase

_VALID_ANSWER_TYPES = {"factual", "numeric", "procedural", "comparative", ""}
_VALID_QUERY_TYPES = {
    "single_doc", "multi_hop", "adversarial", "negative", "table",
    "chunk_level", "long_doc", "comparative", "procedural", "aggregation",
    "conditional_reasoning", "negation_exclusion", "implicit_condition", "",
}
_VALID_TIERS = {"smoke", "core", "hard", "regression", ""}
_VALID_SOURCES = {"curated", "adversarial", "production_log", "regression", "",
                  # cs-v2 锁版（2026-09-19）：生成器归档见 datasets/cs/v2.deprecated
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


def validate_dataset(
    cases: list[TestCase], *, expected_module: str | None = None,
) -> list[str]:
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
        if not case.id.strip():
            errors.append("Case ID is empty")
        if case.id in seen_ids:
            errors.append(f"Duplicate case ID: {case.id}")
        seen_ids.add(case.id)

        if not case.question.strip():
            errors.append(f"Case {case.id}: question is empty")
        if case.module not in MODULE_KINDS:
            errors.append(f"Case {case.id}: invalid module '{case.module}'")
        if expected_module and case.module != expected_module:
            errors.append(
                f"Case {case.id}: module '{case.module}' does not match dataset "
                f"'{expected_module}'"
            )

        if case.module == "planner":
            expected = case.expected
            capabilities = expected.get("capabilities")
            if (
                not isinstance(capabilities, list)
                or any(not isinstance(cap, str) or not cap.strip() for cap in capabilities)
            ):
                errors.append(f"Case {case.id}: planner capabilities must be non-empty strings")
                capabilities = []

            minimum = expected.get("min_steps")
            maximum = expected.get("max_steps")
            for name, value in (("min_steps", minimum), ("max_steps", maximum)):
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    errors.append(f"Case {case.id}: planner {name} must be a non-negative integer")
            if (
                isinstance(minimum, int) and not isinstance(minimum, bool)
                and isinstance(maximum, int) and not isinstance(maximum, bool)
            ):
                if minimum > maximum:
                    errors.append(f"Case {case.id}: planner min_steps exceeds max_steps")
                if (
                    isinstance(capabilities, list)
                    and not minimum <= len(capabilities) <= maximum
                ):
                    errors.append(
                        f"Case {case.id}: capability count is outside planner step bounds"
                    )

            edges = expected.get("edges")
            if not isinstance(edges, list):
                errors.append(f"Case {case.id}: planner edges must be a list")
            else:
                for index, edge in enumerate(edges):
                    if not isinstance(edge, dict) or set(edge) != {"from", "to"}:
                        errors.append(
                            f"Case {case.id}: planner edges[{index}] must have from and to"
                        )
                        continue
                    source, target = edge.get("from"), edge.get("to")
                    if (
                        not isinstance(source, str)
                        or not isinstance(target, str)
                        or source not in capabilities
                        or target not in capabilities
                    ):
                        errors.append(
                            f"Case {case.id}: planner edge endpoints must be expected capabilities"
                        )
                    if source == target and capabilities.count(source) == 1:
                        errors.append(f"Case {case.id}: planner edge cannot be a self-dependency")

            excluded = expected.get("should_not_contain", [])
            if "should_not_contain" not in expected:
                errors.append(f"Case {case.id}: planner should_not_contain is required")
            if not isinstance(excluded, list) or any(
                not isinstance(cap, str) or not cap.strip() for cap in excluded
            ):
                errors.append(
                    f"Case {case.id}: planner should_not_contain must be a list of strings"
                )
            elif set(capabilities) & set(excluded):
                errors.append(
                    f"Case {case.id}: planner expected and excluded capabilities overlap"
                )

            if isinstance(edges, list):
                edge_pairs = [
                    (edge.get("from"), edge.get("to"))
                    for edge in edges
                    if isinstance(edge, dict)
                    and set(edge) == {"from", "to"}
                    and isinstance(edge.get("from"), str)
                    and isinstance(edge.get("to"), str)
                ]
                # capability 标签唯一时可直接检查期望 DAG；有重复 capability 时，
                # 标签不足以区分不同步骤，Live scorer 会按边多重集精确校验。
                if len(capabilities) == len(set(capabilities)):
                    adjacency: dict[str, list[str]] = {}
                    for source, target in edge_pairs:
                        adjacency.setdefault(source, []).append(target)
                    visiting: set[str] = set()
                    visited: set[str] = set()

                    def has_cycle(node: str) -> bool:
                        if node in visiting:
                            return True
                        if node in visited:
                            return False
                        visiting.add(node)
                        if any(
                            has_cycle(next_node)
                            for next_node in adjacency.get(node, [])
                        ):
                            return True
                        visiting.remove(node)
                        visited.add(node)
                        return False

                    if any(has_cycle(node) for node in adjacency):
                        errors.append(f"Case {case.id}: planner edges must form a DAG")

            params = expected.get("params", {})
            if not isinstance(params, dict):
                errors.append(f"Case {case.id}: planner params must be an object")
            else:
                for capability, checks in params.items():
                    if capability not in capabilities or not isinstance(checks, dict):
                        errors.append(
                            f"Case {case.id}: planner params must target an expected capability"
                        )

        relevant_docs = case.expected.get("relevant_docs", [])
        if relevant_docs is not None and not isinstance(relevant_docs, list):
            errors.append(f"Case {case.id}: relevant_docs must be a list")
        elif isinstance(relevant_docs, list):
            for doc_entry in relevant_docs:
                if isinstance(doc_entry, dict):
                    if not doc_entry.get("source_file") and not doc_entry.get("doc_id"):
                        errors.append(f"Case {case.id}: relevant_docs dict must have source_file or doc_id")
                elif not isinstance(doc_entry, str):
                    errors.append(f"Case {case.id}: relevant_docs contains invalid entry: {doc_entry}")

        answer_type = case.metadata.get("answer_type", "")
        if not isinstance(answer_type, str):
            errors.append(f"Case {case.id}: answer_type must be a string")
        elif case.module == "rag" and answer_type and answer_type not in _VALID_ANSWER_TYPES:
            errors.append(f"Case {case.id}: invalid RAG answer_type '{answer_type}'")

        query_type = case.metadata.get("query_type", "")
        if not isinstance(query_type, str):
            errors.append(f"Case {case.id}: query_type must be a string")
        elif case.module == "rag" and query_type and query_type not in _VALID_QUERY_TYPES:
            errors.append(f"Case {case.id}: invalid RAG query_type '{query_type}'")

        tier = case.metadata.get("tier", "")
        if not isinstance(tier, str):
            errors.append(f"Case {case.id}: tier must be a string")
        elif tier and tier not in _VALID_TIERS:
            errors.append(f"Case {case.id}: invalid tier '{tier}'")

        for field in ("must_contain", "must_not_contain"):
            value = case.metadata.get(field)
            if value is not None and not isinstance(value, list):
                errors.append(f"Case {case.id}: {field} must be a list, got {type(value).__name__}")

        source = case.metadata.get("source", "")
        if not isinstance(source, str):
            errors.append(f"Case {case.id}: source must be a string")
        elif source and source not in _VALID_SOURCES:
            errors.append(f"Case {case.id}: invalid source '{source}'")

        if case.module == "sql":
            expected = case.expected
            should_reject = expected.get("should_reject", False)
            if not isinstance(should_reject, bool):
                errors.append(f"Case {case.id}: SQL should_reject must be a boolean")

            expected_tables = expected.get("expected_tables")
            if not isinstance(expected_tables, list) or any(
                not isinstance(table, str) or not table.strip()
                for table in expected_tables
            ):
                errors.append(
                    f"Case {case.id}: SQL expected_tables must be a list of non-empty strings"
                )
            elif not should_reject and not expected_tables:
                errors.append(f"Case {case.id}: SQL positive case needs expected_tables")

            if should_reject is True:
                expected_status = expected.get("expected_status")
                if not isinstance(expected_status, list) or not expected_status or any(
                    not isinstance(status, str) or not status.strip()
                    for status in expected_status
                ):
                    errors.append(
                        f"Case {case.id}: SQL reject case needs non-empty expected_status strings"
                    )
            else:
                gold_sql = expected.get("gold_sql")
                if not isinstance(gold_sql, str) or not gold_sql.strip():
                    errors.append(f"Case {case.id}: SQL positive case needs non-empty gold_sql")

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
                    elif (
                        not isinstance(entry.get("text", ""), str)
                        or not entry.get("text", "").strip()
                    ):
                        errors.append(
                            f"Case {case.id}: ground_truth_context[{i}].text must be a non-empty string"
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

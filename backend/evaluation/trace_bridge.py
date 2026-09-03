"""Trace → TestCase 转换桥。

将线上 trace 记录转换为评测用例，支持自动推断模块和手动覆盖。
转换后的 TestCase 可直接追加到评测集 JSON 中。
"""
from __future__ import annotations

import uuid
from typing import Any

from backend.evaluation.models import TestCase, ModuleKind
from backend.shared.logger import logger

_WORKFLOW_TO_MODULE: dict[str, ModuleKind] = {
    "cs_agent": "cs",
    "customer_service": "cs",
    "rag_agent": "rag",
    "rag_chain": "rag",
    "sql_agent": "sql",
    "planner": "planner",
    "multi_agent": "e2e",
}


def _infer_module(trace: dict | Any) -> ModuleKind:
    """从 trace tags / workflow_name 推断评测模块。"""
    get = lambda k, d=None: trace.get(k, d) if isinstance(trace, dict) else getattr(trace, k, d)

    tags = get("tags", {}) or {}
    if isinstance(tags, dict) and tags.get("conversation_id"):
        return "cs"

    workflow_name = get("workflow_name", "") or ""
    if workflow_name in _WORKFLOW_TO_MODULE:
        return _WORKFLOW_TO_MODULE[workflow_name]

    workflow_kind = get("workflow_kind", "") or ""
    if "cs" in workflow_kind or "customer" in workflow_kind:
        return "cs"
    if "rag" in workflow_kind:
        return "rag"
    if "sql" in workflow_kind:
        return "sql"

    return "e2e"


def _extract_answer(trace: dict | Any) -> str:
    """从 trace 中提取实际回答文本。"""
    get = lambda k, d=None: trace.get(k, d) if isinstance(trace, dict) else getattr(trace, k, d)
    answer = get("answer_preview", "") or ""
    if answer:
        return answer
    metadata = get("metadata", {}) or {}
    if isinstance(metadata, dict):
        return metadata.get("answer", "") or ""
    return ""


def build_test_case_from_trace(
    trace: dict | Any,
    module: ModuleKind | None = None,
    expected_overrides: dict[str, Any] | None = None,
    note: str = "",
) -> TestCase:
    """将 trace 记录转换为 TestCase。

    Args:
        trace: TraceRecord 或等价的 dict（SQLite 存储格式）。
        module: 显式指定模块；为 None 时自动推断。
        expected_overrides: 覆盖自动提取的 expected 字段。
        note: 人工备注，写入 metadata.note。

    Returns:
        TestCase，metadata.source = "trace"，ground_truth_verified = False。
    """
    get = lambda k, d=None: trace.get(k, d) if isinstance(trace, dict) else getattr(trace, k, d)

    resolved_module = module or _infer_module(trace)
    question = get("question", "") or ""
    answer = _extract_answer(trace)
    trace_id = get("id", "") or ""

    expected: dict[str, Any] = {}
    if answer:
        expected["expected_answer"] = answer

    spans = get("spans", []) or []
    llm_spans = [
        s for s in spans
        if (s.get("type") if isinstance(s, dict) else getattr(s, "type", "")) == "llm_call"
    ]
    if llm_spans:
        expected["llm_call_count"] = len(llm_spans)

    retrieval_spans = [
        s for s in spans
        if (s.get("type") if isinstance(s, dict) else getattr(s, "type", "")) == "retrieval"
    ]
    if retrieval_spans:
        expected["retrieval_count"] = len(retrieval_spans)

    if expected_overrides:
        expected.update(expected_overrides)

    metadata: dict[str, Any] = {
        "source": "trace",
        "trace_id": trace_id,
        "ground_truth_verified": False,
    }
    if note:
        metadata["note"] = note

    session_id = get("session_id", "") or ""
    if session_id:
        metadata["session_id"] = session_id

    workflow_name = get("workflow_name", "") or ""
    if workflow_name:
        metadata["workflow_name"] = workflow_name

    duration_ms = get("duration_ms", 0) or 0
    if duration_ms:
        metadata["trace_duration_ms"] = duration_ms

    case_id = f"TRACE-{uuid.uuid4().hex[:8].upper()}"

    return TestCase(
        id=case_id,
        question=question,
        module=resolved_module,
        expected=expected,
        metadata=metadata,
    )

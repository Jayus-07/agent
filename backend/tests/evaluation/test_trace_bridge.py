"""test_trace_bridge.py — Trace → TestCase 转换测试。

覆盖：
- 模块自动推断（conversation_id tag → cs, workflow_name → rag/sql/...）
- expected 字段提取（answer, llm_call_count, retrieval_count）
- expected_overrides 覆盖
- metadata.source = "trace", ground_truth_verified = False
- dict 和 TraceRecord 两种输入格式
"""
from __future__ import annotations

import pytest

from backend.evaluation.trace_bridge import (
    build_test_case_from_trace,
    _infer_module,
    _extract_answer,
)


def _make_trace(**overrides) -> dict:
    base = {
        "id": "abc123",
        "question": "如何退货？",
        "answer_preview": "您可以在7天内退货。",
        "session_id": "sess-001",
        "workflow_name": "cs_agent",
        "workflow_kind": "",
        "duration_ms": 1200,
        "spans": [
            {"span_id": "s1", "type": "llm_call", "name": "LLM", "parent_id": None},
            {"span_id": "s2", "type": "retrieval", "name": "检索", "parent_id": None},
        ],
        "tags": {},
        "metadata": {},
    }
    base.update(overrides)
    return base


class TestInferModule:
    def test_conversation_id_tag_maps_to_cs(self):
        trace = _make_trace(tags={"conversation_id": "conv-123"})
        assert _infer_module(trace) == "cs"

    def test_workflow_name_cs_agent(self):
        assert _infer_module(_make_trace(workflow_name="cs_agent")) == "cs"

    def test_workflow_name_rag_agent(self):
        assert _infer_module(_make_trace(workflow_name="rag_agent")) == "rag"

    def test_workflow_name_sql_agent(self):
        assert _infer_module(_make_trace(workflow_name="sql_agent")) == "sql"

    def test_workflow_name_planner(self):
        assert _infer_module(_make_trace(workflow_name="planner")) == "planner"

    def test_workflow_name_multi_agent_maps_to_e2e(self):
        assert _infer_module(_make_trace(workflow_name="multi_agent")) == "e2e"

    def test_unknown_workflow_defaults_to_e2e(self):
        assert _infer_module(_make_trace(workflow_name="unknown")) == "e2e"

    def test_workflow_kind_cs(self):
        assert _infer_module(_make_trace(workflow_kind="customer_service")) == "cs"

    def test_workflow_kind_rag(self):
        assert _infer_module(_make_trace(workflow_name="", workflow_kind="rag_query")) == "rag"


class TestExtractAnswer:
    def test_from_answer_preview(self):
        assert _extract_answer(_make_trace()) == "您可以在7天内退货。"

    def test_from_metadata_fallback(self):
        trace = _make_trace(answer_preview="")
        trace["metadata"] = {"answer": "来自 metadata 的回答"}
        assert _extract_answer(trace) == "来自 metadata 的回答"

    def test_empty_when_no_answer(self):
        trace = _make_trace(answer_preview="")
        assert _extract_answer(trace) == ""


class TestTestCaseFromTrace:
    def test_basic_conversion(self):
        trace = _make_trace()
        case = build_test_case_from_trace(trace)

        assert case.question == "如何退货？"
        assert case.module == "cs"
        assert case.metadata["source"] == "trace"
        assert case.metadata["trace_id"] == "abc123"
        assert case.metadata["ground_truth_verified"] is False
        assert case.expected["expected_answer"] == "您可以在7天内退货。"
        assert case.expected["llm_call_count"] == 1
        assert case.expected["retrieval_count"] == 1
        assert case.id.startswith("TRACE-")

    def test_explicit_module_override(self):
        trace = _make_trace()
        case = build_test_case_from_trace(trace, module="rag")
        assert case.module == "rag"

    def test_expected_overrides(self):
        trace = _make_trace()
        case = build_test_case_from_trace(trace, expected_overrides={"custom_field": 42})
        assert case.expected["custom_field"] == 42
        assert "expected_answer" in case.expected

    def test_note_in_metadata(self):
        trace = _make_trace()
        case = build_test_case_from_trace(trace, note="回归测试用例")
        assert case.metadata["note"] == "回归测试用例"

    def test_session_id_in_metadata(self):
        trace = _make_trace()
        case = build_test_case_from_trace(trace)
        assert case.metadata["session_id"] == "sess-001"

    def test_no_spans_means_no_counts(self):
        trace = _make_trace(spans=[])
        case = build_test_case_from_trace(trace)
        assert "llm_call_count" not in case.expected
        assert "retrieval_count" not in case.expected

    def test_unique_case_ids(self):
        trace = _make_trace()
        ids = {build_test_case_from_trace(trace).id for _ in range(10)}
        assert len(ids) == 10

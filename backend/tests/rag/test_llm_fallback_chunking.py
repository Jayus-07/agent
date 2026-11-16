"""规则优先的 LLM 边界兜底：分组校验、结构输出和失败回退。"""
from __future__ import annotations

import json
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.rag.preprocessing.ast import (
    DocumentAST,
    DocumentNode,
    annotate_ast_provenance,
)
from backend.rag.preprocessing.chunking import RecursiveChunkStrategy, coverage_audit
from backend.rag.preprocessing.llm_fallback_chunking import (
    apply_llm_boundary_fallback,
    collect_source_units,
    parse_groups,
    validate_groups,
)


class _Recorder:
    def __init__(self):
        self.stages = []

    def begin_stage(self, stage, **kwargs):
        self.stages.append({"stage": stage, "begin": kwargs})
        return "step-1", time.monotonic()

    def bind_stage(self, _step_id):
        return nullcontext()

    def set_stage_model(self, _step_id, model):
        self.stages[-1]["model"] = model

    def finish_stage(self, _step_id, **kwargs):
        self.stages[-1]["finish"] = kwargs


def _ast() -> DocumentAST:
    texts = [
        "旅客提交申请后，系统先核对订单状态和出行日期。",
        "如订单处于已出票状态，应先检查供应商的退改规则。",
        "处理完成后，客服需要记录结果并告知旅客预计到账时间。",
    ]
    ast = DocumentAST(
        root=DocumentNode(
            type="section",
            text="",
            children=[DocumentNode(type="paragraph", text=value) for value in texts],
        ),
        source_file="fallback-fixture.txt",
        raw_text="\n\n".join(texts),
        source_format="txt",
        parser_version="fixture-parser-v1",
    )
    annotate_ast_provenance(ast, ast.parser_version)
    return ast


def _patch_llm(monkeypatch, groups):
    from backend.config import model_roles
    from backend.infra.llm import models
    from backend.rag.preprocessing import llm_enrichment

    monkeypatch.setattr(
        model_roles,
        "resolve_effective",
        lambda _role: {
            "value": "fixture-model",
            "source": "test",
            "updated_at": None,
        },
    )
    monkeypatch.setattr(models, "resolve_provider", lambda _name: "fixture-provider")
    response = SimpleNamespace(
        content=json.dumps({"groups": groups}, ensure_ascii=False)
    )
    monkeypatch.setattr(
        llm_enrichment,
        "invoke_metadata_llm",
        lambda *_args, **_kwargs: response,
    )


def test_llm_groups_must_preserve_every_source_node_and_section():
    ast = _ast()
    units = collect_source_units(ast)
    ids = [item["id"] for item in units]

    assert validate_groups([[ids[0], ids[1]], [ids[2]]], units)
    with pytest.raises(ValueError, match="invalid_source_node_partition"):
        validate_groups([[ids[0]], [ids[2]]], units)
    with pytest.raises(ValueError, match="invalid_source_node_partition"):
        validate_groups([[ids[0], ids[0], ids[2]]], units)

    units[1]["section_path"] = ("另一个真实章节",)
    with pytest.raises(ValueError, match="cross_section_group"):
        validate_groups([[ids[0], ids[1]], [ids[2]]], units)


def test_llm_fallback_creates_parent_leaf_chunks_and_records_the_attempt(monkeypatch):
    ast = _ast()
    units = collect_source_units(ast)
    ids = [item["id"] for item in units]
    _patch_llm(monkeypatch, [[ids[0], ids[1]], [ids[2]]])
    recorder = _Recorder()
    rule_chunks = RecursiveChunkStrategy().split(ast, ast.source_file)

    chunks, metadata = apply_llm_boundary_fallback(
        ast,
        ast.source_file,
        "general",
        "no_recognized_structure",
        rule_chunks,
        tenant_id="tenant-a",
        kb_id="kb-a",
        permission_scope="general",
        department="general",
        rule_strategy="RecursiveChunkStrategy",
        rule_version="v3",
        lineage_recorder=recorder,
    )

    assert metadata["attempted"] is True
    assert metadata["applied"] is True, (
        f"{metadata.get('failure_class')} at {metadata.get('failure_location')}; "
        f"{metadata.get('failure_type')}"
    )
    assert metadata["group_count"] == 2
    assert coverage_audit(ast, chunks)["uncovered_count"] == 0
    parents = {
        chunk.metadata["chunk_id"]
        for chunk in chunks if chunk.metadata.get("granularity") == "parent"
    }
    leaves = [chunk for chunk in chunks if chunk.metadata.get("granularity") == "leaf"]
    assert leaves
    assert all(chunk.metadata.get("parent_chunk_id") in parents for chunk in leaves)
    assert recorder.stages[0]["stage"] == "chunking_llm_fallback"
    assert recorder.stages[0]["finish"]["status"] == "success"
    assert recorder.stages[0]["finish"]["output_count"] == len(chunks)
    assert recorder.stages[0]["begin"]["metadata"]["tenant_id"] == "tenant-a"
    assert recorder.stages[0]["begin"]["metadata"]["kb_id"] == "kb-a"
    assert recorder.stages[0]["begin"]["metadata"]["permission_scope"] == "general"
    assert recorder.stages[0]["begin"]["metadata"]["department"] == "general"
    assert recorder.stages[0]["begin"]["metadata"]["rule_strategy"] == "RecursiveChunkStrategy"
    assert recorder.stages[0]["begin"]["metadata"]["rule_version"] == "v3"
    assert recorder.stages[0]["begin"]["metadata"]["parser_version"] == "fixture-parser-v1"
    assert "text" not in recorder.stages[0]["finish"]["metadata"]


def test_llm_fallback_uses_chunking_role_timeout(monkeypatch):
    ast = _ast()
    units = collect_source_units(ast)
    ids = [item["id"] for item in units]
    _patch_llm(monkeypatch, [[ids[0], ids[1]], [ids[2]]])

    from backend.config import model_roles
    from backend.core.tool_runtime.deadline import remaining_execution_seconds
    from backend.rag.preprocessing import llm_enrichment

    monkeypatch.setattr(
        model_roles,
        "resolve_runtime_policy",
        lambda _role: SimpleNamespace(timeout_seconds=7),
    )
    deadlines = []

    def _invoke_with_deadline(*_args, **_kwargs):
        deadlines.append(remaining_execution_seconds())
        return SimpleNamespace(
            content=json.dumps({"groups": [[ids[0], ids[1]], [ids[2]]]})
        )

    monkeypatch.setattr(llm_enrichment, "invoke_metadata_llm", _invoke_with_deadline)
    rule_chunks = RecursiveChunkStrategy().split(ast, ast.source_file)

    chunks, metadata = apply_llm_boundary_fallback(
        ast,
        ast.source_file,
        "general",
        "no_recognized_structure",
        rule_chunks,
    )

    assert metadata["applied"] is True
    assert len(deadlines) == 1
    assert deadlines[0] is not None
    assert 0 < deadlines[0] <= 7
    assert remaining_execution_seconds() is None
    assert coverage_audit(ast, chunks)["uncovered_count"] == 0


def test_invalid_llm_boundaries_keep_the_original_rule_chunks(monkeypatch):
    ast = _ast()
    units = collect_source_units(ast)
    ids = [item["id"] for item in units]
    _patch_llm(monkeypatch, [[ids[0], ids[2]]])
    recorder = _Recorder()
    rule_chunks = RecursiveChunkStrategy().split(ast, ast.source_file)

    chunks, metadata = apply_llm_boundary_fallback(
        ast,
        ast.source_file,
        "general",
        "no_recognized_structure",
        rule_chunks,
        lineage_recorder=recorder,
    )

    assert chunks is rule_chunks
    assert metadata["applied"] is False
    assert metadata["failure_type"] == "invalid_source_node_partition"
    assert recorder.stages[0]["finish"]["status"] == "fallback"
    assert recorder.stages[0]["finish"]["fallback_reason"] == "invalid_source_node_partition"


def test_context_budget_rejection_is_not_counted_as_provider_failure(monkeypatch):
    from backend.context_budget.errors import ContextBudgetExceededError
    from backend.rag.preprocessing import llm_enrichment

    ast = _ast()
    rule_chunks = RecursiveChunkStrategy().split(ast, ast.source_file)
    recorder = _Recorder()
    _patch_llm(monkeypatch, [])
    monkeypatch.setattr(
        llm_enrichment,
        "invoke_metadata_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ContextBudgetExceededError("over budget", used_tokens=4097, input_budget=4096)
        ),
    )

    chunks, metadata = apply_llm_boundary_fallback(
        ast,
        ast.source_file,
        "general",
        "no_recognized_structure",
        rule_chunks,
        lineage_recorder=recorder,
    )

    assert chunks is rule_chunks
    assert metadata["attempted"] is True
    assert metadata["applied"] is False
    assert metadata["failure_type"] == "context_budget_rejected"
    assert metadata["failure_class"] == "ContextBudgetExceededError"
    assert recorder.stages[0]["finish"]["status"] == "fallback"
    assert recorder.stages[0]["finish"]["fallback_reason"] == "context_budget_rejected"


def test_response_parser_requires_json_groups():
    assert parse_groups(SimpleNamespace(content='```json\n{"groups":[["a"]]}\n```')) == [["a"]]
    with pytest.raises(ValueError, match="invalid_json"):
        parse_groups(SimpleNamespace(content="不是 JSON"))

"""规则切分无法识别结构时，用 LLM 选择原文节点边界。

LLM 只返回原 AST 节点 ID 的分组，不生成或改写正文。输出必须是原节点的
完整有序分组；解析、校验或模型调用失败时，调用方继续使用原规则分块。
"""
from __future__ import annotations

import hashlib
import json
import traceback
from typing import Any

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, walk
from backend.rag.preprocessing.chunking import (
    LEAF_TYPES,
    StructureChunkStrategy,
    _enrich,
    coverage_audit,
    ensure_unique_chunk_ids,
)

PROMPT_VERSION = "rag-chunk-boundaries-v1"
SCHEMA_FINGERPRINT = hashlib.sha256(
    b'{"groups":[["source_node_id"]]}'
).hexdigest()
MAX_PROMPT_CHARS = 30000


def collect_source_units(ast: DocumentAST) -> list[dict[str, Any]]:
    """按原文顺序收集 AST 内容叶子及其真实章节路径。"""
    result: list[dict[str, Any]] = []

    def visit(node: DocumentNode, section_path: tuple[str, ...]) -> None:
        current_path = section_path
        if node.type == "section" and node.level > 0 and node.text.strip():
            current_path = (*section_path, node.text.strip())
        if node.type in LEAF_TYPES and node.text.strip():
            result.append({
                "id": str(node.node_id or ""),
                "type": node.type,
                "section_path": current_path,
                "text": node.text,
                "node": node,
            })
        for child in node.children:
            visit(child, current_path)

    visit(ast.root, ())
    return result


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content if isinstance(part, dict)
        ).strip()
    return str(content or "").strip()


def parse_groups(response: Any) -> list[list[str]]:
    """读取严格 JSON 分组，容忍模型附带的 Markdown 代码围栏。"""
    text = _response_text(response)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("invalid_json")
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_json") from exc
    groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list) or not groups:
        raise ValueError("invalid_groups")
    if any(
        not isinstance(group, list) or not group
        or any(not isinstance(node_id, str) or not node_id for node_id in group)
        for group in groups
    ):
        raise ValueError("invalid_groups")
    return groups


def validate_groups(
    groups: list[list[str]], units: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """要求节点无缺失、无重复、保持原顺序且不跨真实章节。"""
    by_id = {item["id"]: item for item in units}
    ordered_ids = [item["id"] for item in units]
    flattened = [node_id for group in groups for node_id in group]
    if (
        not all(ordered_ids)
        or len(by_id) != len(ordered_ids)
        or flattened != ordered_ids
    ):
        raise ValueError("invalid_source_node_partition")

    result: list[list[dict[str, Any]]] = []
    for group in groups:
        resolved = [by_id[node_id] for node_id in group]
        if len({item["section_path"] for item in resolved}) > 1:
            raise ValueError("cross_section_group")
        result.append(resolved)
    return result


def build_llm_boundary_chunks(
    ast: DocumentAST,
    file_path: str,
    groups: list[list[dict[str, Any]]],
) -> list[Document]:
    """用规则策略生成 Parent/Leaf；LLM 只决定哪些原节点属于同一组。"""
    chunks: list[Document] = []
    strategy = StructureChunkStrategy()
    for group in groups:
        section_path = tuple(group[0]["section_path"])
        section_title = section_path[-1] if section_path else ""
        section = DocumentNode(
            type="section",
            text=section_title,
            level=len(section_path),
        )
        group_key = hashlib.sha256(
            "\x00".join(item["id"] for item in group).encode("utf-8")
        ).hexdigest()[:20]
        strategy._emit_section_chunks(
            chunks,
            file_path,
            section,
            list(section_path),
            [item["node"] for item in group],
            anchor=f"llm:{group_key}",
        )
    ensure_unique_chunk_ids(chunks, file_path)
    return _enrich(chunks, file_path)


def _prompt(ast: DocumentAST, doc_type: str, units: list[dict[str, Any]]) -> str:
    source = [
        {
            "id": item["id"],
            "type": item["type"],
            "section_path": list(item["section_path"]),
            "text": item["text"],
        }
        for item in units
    ]
    return (
        "你负责为企业知识检索选择切分边界。规则切分没有识别出可靠结构，"
        "请只把相邻的原文节点分组为语义完整的小节。不得改写、删减、补充正文，"
        "不得跨越已有章节路径。输出必须是 JSON："
        '{"groups":[["节点ID","节点ID"],["节点ID"]]}。'
        "每个输入节点 ID 必须按原顺序恰好出现一次。"
        "每组会由确定性代码生成 Parent/Leaf 并校验原文覆盖。"
        f"\n文档格式：{ast.source_format or 'unknown'}"
        f"\n业务类型：{doc_type or 'general'}"
        f"\n节点：{json.dumps(source, ensure_ascii=False, separators=(',', ':'))}"
    )


def apply_llm_boundary_fallback(
    ast: DocumentAST,
    file_path: str,
    doc_type: str,
    fallback_type: str,
    rule_chunks: list[Document],
    tenant_id: str = "",
    kb_id: str = "",
    permission_scope: str = "general",
    department: str = "",
    rule_strategy: str = "unknown",
    rule_version: str = "unknown",
    lineage_recorder=None,
    trace_parent_span_id: str | None = None,
) -> tuple[list[Document], dict[str, Any]]:
    """执行一次受限兜底；所有异常均保留原规则结果并留下血缘。"""
    units = collect_source_units(ast)
    metadata: dict[str, Any] = {
        "attempted": False,
        "applied": False,
        "fallback_type": fallback_type,
        "source_format": (ast.source_format or "unknown").lower(),
        "doc_type": doc_type or "general",
        "source_unit_count": len(units),
        "prompt_version": PROMPT_VERSION,
        "tenant_id": str(tenant_id or ""),
        "kb_id": str(kb_id or ""),
        "permission_scope": str(permission_scope or "general"),
        "department": str(department or ""),
        "parser_version": str(ast.parser_version or "unknown"),
        "rule_strategy": str(rule_strategy or "unknown"),
        "rule_version": str(rule_version or "unknown"),
    }
    stage_token = None
    if lineage_recorder is not None:
        stage_token = lineage_recorder.begin_stage(
            "chunking_llm_fallback",
            role="chunking",
            engine_type="llm",
            metadata=metadata,
        )

    def finish(status: str, *, reason: str | None = None,
               output_count: int = 0, error_message: str | None = None) -> None:
        if lineage_recorder is None or stage_token is None:
            return
        if status == "skipped":
            lineage_recorder.finish_stage(
                stage_token[0], status=status, started_at=stage_token[1],
                input_count=len(units), skip_reason=reason,
                fallback_reason=fallback_type, metadata=metadata,
            )
            return
        lineage_recorder.finish_stage(
            stage_token[0], status=status, started_at=stage_token[1],
            input_count=len(units), output_count=output_count,
            fallback_reason=reason or fallback_type,
            error_message=error_message,
            prompt_key="rag_chunk_boundaries",
            prompt_version=PROMPT_VERSION,
            rules_version=str(rule_version or "unknown"),
            schema_fingerprint=SCHEMA_FINGERPRINT,
            metadata=metadata,
        )

    if len(units) < 2 or any(not item["id"] for item in units):
        metadata["skip_reason"] = "source_units_below_minimum"
        finish("skipped", reason=metadata["skip_reason"])
        return rule_chunks, metadata

    prompt = _prompt(ast, doc_type, units)
    if len(prompt) > MAX_PROMPT_CHARS:
        metadata["skip_reason"] = "prompt_limit_exceeded"
        finish("skipped", reason=metadata["skip_reason"])
        return rule_chunks, metadata

    model_identity = None
    trace_span = None
    try:
        from backend.config import model_roles
        from backend.core.tool_runtime.deadline import scoped_execution_timeout
        from backend.infra.llm.models import resolve_provider
        from backend.rag.indexing.processing_lineage import ModelIdentity

        effective = model_roles.resolve_effective("chunking")
        try:
            timeout_seconds = float(
                model_roles.resolve_runtime_policy("chunking").timeout_seconds
            )
        except (AttributeError, TypeError, ValueError):
            timeout_seconds = float(
                model_roles.runtime_defaults("chunking").timeout_seconds
            )
        model_name = str(effective.get("value") or "").strip() or None
        model_identity = ModelIdentity(
            role="chunking",
            engine_type="llm",
            provider=resolve_provider(model_name) if model_name else None,
            model_name=model_name,
            config_source=str(effective.get("source") or "") or None,
            config_revision=str(effective.get("updated_at") or "") or None,
        )
        if lineage_recorder is not None and stage_token is not None:
            lineage_recorder.set_stage_model(stage_token[0], model_identity)

        from backend.observability.tracer import SpanKind, trace_collector
        trace_span = trace_collector.start_span(
            "chunking_llm_fallback",
            parent_id=trace_parent_span_id,
            name="LLM 切分边界兜底",
            type="llm_call",
            kind=SpanKind.LLM.value,
            input={
                "source_format": metadata["source_format"],
                "fallback_type": fallback_type,
                "source_unit_count": len(units),
                "role": "chunking",
            },
        )

        from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm
        from backend.rag.preprocessing.chunking import coverage_audit
        from contextlib import nullcontext

        binding = (
            lineage_recorder.bind_stage(stage_token[0])
            if lineage_recorder is not None and stage_token is not None
            else nullcontext()
        )
        metadata["attempted"] = True
        with binding, scoped_execution_timeout(timeout_seconds):
            response = invoke_metadata_llm(
                prompt, role="chunking", max_tokens=2048,
            )
        groups = validate_groups(parse_groups(response), units)
        llm_chunks = build_llm_boundary_chunks(ast, file_path, groups)
        coverage = coverage_audit(ast, llm_chunks)
        if coverage.get("uncovered_count"):
            raise ValueError("coverage_validation_failed")

        metadata.update({
            "applied": True,
            "group_count": len(groups),
            "output_parent_leaf_count": len(llm_chunks),
        })
        trace_collector.end_span(
            trace_span,
            metrics={
                "fallback_type": fallback_type,
                "source_format": metadata["source_format"],
                "source_unit_count": len(units),
                "group_count": len(groups),
                "output_chunk_count": len(llm_chunks),
                "applied": True,
                "model": model_identity.model_name if model_identity else "",
            },
        )
        finish("success", output_count=len(llm_chunks))
        return llm_chunks, metadata
    except Exception as exc:
        metadata["applied"] = False
        from backend.context_budget.errors import ContextBudgetExceededError

        if isinstance(exc, ContextBudgetExceededError):
            error_code = "context_budget_rejected"
        else:
            error_code = str(exc) if isinstance(exc, ValueError) else "provider_or_runtime_error"
        if error_code not in {
            "invalid_json", "invalid_groups", "invalid_source_node_partition",
            "cross_section_group", "coverage_validation_failed", "context_budget_rejected",
        }:
            error_code = "provider_or_runtime_error"
        metadata["failure_type"] = error_code
        metadata["failure_class"] = type(exc).__name__
        frame = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
        if frame is not None:
            metadata["failure_location"] = f"{frame.name}:{frame.lineno}"
        if trace_span is not None:
            try:
                from backend.observability.tracer import trace_collector

                trace_collector.end_span(
                    trace_span,
                    metrics={
                        "fallback_type": fallback_type,
                        "source_format": metadata["source_format"],
                        "source_unit_count": len(units),
                        "failure_type": error_code,
                        "applied": False,
                    },
                    status="error",
                )
            except Exception:
                pass
        finish(
            "fallback", reason=error_code,
            error_message=type(exc).__name__,
        )
        return rule_chunks, metadata

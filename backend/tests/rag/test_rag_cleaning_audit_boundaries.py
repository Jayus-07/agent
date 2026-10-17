"""test_rag_cleaning_audit_boundaries.py — #90 边界专项（2026-10-18）。

三项边界：
1. 逐节点对账：两个节点文本相同、只删除其一，审计不得因"全文还有同样
   文字"而放过被删除节点（全局 blob 对账的假通过）；
2. 长文档抽样：默认全量审计；显式限流时必须给出 sampled=True 且发布门禁
   硬失败，不能把"抽样无缺口"当作全量完整；
3. 敏感信息：清洗删除证据、未覆盖样本、质量记录与 Trace 摘要不得出现
   明文手机号/邮箱/Token/口令；
4. 付费前预检：各失败码都必须在元数据 LLM / Embedding 之前拦截。
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.rag.preprocessing.quality_gate import (
    build_quality_record,
    cleaning_coverage_audit,
    hard_anomalies,
    run_typed_validation,
)

PII_PHONE = "13800138000"
PII_EMAIL = "zhangsan@example.com"
PII_TOKEN = "sk-live-abcdef1234567890"
PII_PASSWORD = "P@ssw0rd-very-secret"


def _leaf(text: str, chunk_id: str, parent_id: str = "") -> Document:
    return Document(
        page_content=text,
        metadata={
            "granularity": "leaf",
            "chunk_id": chunk_id,
            "parent_chunk_id": parent_id,
        },
    )


def _ast(children: list[DocumentNode], raw_text: str = "") -> DocumentAST:
    return DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=children),
        raw_text=raw_text or "".join(node.raw_text or node.text for node in children),
    )


# ── 边界一：逐节点对账，禁止全局文本抵消 ──────────────────────────


def test_identical_text_nodes_are_audited_per_node():
    """两个节点文本相同、只删其中一个：必须发现真实丢失（RED 基线）。"""
    duplicated = "月度质量复盘结论：本季度无重大质量事故。"
    ast = _ast([
        DocumentNode(type="paragraph", text="", raw_text=duplicated),
        DocumentNode(type="paragraph", text=duplicated, raw_text=duplicated),
    ])

    report = cleaning_coverage_audit(ast)

    assert report["uncovered_count"] == 1, report
    assert report["lost_line_count"] == 1
    assert report["uncovered_samples"][0]["lost_samples"] == [duplicated]


def test_per_node_audit_keeps_legal_noise_pass():
    """逐节点对账仍要放过节点内确定性噪音（页码）删除。"""
    ast = _ast([
        DocumentNode(
            type="paragraph",
            text="正文保留。",
            raw_text="正文保留。\n3",
        ),
    ])

    report = cleaning_coverage_audit(ast)

    assert report["uncovered_count"] == 0
    assert report["legal_noise_line_count"] == 1


# ── 边界二：长文档抽样不得假通过 ──────────────────────────────────


def _long_ast(node_count: int, deleted_index: int) -> DocumentAST:
    children = []
    for i in range(node_count):
        text = f"第 {i} 条正文内容：设备巡检记录与交接说明。"
        children.append(DocumentNode(
            type="paragraph",
            text="" if i == deleted_index else text,
            raw_text=text,
        ))
    return _ast(children)


def test_long_document_full_audit_finds_deleted_node():
    """默认全量审计：2001 个节点里被抽掉的那个也必须被发现。"""
    ast = _long_ast(2001, deleted_index=1500)

    report = cleaning_coverage_audit(ast)

    assert report["sampled"] is False
    assert report["audited_node_count"] == 2001
    assert report["uncovered_count"] == 1
    assert report["uncovered_samples"][0]["type"] == "paragraph"


def test_full_audit_is_linear_enough_for_long_documents():
    """全量逐节点审计不得退化成全文反复扫描（长文档保护）。"""
    ast = _long_ast(2001, deleted_index=-1)

    started = time.monotonic()
    report = cleaning_coverage_audit(ast)
    elapsed = time.monotonic() - started

    assert report["uncovered_count"] == 0
    assert elapsed < 10.0, f"全量清洗审计耗时 {elapsed:.2f}s，疑似非线性扫描"


def test_explicit_sampling_cannot_claim_complete_and_gate_fails():
    """显式限流时 sampled=True，且发布门禁必须硬失败（不得假通过）。"""
    ast = _long_ast(5, deleted_index=-1)

    report = cleaning_coverage_audit(ast, max_audit_nodes=2)

    assert report["sampled"] is True
    assert report["audited_node_count"] < report["raw_leaf_total"]

    chunks = [_leaf("正文占位内容用于通过 chunk 存在性检查。", "leaf-0")]
    record = build_quality_record(
        file_path="long.md", doc_id="d-long", kb_id="kb-test", raw_ast=ast,
        chunks=chunks, file_size=1, file_hash="sha256", coverage=None,
        cleaning_coverage=report,
    )
    hard = [a["check"] for a in hard_anomalies(run_typed_validation(record, ast))]
    assert "cleaning_audit_incomplete" in hard, hard


# ── 边界三：敏感信息不得进入证据/记录/Trace ──────────────────────


def _sensitive_ast() -> DocumentAST:
    raw = (
        f"联系人手机 {PII_PHONE}，邮箱 {PII_EMAIL}，"
        f"Token {PII_TOKEN}，口令 {PII_PASSWORD}。"
    )
    return _ast([
        DocumentNode(type="paragraph", text="", raw_text=raw),
    ])


def _assert_no_secret(payload: str, label: str) -> None:
    for secret in (PII_PHONE, PII_EMAIL, PII_TOKEN[:-4], PII_PASSWORD):
        assert secret not in payload, f"{label} 泄露敏感值: {secret}\n{payload[:400]}"


def test_cleaning_audit_samples_are_masked():
    report = cleaning_coverage_audit(_sensitive_ast())

    assert report["uncovered_count"] == 1
    payload = json.dumps(report, ensure_ascii=False)
    _assert_no_secret(payload, "cleaning_coverage_audit")
    sample = report["uncovered_samples"][0]["lost_samples"][0]
    assert "****" in sample  # 手机号保留可对账的掩码形态


def test_cleaner_drop_evidence_is_masked():
    legend = f"*[📞]: 电话 {PII_PHONE} / {PII_EMAIL} / {PII_TOKEN}"
    result = DocumentCleaner().clean(
        f"正文保留内容。\n\n{legend}", source_type="text", structured=True,
    )

    payload = json.dumps(result.dropped, ensure_ascii=False)
    _assert_no_secret(payload, "CleanResult.dropped")
    assert result.dropped and "138****8000" in payload


def test_quality_record_and_gate_details_do_not_leak_secrets():
    ast = _sensitive_ast()
    report = cleaning_coverage_audit(ast)
    chunks = [_leaf("正文占位内容用于通过 chunk 存在性检查。", "leaf-0")]
    record = build_quality_record(
        file_path="pii.md", doc_id="d-pii", kb_id="kb-test", raw_ast=ast,
        chunks=chunks, file_size=1, file_hash="sha256", coverage=None,
        cleaning_coverage=report,
    )
    run_typed_validation(record, ast)

    _assert_no_secret(json.dumps(record, ensure_ascii=False), "quality_record")


def test_coverage_audit_preview_is_masked():
    from backend.rag.preprocessing.chunking import coverage_audit

    raw = f"备用电话是{PII_PHONE}，邮箱 {PII_EMAIL}。"
    ast = _ast([DocumentNode(type="paragraph", text=raw)])
    report = coverage_audit(ast, [])

    assert report["uncovered_count"] == 1
    payload = json.dumps(report, ensure_ascii=False)
    _assert_no_secret(payload, "coverage_audit")


# ── 边界四：付费前预检契约 ────────────────────────────────────────


def _index_harness(tmp_path, monkeypatch, chunks, qc):
    import backend.rag.indexing.indexer as indexer_module
    import backend.rag.preprocessing.pipeline as pipeline_module
    import backend.rag.preprocessing.quality_gate as quality_gate
    from backend.rag.indexing.indexer import IncrementalIndexer

    monkeypatch.setattr(
        pipeline_module, "parse_and_chunk_full",
        lambda _path: (chunks, qc),
    )
    persisted: list[dict] = []
    monkeypatch.setattr(
        quality_gate, "persist_quality_record",
        lambda record: persisted.append(record) or "prepaid-record.json",
    )

    class Registry:
        def __init__(self):
            self.status = "parsing"
            self.updates = []

        def get_by_path(self, _path):
            return {"status": self.status}

        def register_in_progress(self, *_args, **_kwargs):
            self.status = "parsing"

        def update_fields(self, _path, fields):
            self.updates.append(fields)

    class Collector:
        def __init__(self):
            self.spans = {}

        def start(self, **_kwargs):
            return SimpleNamespace(id="trace-prepaid", tags={})

        def start_span(self, span_id, **kwargs):
            span = SimpleNamespace(
                span_id=span_id, input=kwargs.get("input"), metrics={},
                status="running", duration_ms=0,
            )
            self.spans[span_id] = span
            return span

        def end_span(self, span, status="success", metrics=None, **_kwargs):
            span.status = status
            span.metrics.update(metrics or {})

        def finish(self, *_args, **_kwargs):
            return None

    paid_calls: list[str] = []
    vector_writes: list = []

    async def metadata_stub(self, _full_text, base_meta, **_kwargs):
        paid_calls.append("metadata_llm")
        return dict(base_meta)

    registry = Registry()
    collector = Collector()
    monkeypatch.setattr(indexer_module, "trace_collector", collector)
    monkeypatch.setattr(IncrementalIndexer, "_build_doc_metadata", metadata_stub)
    indexer = IncrementalIndexer(
        docs_dir=str(tmp_path),
        vectordb=SimpleNamespace(
            add_documents=lambda docs, **_kwargs: vector_writes.append(docs) or [],
        ),
        doc_db=SimpleNamespace(), embedding=SimpleNamespace(),
        registry=registry, candidate_mode=False,
    )
    document = tmp_path / "doc.txt"
    document.write_text("测试索引内容。" * 5, encoding="utf-8")
    return indexer, document, paid_calls, vector_writes, persisted, registry, collector


def _base_case(cleaning_override=None, chunk_metadata=None, parent_id=""):
    body = "已覆盖的正文内容。" * 8
    raw_ast = _ast([DocumentNode(type="paragraph", text=body)])
    chunks = [_leaf(body, "leaf-0", parent_id=parent_id)]
    metadata = dict(chunks[0].metadata)
    metadata.update(chunk_metadata or {})
    chunks[0].metadata = metadata
    qc = {"raw_ast": raw_ast}
    if cleaning_override is not None:
        qc["cleaning_coverage"] = cleaning_override
    return chunks, qc


_CLEANING_OK = {
    "raw_leaf_total": 1, "audited_node_count": 1, "covered": 1,
    "uncovered_count": 0, "uncovered_samples": [],
    "legal_noise_line_count": 0, "lost_line_count": 0, "sampled": False,
}


@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [
        ("coverage_gap", "coverage_gap"),
        ("cleaner_content_loss", "cleaner_content_loss"),
        ("cleaning_audit_failed", "cleaning_audit_failed"),
        ("parent_references", "parent_references"),
        ("chunks_truncated", "chunks_truncated"),
    ],
)
def test_prepaid_gate_blocks_paid_calls(tmp_path, monkeypatch, scenario, expected_code):
    """每个可在付费前判定的失败码都必须阻止元数据 LLM / Embedding。"""
    cleaning = dict(_CLEANING_OK)
    if scenario == "coverage_gap":
        body = "已覆盖的正文内容。" * 8
        missing = "未进入任何 Leaf 的正文内容。" * 8
        raw_ast = _ast([
            DocumentNode(type="paragraph", text=body),
            DocumentNode(type="paragraph", text=missing),
        ])
        chunks = [_leaf(body, "leaf-0")]
        qc = {"raw_ast": raw_ast, "cleaning_coverage": cleaning}
    elif scenario == "cleaner_content_loss":
        chunks, qc = _base_case(cleaning_override=dict(
            cleaning, uncovered_count=1,
            uncovered_samples=[{"type": "paragraph", "preview": "…"}],
        ))
    elif scenario == "cleaning_audit_failed":
        chunks, qc = _base_case(cleaning_override=dict(
            cleaning, error="RuntimeError: boom",
        ))
    elif scenario == "parent_references":
        chunks, qc = _base_case(parent_id="missing-parent")
        qc["cleaning_coverage"] = cleaning
    else:  # chunks_truncated
        chunks, qc = _base_case(chunk_metadata={"chunks_truncated": True})
        qc["cleaning_coverage"] = cleaning

    indexer, document, paid, vectors, persisted, registry, collector = (
        _index_harness(tmp_path, monkeypatch, chunks, qc)
    )

    with pytest.raises(ValueError) as excinfo:
        indexer._index_file(str(document))

    assert expected_code in str(excinfo.value), str(excinfo.value)
    assert paid == [], f"{expected_code} 未在付费调用前拦截"
    assert vectors == []
    assert persisted, "失败必须留下质量记录"
    assert registry.updates[-1]["quality_status"] == "failed"
    assert collector.spans["index_quality"].status == "error"


def test_prepaid_gate_blocks_duplicate_chunk_identifiers():
    """chunk 身份不唯一同样在付费前拦截（直接调用预检，避免身份协议改写 ID）。"""
    from backend.rag.indexing.indexer import _enforce_prepaid_content_integrity

    body = "重复身份正文内容。" * 8
    raw_ast = _ast([DocumentNode(type="paragraph", text=body)])
    chunks = [_leaf(body, "dup-0"), _leaf(body, "dup-0")]

    with pytest.raises(ValueError) as excinfo:
        _enforce_prepaid_content_integrity(
            SimpleNamespace(registry=None, candidate_mode=False),
            {"raw_ast": raw_ast, "cleaning_coverage": dict(_CLEANING_OK)},
            chunks, "path", {},
            doc_id="d", kb_id="kb", file_path="x.txt",
            file_size=1, file_hash="sha256",
        )

    assert "chunk_identifiers" in str(excinfo.value)

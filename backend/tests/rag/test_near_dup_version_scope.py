"""test_near_dup_version_scope.py — 版本演进豁免（近重复审核摩擦修复）。

场景（验收 UL-02 / UPLOAD-16）：同路径同名上传新版本（v2 内容），与旧版
MinHash 相似度超阈值是版本演进的必然结果，不应被误卡 pending_review；
跨路径的高相似副本不受豁免，照旧进人工审核。
"""
from types import SimpleNamespace

from backend.rag.preprocessing.metadata import compute_minhash
from backend.rag.indexing.stages.metadata_stage import MetadataStage


V1_TEXT = "员工手册(2023 版)。采购金额超过一万元须经三家比价并由财务复核。" * 40
# 版本演进形态：全文一致，仅标题年份更新（MinHash 相似度约 0.91 > 0.85 阈值）
V2_TEXT = V1_TEXT.replace("2023", "2024", 1)
UNRELATED = "本报告汇总了东南亚七国旅游线路的季度销售数据与热门目的地分析。" * 40


def _registry_with(*rows: dict):
    return SimpleNamespace(list_all=lambda: {r["doc_id"]: r for r in rows})


def _row(doc_id: str, sig_text: str, file_path: str, kb_id="kb1",
         tenant_id="t1"):
    import json
    return {
        "doc_id": doc_id,
        "file_path": file_path,
        "kb_id": kb_id,
        "tenant_id": tenant_id,
        "minhash_sig": json.dumps(compute_minhash(sig_text)),
    }


def test_same_path_new_version_exempt_from_near_dup():
    """同租户+同KB+同路径的旧代次被豁免：v2 覆盖上传不再 pending_review。"""
    old = _row("doc-v1", V1_TEXT, file_path="/kb1/员工手册.txt")
    registry = _registry_with(old)
    scope = {"file_path": "/kb1/员工手册.txt", "kb_id": "kb1", "tenant_id": "t1"}

    hit = MetadataStage.detect_near_dup(
        registry, compute_minhash(V2_TEXT), "policy",
        exclude_doc_id="doc-v2", version_scope=scope,
    )
    assert hit == ""


def test_cross_path_duplicate_still_flagged():
    """不同路径的高相似副本（含同 KB）不受豁免，照旧返回 near_dup_id。"""
    other = _row("doc-copy", V1_TEXT, file_path="/kb1/员工手册_副本.txt")
    registry = _registry_with(other)
    scope = {"file_path": "/kb1/员工手册.txt", "kb_id": "kb1", "tenant_id": "t1"}

    hit = MetadataStage.detect_near_dup(
        registry, compute_minhash(V2_TEXT), "policy",
        exclude_doc_id="doc-v2", version_scope=scope,
    )
    assert hit == "doc-copy"


def test_scope_mismatch_kb_or_tenant_not_exempt():
    """同路径但不同 KB / 不同租户：不是本文档的版本演进，不豁免。"""
    cross_kb = _row("doc-v1", V1_TEXT, file_path="/kb1/员工手册.txt", kb_id="kb2")
    registry = _registry_with(cross_kb)
    scope = {"file_path": "/kb1/员工手册.txt", "kb_id": "kb1", "tenant_id": "t1"}

    hit = MetadataStage.detect_near_dup(
        registry, compute_minhash(V2_TEXT), "policy",
        exclude_doc_id="doc-v2", version_scope=scope,
    )
    assert hit == "doc-v1"


def test_scope_requires_all_three_fields():
    """version_scope 字段缺失 → 不豁免（宁保守）；None → 行为同旧版。"""
    old = _row("doc-v1", V1_TEXT, file_path="/kb1/员工手册.txt")
    registry = _registry_with(old)

    # 三要素不全的 scope 视为无豁免
    partial = {"file_path": "/kb1/员工手册.txt", "kb_id": "kb1"}
    assert MetadataStage.detect_near_dup(
        registry, compute_minhash(V2_TEXT), "policy",
        exclude_doc_id="doc-v2", version_scope=partial,
    ) == "doc-v1"

    # base_meta 三要素齐全才构造 scope
    assert MetadataStage._version_scope_from_base_meta(
        {"file_path": "/a.txt", "kb_id": "kb", "tenant_id": "t"},
    ) == {"file_path": "/a.txt", "kb_id": "kb", "tenant_id": "t"}
    assert MetadataStage._version_scope_from_base_meta(
        {"file_path": "/a.txt"},
    ) is None


def test_unrelated_doc_never_flagged():
    """无关负对照：无豁免也无误报。"""
    other = _row("doc-travel", UNRELATED, file_path="/kb1/旅游报告.txt")
    registry = _registry_with(other)
    assert MetadataStage.detect_near_dup(
        registry, compute_minhash(V2_TEXT), "policy",
        exclude_doc_id="doc-v2",
        version_scope={"file_path": "/kb1/员工手册.txt",
                       "kb_id": "kb1", "tenant_id": "t1"},
    ) == ""

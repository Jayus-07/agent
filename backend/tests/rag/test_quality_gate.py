"""§5.1–5.3 质量门禁 + §4 权限范围消费方 测试（2026-09-17 R3）。"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from backend.rag.permissions import (
    is_accessible, normalize_permissions, partition_by_permission,
    required_permissions,
)
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.quality_gate import (
    anomaly_summary,
    build_quality_record,
    hard_anomalies,
    persist_quality_record,
    run_typed_validation,
)


# ============ §4 权限范围：permissions.py ============

class TestPermissions:
    def test_normalize_variants(self):
        assert normalize_permissions("general") == {"general"}
        assert normalize_permissions("a,b") == {"a", "b"}
        assert normalize_permissions(["a", "b"]) == {"a", "b"}
        assert normalize_permissions(None) == set()
        assert normalize_permissions("") == set()

    def test_general_doc_always_accessible(self):
        assert is_accessible({"permission_scope": "general"}, set()) is True
        assert is_accessible({}, None) is True  # 无标记 = general 开放
        assert is_accessible(None, None) is True

    def test_restricted_denied_without_permission(self):
        meta = {"permission_scope": "finance_restricted"}
        assert is_accessible(meta, set()) is False
        assert is_accessible(meta, None) is False  # fail-safe：身份未声明

    def test_restricted_allowed_with_permission(self):
        meta = {"permission_scope": "finance_restricted"}
        assert is_accessible(meta, {"finance_restricted"}) is True

    def test_general_implicit_to_all(self):
        # general 不要求显式持有
        meta = {"permission_scope": "general,finance_restricted"}
        assert is_accessible(meta, {"finance_restricted"}) is True
        assert is_accessible(meta, set()) is False

    def test_required_permissions_strips_general(self):
        assert required_permissions({"permission_scope": "general"}) == set()
        assert required_permissions({}) == set()

    def test_partition_keeps_order(self):
        metas = [
            {"permission_scope": "general"},
            {"permission_scope": "hr_confidential"},
            {"permission_scope": "hr_confidential"},
        ]
        allowed, denied = partition_by_permission(metas, {"hr_confidential"})
        assert allowed == [0, 1, 2]
        allowed, denied = partition_by_permission(metas, set())
        assert allowed == [0]
        assert denied == [1, 2]


# ============ §5.1 质量记录字段 ============

def _make_ast() -> DocumentAST:
    root = DocumentNode(type="section", text="", level=0)
    sec = DocumentNode(type="section", text="一、总则", level=1)
    p1 = DocumentNode(type="paragraph", text="这是第一条内容，包含足够多的文字。" * 3)
    tbl = DocumentNode(type="table", text="表格", rows=[["列A", "列B"], ["1", "2"], ["3", "4"]])
    sec.children.extend([p1, tbl])
    root.children.append(sec)
    ast = DocumentAST(root=root, source_file="demo.md", raw_text="原文内容")
    return ast


def _chunks_from(ast: DocumentAST):
    class _C:
        def __init__(self, meta):
            self.metadata = meta
            self.page_content = "x"
    return [
        _C({"granularity": "leaf", "chunk_id": "d_0"}),
        _C({"granularity": "parent", "chunk_id": "d_p0"}),
        _C({"granularity": "leaf", "chunk_id": "d_1"}),
    ]


class TestQualityRecord:
    def test_s1_full_fields(self):
        ast = _make_ast()
        rec = build_quality_record(
            file_path="/tmp/demo.md", doc_id="demo", kb_id="kb1",
            raw_ast=ast, chunks=_chunks_from(ast),
            file_size=1024, file_hash="ab" * 32,
            doc_type="policy", strategy_name="SectionChunkStrategy",
            filtered_details=[{"reason": "empty"}, {"reason": "too_short"}],
            truncated=True,
            ocr_triggered=False,
        )
        assert rec["file_size"] == 1024
        assert rec["file_sha256"] == "ab" * 32
        assert rec["parsing"]["node_count"] == 4  # root + sec + p1 + tbl
        assert rec["parsing"]["section_count"] == 2  # 虚拟根 + 一级标题
        assert rec["parsing"]["leaf_count"] == 2  # paragraph + table
        assert rec["parsing"]["table_count"] == 1
        assert rec["parsing"]["table_total_rows"] == 3
        assert rec["parsing"]["table_max_cols"] == 2
        assert rec["cleaning"]["raw_chars"] == len("原文内容")
        assert rec["cleaning"]["cleaned_chars"] > 0
        assert rec["chunking"]["chunk_count"] == 3
        assert rec["chunking"]["leaf_chunk_count"] == 2
        assert rec["chunking"]["parent_chunk_count"] == 1
        assert rec["chunking"]["truncated"] is True
        assert rec["chunking"]["filtered_count"] == 2
        assert rec["chunking"]["filtered_reasons"] == {"empty": 1, "too_short": 1}

    def test_s2_traceability_nodes(self):
        ast = _make_ast()
        rec = build_quality_record(
            file_path="/tmp/demo.md", doc_id="demo", kb_id="kb1",
            raw_ast=ast, chunks=[], file_size=1, file_hash="x",
        )
        nodes = rec["traceability"]["nodes"]
        assert nodes[0]["node_id"] == "n0"
        assert all("content_hash" in n and "source_range" in n for n in nodes)
        assert len({n["content_hash"] for n in nodes}) == len(nodes)  # 确定性哈希

    def test_ocr_flag_recorded(self):
        ast = _make_ast()
        rec = build_quality_record(
            file_path="/tmp/scan.pdf", doc_id="scan", kb_id="kb1",
            raw_ast=ast, chunks=[], file_size=1, file_hash="x",
            ocr_triggered=True, ocr_pages=3,
        )
        assert rec["parsing"]["ocr_triggered"] is True
        assert rec["parsing"]["ocr_pages"] == 3
        assert rec["parsing"]["degraded"] is True
        assert rec["parsing"]["degrade_reason"] == "ocr_fallback"


# ============ §5.3 类型化校验 ============

class TestTypedValidation:
    def test_text_doc_passes(self):
        ast = _make_ast()
        for n in (ast.root.children[0].children):
            if n.type == "paragraph":
                n.raw_text = n.text
        rec = build_quality_record(
            file_path="/tmp/demo.md", doc_id="demo", kb_id="kb1",
            raw_ast=ast, chunks=[], file_size=1, file_hash="x",
        )
        anomalies = run_typed_validation(rec, ast)
        hard = hard_anomalies(anomalies)
        assert not hard, f"正常文本不应有硬异常: {hard}"

    def test_empty_content_hard_anomaly(self):
        root = DocumentNode(type="section", text="", level=0)
        ast = DocumentAST(root=root, source_file="empty.md", raw_text="")
        rec = build_quality_record(
            file_path="/tmp/empty.md", doc_id="e", kb_id="kb1",
            raw_ast=ast, chunks=[], file_size=0, file_hash="x",
        )
        anomalies = run_typed_validation(rec, ast)
        hard = hard_anomalies(anomalies)
        assert any(a["check"] in ("cleaned_chars", "leaf_nodes") for a in hard)

    def test_tabular_requires_table(self):
        root = DocumentNode(type="section", text="", level=0)
        ast = DocumentAST(root=root, source_file="t.csv", raw_text="")
        rec = build_quality_record(
            file_path="/tmp/t.csv", doc_id="t", kb_id="kb1",
            raw_ast=ast, chunks=[], file_size=1, file_hash="x",
        )
        rec["format"] = "csv"
        anomalies = run_typed_validation(rec, ast)
        assert any(a["check"] == "table_present" and a["severity"] == "error"
                   for a in anomalies)

    def test_value_loss_warn(self):
        root = DocumentNode(type="section", text="", level=0)
        p = DocumentNode(
            type="paragraph",
            text="详情见内部系统。",  # 原文里的 URL/邮箱/日期全丢了
        )
        root.children.append(p)
        ast = DocumentAST(
            root=root, source_file="v.md",
            raw_text="详情见 https://intra.example.com/x 联系 a@b.com，截止 2026-09-30，编号 12345678。",
        )
        rec = build_quality_record(
            file_path="/tmp/v.md", doc_id="v", kb_id="kb1",
            raw_ast=ast, chunks=[], file_size=1, file_hash="x",
        )
        anomalies = run_typed_validation(rec, ast)
        lost = {a["value_type"] for a in anomalies if a["check"] == "value_loss"}
        assert {"url", "email", "date", "number"} <= lost
        assert all(a["severity"] == "warn" for a in anomalies if a["check"] == "value_loss")

    def test_anomaly_summary_format(self):
        anomalies = [
            {"check": "chunks_truncated", "severity": "warn", "detail": "x"},
            {"check": "cleaned_chars", "severity": "error", "detail": "y"},
        ]
        s = anomaly_summary(anomalies)
        assert s == "quality_anomalies(1):quality_anomaly:chunks_truncated"
        assert anomaly_summary([]) == ""


# ============ 质量记录落盘 ============

class TestPersist:
    def test_persist_atomic_json(self, tmp_path):
        ast = _make_ast()
        rec = build_quality_record(
            file_path="/tmp/demo.md", doc_id="demo", kb_id="kb1",
            raw_ast=ast, chunks=[], file_size=1, file_hash="x",
        )
        path = persist_quality_record(rec, quality_dir=str(tmp_path))
        assert os.path.exists(path)
        assert path.endswith(os.path.join("kb1", "demo.json"))
        loaded = json.load(open(path, encoding="utf-8"))
        assert loaded["doc_id"] == "demo"
        assert not os.path.exists(path + ".tmp")


# ============ registry permission_scope 列（双后端之一：SQLite）============

class TestRegistryPermissionColumn:
    def test_register_and_read(self, tmp_path):
        from backend.rag.indexing.doc_registry import DocumentRegistry
        db = str(tmp_path / "reg.db")
        r = DocumentRegistry(db)
        p = str(tmp_path / "doc.md")
        open(p, "w").write("x")
        r.register(p, doc_id="d1", file_hash="h", kb_id="kb", chunk_ids=[],
                   doc_db_id="", metadata={"permission_scope": "finance_restricted"})
        row = r.get_by_path(p)
        assert row["permission_scope"] == "finance_restricted"
        # 缺省 general
        p2 = str(tmp_path / "doc2.md")
        open(p2, "w").write("x")
        r.register(p2, doc_id="d2", file_hash="h2", kb_id="kb", chunk_ids=[],
                   doc_db_id="")
        assert r.get_by_path(p2)["permission_scope"] == "general"
        # update_fields 白名单放行
        r.update_fields(p, {"permission_scope": "hr_confidential"})
        assert r.get_by_path(p)["permission_scope"] == "hr_confidential"

    def test_legacy_db_migration(self, tmp_path):
        """存量库（无 permission_scope 列）惰性补列。"""
        db = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE doc_registry (file_path TEXT PRIMARY KEY, file_name TEXT, "
            "kb_id TEXT, doc_id TEXT, file_hash TEXT, file_size INTEGER, "
            "file_mtime REAL, chunk_count INTEGER, chunk_ids TEXT, doc_db_id TEXT, "
            "doc_type TEXT, confidence REAL, llm_used INTEGER, quality_score REAL, "
            "quality_issues TEXT, embedding_model TEXT, minhash_sig TEXT, "
            "near_dup_id TEXT, status TEXT, last_indexed TEXT, created_at TEXT, "
            "updated_at TEXT)"
        )
        conn.commit()
        conn.close()
        from backend.rag.indexing.doc_registry import DocumentRegistry
        r = DocumentRegistry(db)  # init 触发迁移
        cols = {row[1] for row in sqlite3.connect(db).execute(
            "PRAGMA table_info(doc_registry)")}
        assert "permission_scope" in cols

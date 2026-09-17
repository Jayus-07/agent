"""test_doc_registry_version_fields.py — R4 版本治理：§6 治理 11 字段迁移测试。

覆盖（2026-09-17 SQLite 轨删除后 PG-only 重写；PG 后端与原 SQLite 同语义）：
  1. register 写入版本治理元数据 → 查询原样返回
  2. update_fields 白名单放行版本字段、拦截未知列
  3. 缺省行为：不传版本元数据 = 非版本链文档（version_id 空）
  4. 版本链往返：v1→v2→v3 supersedes 链 + 生效窗口落库一致

PG 用例带 pg marker，PostgreSQL 不可达时自动 skip（与 test_doc_registry_pg 同约定）。
"""
from __future__ import annotations

import psycopg2
import pytest

from backend.config.database import DOC_REGISTRY_PG_CONFIG
from backend.rag.indexing.doc_registry import DocumentRegistry

PG_TABLE = "doc_registry_r4_version_test"

TRAVEL_CHAIN = {
    "policy_travel_v1": {"version_id": "v1", "effective_from": "2024-04-01",
                          "effective_to": "2025-06-30", "supersedes": None},
    "policy_travel_v2": {"version_id": "v2", "effective_from": "2025-07-01",
                          "effective_to": "2026-08-31", "supersedes": "policy_travel_v1"},
    "policy_travel_v3": {"version_id": "v3", "effective_from": "2026-09-01",
                          "effective_to": None, "supersedes": "policy_travel_v2"},
}


def _version_meta(v: dict) -> dict:
    return {
        "version_id": v.get("version_id", ""),
        "effective_from": v.get("effective_from"),
        "effective_to": v.get("effective_to"),
        "supersedes_version_id": v.get("supersedes") or "",
        "source_priority": 5,
        "quality_status": "pass",
    }


def _pg_alive() -> bool:
    try:
        conn = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


# ── 1: 版本元数据往返 ────────────────────────────────────────────────

@pytest.mark.pg
@pytest.mark.skipif(not _pg_alive(), reason="No PostgreSQL reachable")
def test_pg_register_version_metadata_roundtrip(monkeypatch):
    """register 写入版本治理元数据 → get_by_doc_id 原样返回（010 建表已含 6 列）。"""
    monkeypatch.setenv("DOC_REGISTRY_PG_TABLE", PG_TABLE)
    reg = DocumentRegistry("ignored/path.db")
    reg.clear()
    try:
        reg.register(
            "test://r4/v2.docx", "policy_travel_v2", "hash_v2", "kb_r4",
            ["policy_travel_v2_0"], "db_v2",
            metadata={**_version_meta(TRAVEL_CHAIN["policy_travel_v2"])},
        )
        row = reg.get_by_doc_id("policy_travel_v2")
        assert row["version_id"] == "v2"
        assert row["effective_from"] == "2025-07-01"
        assert row["effective_to"] == "2026-08-31"
        assert row["supersedes_version_id"] == "policy_travel_v1"
        assert row["source_priority"] == 5
        assert row["quality_status"] == "pass"
    finally:
        reg.clear()


# ── 2: update_fields 白名单 ──────────────────────────────────────────

@pytest.mark.pg
@pytest.mark.skipif(not _pg_alive(), reason="No PostgreSQL reachable")
def test_pg_update_fields_whitelist_version_columns(monkeypatch):
    """update_fields 白名单放行版本字段；未知列被静默忽略。"""
    monkeypatch.setenv("DOC_REGISTRY_PG_TABLE", PG_TABLE)
    reg = DocumentRegistry("ignored/path.db")
    reg.clear()
    try:
        reg.register("test://r4/v3.pdf", "policy_travel_v3", "hash_v3", "kb_r4",
                     ["policy_travel_v3_0"], "db_v3", metadata={})
        reg.update_fields("test://r4/v3.pdf", {
            "version_id": "v3",
            "effective_from": "2026-09-01",
            "effective_to": None,
            "quality_status": "pass",
            "source_priority": 3,
            "not_a_column": "x",
        })
        row = reg.get_by_doc_id("policy_travel_v3")
        assert row["version_id"] == "v3"
        assert row["effective_from"] == "2026-09-01"
        assert row["quality_status"] == "pass"
        assert row["source_priority"] == 3
        assert "not_a_column" not in row
    finally:
        reg.clear()


# ── 3: 缺省行为 ──────────────────────────────────────────────────────

@pytest.mark.pg
@pytest.mark.skipif(not _pg_alive(), reason="No PostgreSQL reachable")
def test_pg_register_defaults_non_versioned(monkeypatch):
    """缺省 = 非版本链文档：version_id 空、无生效窗口、quality_status=unknown。"""
    monkeypatch.setenv("DOC_REGISTRY_PG_TABLE", PG_TABLE)
    reg = DocumentRegistry("ignored/path.db")
    reg.clear()
    try:
        reg.register(
            "test://r4/faq.md", "faq_employee", "hash_faq", "kb_r4",
            ["faq_employee_0"], "db_faq", metadata={"doc_type": "faq"},
        )
        row = reg.get_by_doc_id("faq_employee")
        assert row["version_id"] == ""
        assert not row["effective_from"]
        assert not row["effective_to"]
        assert row["supersedes_version_id"] == ""
        assert row["source_priority"] == 0
        assert row["quality_status"] == "unknown"
    finally:
        reg.clear()


# ── 4: 版本链往返 ────────────────────────────────────────────────────

@pytest.mark.pg
@pytest.mark.skipif(not _pg_alive(), reason="No PostgreSQL reachable")
def test_pg_version_chain_roundtrip(monkeypatch):
    """v1→v2→v3 supersedes 链 + 生效窗口全链落库一致（manifest 语义）。"""
    monkeypatch.setenv("DOC_REGISTRY_PG_TABLE", PG_TABLE)
    reg = DocumentRegistry("ignored/path.db")
    reg.clear()
    try:
        for doc_id, v in TRAVEL_CHAIN.items():
            reg.register(
                f"test://r4/{doc_id}.md", doc_id, f"hash_{doc_id}", "kb_r4",
                [f"{doc_id}_0"], f"db_{doc_id}", metadata=_version_meta(v),
            )
        rows = {doc_id: reg.get_by_doc_id(doc_id) for doc_id in TRAVEL_CHAIN}
        assert rows["policy_travel_v1"]["supersedes_version_id"] == ""
        assert rows["policy_travel_v2"]["supersedes_version_id"] == "policy_travel_v1"
        assert rows["policy_travel_v3"]["supersedes_version_id"] == "policy_travel_v2"
        # 窗口首尾衔接：v1.to < v2.from ≤ v2.to < v3.from（文本比较语义成立）
        assert rows["policy_travel_v1"]["effective_to"] < rows["policy_travel_v2"]["effective_from"]
        assert rows["policy_travel_v2"]["effective_to"] < rows["policy_travel_v3"]["effective_from"]
        assert rows["policy_travel_v3"]["effective_to"] in (None, "")
    finally:
        reg.clear()

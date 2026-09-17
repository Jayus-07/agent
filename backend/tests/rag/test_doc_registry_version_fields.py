"""test_doc_registry_version_fields.py — R4 版本治理：§6 治理 11 字段迁移测试。

覆盖：
  1. SQLite/PG 双后端：register 写入版本治理元数据 → 查询原样返回
  2. 存量库惰性补列（老 schema 打开即自动加 6 列，幂等）
  3. update_fields 白名单放行版本字段、拦截未知列
  4. 缺省行为：不传版本元数据 = 非版本链文档（version_id 空）
  5. 版本链往返：v1→v2→v3 supersedes 链 + 生效窗口落库一致

PG 用例带 pg marker，PostgreSQL 不可达时自动 skip（与 test_doc_registry_pg 同约定）。
"""
from __future__ import annotations

import sqlite3

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


# ── 1/2/3/4/5: SQLite ────────────────────────────────────────────────

def test_sqlite_register_version_metadata_roundtrip(tmp_path):
    """register 写入版本治理元数据 → get_by_doc_id 原样返回。"""
    reg = DocumentRegistry(str(tmp_path / "reg.db"))
    reg.register(
        str(tmp_path / "v2.docx"), "policy_travel_v2", "hash_v2", "kb_r4",
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


def test_sqlite_register_defaults_non_versioned(tmp_path):
    """缺省 = 非版本链文档：version_id 空、无生效窗口、quality_status=unknown。"""
    reg = DocumentRegistry(str(tmp_path / "reg.db"))
    reg.register(
        str(tmp_path / "faq.md"), "faq_employee", "hash_faq", "kb_r4",
        ["faq_employee_0"], "db_faq", metadata={"doc_type": "faq"},
    )
    row = reg.get_by_doc_id("faq_employee")
    assert row["version_id"] == ""
    assert not row["effective_from"]
    assert not row["effective_to"]
    assert row["supersedes_version_id"] == ""
    assert row["source_priority"] == 0
    assert row["quality_status"] == "unknown"


def test_sqlite_legacy_db_lazy_column_migration(tmp_path):
    """存量库（无 6 个版本列）打开即惰性补列，旧数据可读、幂等可重入。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    # R3 时点完整 schema（doc_registry.py SCHEMA_SQL 截至 permission_scope），
    # 仅缺 R4 新增的 6 个版本治理列
    conn.executescript("""
        CREATE TABLE doc_registry (
            file_path TEXT PRIMARY KEY, file_name TEXT NOT NULL,
            kb_id TEXT NOT NULL, doc_id TEXT NOT NULL, file_hash TEXT NOT NULL,
            file_size INTEGER NOT NULL, file_mtime REAL NOT NULL,
            chunk_count INTEGER DEFAULT 0, chunk_ids TEXT DEFAULT '[]',
            doc_db_id TEXT, doc_type TEXT DEFAULT 'general',
            confidence REAL DEFAULT 0, llm_used INTEGER DEFAULT 0,
            quality_score REAL DEFAULT 0, quality_issues TEXT DEFAULT '',
            embedding_model TEXT DEFAULT '', minhash_sig TEXT DEFAULT '',
            near_dup_id TEXT DEFAULT '', status TEXT DEFAULT 'active',
            last_indexed TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            metadata_fingerprint TEXT DEFAULT '',
            doc_version INTEGER DEFAULT 1, kb_version TEXT DEFAULT 'v1',
            department TEXT DEFAULT '', summary TEXT DEFAULT '',
            keywords TEXT DEFAULT '', time_refs TEXT DEFAULT '',
            business_domain TEXT DEFAULT '', complexity TEXT DEFAULT '',
            permission_scope TEXT DEFAULT 'general'
        );
        INSERT INTO doc_registry (file_path, file_name, kb_id, doc_id, file_hash,
                                  file_size, file_mtime)
        VALUES ('/legacy/old.md', 'old.md', 'kb_r4', 'legacy_doc', 'hash_old', 1, 1.0);
    """)
    conn.commit()
    conn.close()

    reg = DocumentRegistry(str(db))
    # 存量行可读，版本字段为缺省值
    row = reg.get_by_doc_id("legacy_doc")
    assert row["version_id"] == ""
    assert row["quality_status"] == "unknown"
    # 补列后可正常写版本元数据（老行 upsert 不受影响）
    reg.register(
        "/legacy/old.md", "legacy_doc", "hash_new", "kb_r4",
        ["legacy_doc_0"], "db_old",
        metadata={**_version_meta(TRAVEL_CHAIN["policy_travel_v3"])},
    )
    row = reg.get_by_doc_id("legacy_doc")
    assert row["version_id"] == "v3"
    # 二次打开（幂等重入）不报错
    reg2 = DocumentRegistry(str(db))
    assert reg2.get_by_doc_id("legacy_doc")["version_id"] == "v3"


def test_sqlite_update_fields_whitelist_version_columns(tmp_path):
    """update_fields 白名单放行版本字段；未知列被静默忽略。"""
    reg = DocumentRegistry(str(tmp_path / "reg.db"))
    path = str(tmp_path / "v1.md")
    reg.register(path, "policy_travel_v1", "hash_v1", "kb_r4",
                 ["policy_travel_v1_0"], "db_v1", metadata={})
    reg.update_fields(path, {
        "version_id": "v1",
        "effective_from": "2024-04-01",
        "effective_to": "2025-06-30",
        "quality_status": "soft_warning",
        "source_priority": 3,
        "not_a_column": "注入尝试应被忽略",
    })
    row = reg.get_by_doc_id("policy_travel_v1")
    assert row["version_id"] == "v1"
    assert row["effective_from"] == "2024-04-01"
    assert row["effective_to"] == "2025-06-30"
    assert row["quality_status"] == "soft_warning"
    assert row["source_priority"] == 3
    assert "not_a_column" not in row


def test_sqlite_version_chain_roundtrip(tmp_path):
    """v1→v2→v3 supersedes 链 + 生效窗口全链落库一致（manifest 语义）。"""
    reg = DocumentRegistry(str(tmp_path / "reg.db"))
    for i, (doc_id, v) in enumerate(TRAVEL_CHAIN.items()):
        reg.register(
            str(tmp_path / f"{doc_id}.md"), doc_id, f"hash_{doc_id}", "kb_r4",
            [f"{doc_id}_0"], f"db_{doc_id}", metadata=_version_meta(v),
        )
    rows = {reg.get_by_doc_id(doc_id)["version_id"]: reg.get_by_doc_id(doc_id)
            for doc_id in TRAVEL_CHAIN}
    assert rows["v1"]["supersedes_version_id"] == ""
    assert rows["v2"]["supersedes_version_id"] == "policy_travel_v1"
    assert rows["v3"]["supersedes_version_id"] == "policy_travel_v2"
    # 窗口首尾衔接：v1.to < v2.from ≤ v2.to < v3.from（文本比较语义成立）
    assert rows["v1"]["effective_to"] < rows["v2"]["effective_from"]
    assert rows["v2"]["effective_to"] < rows["v3"]["effective_from"]
    assert rows["v3"]["effective_to"] in (None, "")


# ── PG 同构验收 ──────────────────────────────────────────────────────

@pytest.mark.pg
@pytest.mark.skipif(not _pg_alive(), reason="No PostgreSQL reachable")
def test_pg_register_version_metadata_roundtrip(monkeypatch, tmp_path):
    """PG 后端版本元数据往返（与 SQLite 同语义；010 建表已含 6 列）。"""
    monkeypatch.setenv("DOC_REGISTRY_BACKEND", "postgres")
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


@pytest.mark.pg
@pytest.mark.skipif(not _pg_alive(), reason="No PostgreSQL reachable")
def test_pg_update_fields_whitelist_version_columns(monkeypatch):
    """PG update_fields 白名单与 SQLite 一致。"""
    monkeypatch.setenv("DOC_REGISTRY_BACKEND", "postgres")
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
            "not_a_column": "x",
        })
        row = reg.get_by_doc_id("policy_travel_v3")
        assert row["version_id"] == "v3"
        assert row["effective_from"] == "2026-09-01"
        assert row["quality_status"] == "pass"
        assert "not_a_column" not in row
    finally:
        reg.clear()

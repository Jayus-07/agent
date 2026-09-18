from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.ingest_eval_fixtures import (  # noqa: E402
    DEPRECATED_ALIAS_KBS,
    activate_reviewed_migration_copies,
)


class _StubRegistry:
    """内存注册表：只实现激活逻辑用到的三个方法。"""

    def __init__(self, rows: dict[str, dict]):
        self._rows = rows

    def get_by_path(self, file_path: str) -> dict | None:
        return self._rows.get(file_path)

    def get_by_doc_id(self, doc_id: str) -> dict | None:
        for row in self._rows.values():
            if row.get("doc_id") == doc_id:
                return row
        return None

    def update_status(self, file_path: str, status: str) -> None:
        self._rows[file_path]["status"] = status


def _rows() -> dict[str, dict]:
    return {
        # 迁移副本（待激活判定）
        "/docs/rag_eval_kb/a.md": {
            "file_path": "/docs/rag_eval_kb/a.md", "doc_id": "a",
            "status": "pending_review", "near_dup_id": "legacy-1",
        },
        "/docs/rag_eval_kb/b.md": {
            "file_path": "/docs/rag_eval_kb/b.md", "doc_id": "b",
            "status": "pending_review", "near_dup_id": "same-kb-9",
        },
        "/docs/rag_eval_kb/c.md": {
            "file_path": "/docs/rag_eval_kb/c.md", "doc_id": "c",
            "status": "pending_review", "near_dup_id": "",
        },
        "/docs/rag_eval_kb/d.md": {
            "file_path": "/docs/rag_eval_kb/d.md", "doc_id": "d",
            "status": "active", "near_dup_id": "",
        },
        "/docs/rag_eval_kb/e.md": {
            "file_path": "/docs/rag_eval_kb/e.md", "doc_id": "e",
            "status": "pending_review", "near_dup_id": "ghost-id",
        },
        # 近重复源文档
        "/docs/rag_100_docs/legacy-1": {
            "file_path": "/docs/rag_100_docs/legacy-1", "doc_id": "legacy-1",
            "kb_id": "rag_100_docs", "status": "active",
        },
        "/docs/rag_eval_kb/same-kb-9": {
            "file_path": "/docs/rag_eval_kb/same-kb-9", "doc_id": "same-kb-9",
            "kb_id": "rag_eval_kb", "status": "active",
        },
    }


def test_deprecated_alias_duplicates_are_activated() -> None:
    rows = _rows()
    registry = _StubRegistry(rows)
    paths = {slug: f"/docs/rag_eval_kb/{slug}.md" for slug in "abcde"}

    activated = activate_reviewed_migration_copies(registry, paths)

    assert activated == ["a"]
    assert rows["/docs/rag_eval_kb/a.md"]["status"] == "active"
    assert DEPRECATED_ALIAS_KBS == {"rag_100_docs", "rag_test_kb"}


def test_same_kb_or_unknown_duplicates_stay_pending() -> None:
    """同 KB 内重复与查无来源的近重复必须留给人工，不得自动激活。"""
    rows = _rows()
    registry = _StubRegistry(rows)
    paths = {slug: f"/docs/rag_eval_kb/{slug}.md" for slug in "abcde"}

    # same-kb-9 → rag_eval_kb（同 KB）；ghost-id → 查无此文档
    activated = activate_reviewed_migration_copies(registry, paths)

    assert rows["/docs/rag_eval_kb/b.md"]["status"] == "pending_review"
    assert rows["/docs/rag_eval_kb/e.md"]["status"] == "pending_review"
    assert "b" not in activated and "e" not in activated


def test_active_rows_and_clean_rows_untouched() -> None:
    rows = _rows()
    registry = _StubRegistry(rows)
    paths = {slug: f"/docs/rag_eval_kb/{slug}.md" for slug in "abcde"}

    activate_reviewed_migration_copies(registry, paths)

    assert rows["/docs/rag_eval_kb/d.md"]["status"] == "active"
    assert rows["/docs/rag_eval_kb/c.md"]["status"] == "pending_review"

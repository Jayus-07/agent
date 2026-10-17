"""reindex 失败必须恢复旧 active 快照（2026-10-18）。

真实路径里 _index_file → register_in_progress 会先把 registry 行改成
parsing + 新 file_hash；若随后失败而旧向量按"先写后删"保留，registry
就与内容不一致：
  - status=parsing → 检索可见性（status=active 正向校验）隐藏旧版本；
  - file_hash=新指纹 → 指向尚未成功索引的内容。
本用例锁定 reindex_file 的失败恢复契约。
"""
from __future__ import annotations

import pytest

from backend.rag.indexing.indexer import IncrementalIndexer


class FakeRegistry:
    def __init__(self, row):
        self.row = dict(row)
        self.calls = []

    def get_by_path(self, _path):
        return dict(self.row)

    def update_status(self, _path, status):
        self.row["status"] = status
        self.calls.append(("status", status))

    def update_fields(self, _path, fields):
        self.row.update({k: v for k, v in fields.items() if k != "file_hash" or True})
        self.calls.append(("fields", dict(fields)))

    def bump_doc_version(self, _doc_id, delta=0):
        return 1


def _indexer(tmp_path, registry):
    return IncrementalIndexer(
        docs_dir=str(tmp_path), vectordb=object(), doc_db=object(),
        embedding=object(), registry=registry, kb_id="kb", candidate_mode=False,
    )


def test_failed_reindex_restores_previous_active_snapshot(tmp_path):
    target = tmp_path / "doc.md"
    target.write_text("body", encoding="utf-8")
    registry = FakeRegistry({
        "doc_id": "did1", "status": "active", "file_hash": "old_hash",
        "chunk_ids": '["c1", "c2"]', "doc_db_id": "ddb-old",
        "doc_type": "general",
    })
    indexer = _indexer(tmp_path, registry)

    def failing_index(path, **_kwargs):
        # 模拟真实路径：register_in_progress 先写 parsing + 新指纹，然后失败
        registry.update_status(path, "parsing")
        registry.update_fields(path, {"file_hash": "new_hash"})
        raise ValueError("质量门禁硬异常(付费前预检): coverage_gap")

    indexer._index_file = failing_index

    with pytest.raises(ValueError):
        indexer.reindex_file(str(target))

    assert registry.row["status"] == "active", "失败更新不得让旧 active 记录掉活"
    assert registry.row["file_hash"] == "old_hash", "失败后指纹必须回到上次成功版本"


def test_failed_first_index_keeps_failed_semantics(tmp_path):
    """首次索引失败（无旧版本）不得被"恢复 active"逻辑伪造成功。"""
    target = tmp_path / "new.md"
    target.write_text("body", encoding="utf-8")
    registry = FakeRegistry({
        "doc_id": "did2", "status": "", "file_hash": "",
        "chunk_ids": "[]", "doc_db_id": "", "doc_type": "general",
    })
    indexer = _indexer(tmp_path, registry)

    def failing_index(path, **_kwargs):
        registry.update_status(path, "parsing")
        raise ValueError("parse failed")

    indexer._index_file = failing_index

    with pytest.raises(ValueError):
        indexer.reindex_file(str(target))

    assert registry.row["status"] == "parsing"

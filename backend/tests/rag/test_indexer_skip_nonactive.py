"""STOP C 回归：sync 不再按 ADDED 重试 hash 未变的 pending_review/failed 文件。

背景（2026-09-28 实测事故）：sync 只认 active 注册行，near-dup 待审与
结构性失败（0-chunk CSV）文件每次启动都被当 ADDED 重走元数据 LLM
（~20s/文件，一次启动重处理 89 个），且每次重试追加 failed 行。
"""
import pytest

from backend.rag.indexing.indexer import IncrementalIndexer


def _disk(hash_a: str, hash_b: str) -> dict[str, tuple[str, int, float]]:
    return {
        "/data/a.md": (hash_a, 10, 0.0),
        "/data/b.csv": (hash_b, 10, 0.0),
    }


class TestFilterNonactiveAdditions:
    def test_skip_unchanged_pending_and_failed(self):
        disk = _disk("h1", "h2")
        nonactive = {
            "/data/a.md": {"status": "pending_review", "file_hash": "h1"},
            "/data/b.csv": {"status": "failed", "file_hash": "h2"},
        }
        keep, skipped = IncrementalIndexer._filter_nonactive_additions(
            disk, nonactive, ["/data/a.md", "/data/b.csv"])
        assert keep == []
        assert skipped == {"/data/a.md", "/data/b.csv"}

    def test_retry_when_hash_changed(self):
        """内容真改了（hash 变）→ 仍按 ADDED 重评，不能漏。"""
        disk = _disk("h1-new", "h2")
        nonactive = {
            "/data/a.md": {"status": "failed", "file_hash": "h1-old"},
            "/data/b.csv": {"status": "pending_review", "file_hash": "h2"},
        }
        keep, skipped = IncrementalIndexer._filter_nonactive_additions(
            disk, nonactive, ["/data/a.md", "/data/b.csv"])
        assert keep == ["/data/a.md"]
        assert skipped == {"/data/b.csv"}

    def test_new_file_without_row_is_kept(self):
        disk = _disk("h1", "h2")
        keep, skipped = IncrementalIndexer._filter_nonactive_additions(
            disk, {}, ["/data/a.md", "/data/b.csv"])
        assert keep == ["/data/a.md", "/data/b.csv"]
        assert skipped == set()

    def test_active_files_never_enter_filter_scope(self):
        """active 行不进 nonactive_registry（sync 上游已按 active diff），
        这里只锁过滤函数对陌生路径的保守行为：不跳过。"""
        disk = _disk("h1", "h2")
        nonactive = {"/data/other.md": {"status": "failed", "file_hash": "h1"}}
        keep, skipped = IncrementalIndexer._filter_nonactive_additions(
            disk, nonactive, ["/data/a.md"])
        assert keep == ["/data/a.md"]
        assert skipped == set()

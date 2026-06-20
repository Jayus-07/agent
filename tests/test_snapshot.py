"""Snapshot 测试 — 数据快照存取"""
import pytest
import json
import os

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_agent.snapshot import (
    save_snapshot, load_snapshot, load_latest_snapshot,
    list_snapshots, cleanup_old_snapshots,
)
# 临时覆盖快照目录
import report_agent.snapshot as snap_module


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    """使用临时目录作为快照目录"""
    snap_dir = tmp_path / "snapshots"
    monkeypatch.setattr(snap_module, "_get_snapshot_dir", lambda: str(snap_dir))
    return str(snap_dir)


class TestSnapshot:
    """数据快照存取"""

    def test_save_and_load(self, snapshot_dir):
        result = {"data": [{"id": 1, "name": "test"}],
                   "metadata": {"fetched_at": "2026-01-01"}}
        path = save_snapshot("test_report", result)
        assert os.path.exists(path)
        assert path.endswith(".json")

        loaded = load_snapshot(path)
        assert loaded is not None
        assert loaded["report_type"] == "test_report"
        assert loaded["data"] == [{"id": 1, "name": "test"}]

    def test_save_with_filters(self, snapshot_dir):
        result = {"data": [], "metadata": {}}
        path = save_snapshot("test_report", result,
                              filters={"dept": "技术部"}, rendered="# 报告")
        loaded = load_snapshot(path)
        assert loaded["filters"] == {"dept": "技术部"}
        assert loaded["rendered"] == "# 报告"

    def test_load_nonexistent(self, snapshot_dir):
        loaded = load_snapshot("/nonexistent/path.json")
        assert loaded is None

    def test_latest_snapshot(self, snapshot_dir):
        result1 = {"data": [{"v": 1}], "metadata": {}}
        result2 = {"data": [{"v": 2}], "metadata": {}}
        save_snapshot("my_report", result1)
        # 延迟确保时间戳不同（Windows 文件系统时间精度较低）
        import time; time.sleep(1.5)
        save_snapshot("my_report", result2)

        latest = load_latest_snapshot("my_report")
        assert latest is not None
        assert latest["data"] == [{"v": 2}]

    def test_latest_nonexistent_type(self, snapshot_dir):
        latest = load_latest_snapshot("nonexistent_type")
        assert latest is None

    def test_list_snapshots(self, snapshot_dir):
        save_snapshot("list_test", {"data": [1], "metadata": {}})
        save_snapshot("list_test", {"data": [2], "metadata": {}})

        snaps = list_snapshots("list_test")
        assert len(snaps) == 2
        # 按时间倒序
        assert snaps[0]["name"].endswith(".json")
        assert "saved_at" in snaps[0]
        assert snaps[0]["size"] > 0

    def test_list_empty(self, snapshot_dir):
        snaps = list_snapshots("empty_type")
        assert snaps == []

    def test_list_with_limit(self, snapshot_dir):
        for i in range(5):
            save_snapshot("limit_test", {"data": [i], "metadata": {}})
        snaps = list_snapshots("limit_test", limit=2)
        assert len(snaps) == 2

    def test_cleanup_old(self, snapshot_dir):
        """清理过期快照"""
        result = {"data": [1], "metadata": {}}
        path = save_snapshot("cleanup_test", result)

        # 手动修改文件时间戳为 365 天前，确保 retention_days=1 能清理
        old_time = os.path.getmtime(path) - 86400 * 365
        os.utime(path, (old_time, old_time))

        removed = cleanup_old_snapshots(retention_days=1)
        assert removed >= 1

    def test_snapshot_contains_required_fields(self, snapshot_dir):
        result = {"data": [{"x": 1}], "metadata": {"key": "val"}}
        path = save_snapshot("fields_test", result)
        loaded = load_snapshot(path)
        assert "report_type" in loaded
        assert "saved_at" in loaded
        assert "filters" in loaded
        assert "data" in loaded
        assert "metadata" in loaded
        assert "rendered" in loaded

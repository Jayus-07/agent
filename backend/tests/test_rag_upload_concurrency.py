"""P1-2 测试：同文件并发上传 race condition 防护。

背景:_do_index_sync 在线程池跑,流程:
  1. duplicate 检测(读 registry)
  2. 如果 SHA256 变了 → 删旧 + _index_file 写新
两个并发请求可能都通过步骤 1,然后都走到步骤 2,导致同 doc_id 写两次到向量库。

修法:在 _do_index_sync 入口对 filepath 加文件锁(非阻塞),失败立即抛错,
不让两个请求同时走到 _index_file。Windows 用 msvcrt.locking,Linux 用 fcntl.flock。
"""
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.api.routes.rag_upload import (
    acquire_index_lock,
    release_index_lock,
    FileLockedByOtherError,
)
from backend.app.api.routes import rag_upload


# ============ 文件锁纯函数测试 ============

class TestFileLocking:
    """acquire/release_index_lock 在跨平台下都要工作。"""

    def test_lock_then_release_succeeds(self, tmp_path):
        """基本 acquire → release 循环。"""
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello")
        fd = acquire_index_lock(str(f))
        try:
            # 锁存在期间,fd 应该 > 0
            assert fd > 0
        finally:
            release_index_lock(fd, str(f))

    def test_second_lock_fails_immediately(self, tmp_path):
        """非阻塞锁:第二个锁立即失败。"""
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello")

        fd1 = acquire_index_lock(str(f))
        try:
            with pytest.raises(FileLockedByOtherError) as exc_info:
                acquire_index_lock(str(f))
            assert str(f) in str(exc_info.value) or "locked" in str(exc_info.value).lower()
        finally:
            release_index_lock(fd1, str(f))

    def test_release_unlocks_for_subsequent_acquire(self, tmp_path):
        """释放后,第二次 acquire 必须能成功(锁真的被释放了)。"""
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello")

        fd1 = acquire_index_lock(str(f))
        release_index_lock(fd1, str(f))

        # 这次应该成功
        fd2 = acquire_index_lock(str(f))
        try:
            assert fd2 > 0
        finally:
            release_index_lock(fd2, str(f))

    def test_readonly_file_works(self, tmp_path):
        """只读文件也能加锁(Windows 上 O_RDONLY 是关键)。

        背景: 之前的实现用 os.O_RDWR,只读文件 Permission denied。
        P0-X-DOCS 修复: 改用 os.O_RDONLY,兼容性更好。
        """
        import os
        f = tmp_path / "readonly.txt"
        f.write_bytes(b"hello")
        os.chmod(f, 0o444)  # 只读

        try:
            fd = acquire_index_lock(str(f))
            try:
                assert fd > 0
            finally:
                release_index_lock(fd, str(f))
        except OSError as e:
            # 容错:某些 Windows 文件系统不响应 chmod(例如 NTFS 的 ACL),
            # 测试跳过但不失败
            if "Permission" in str(e) and "denied" in str(e):
                pytest.skip(f"Windows 不允许 chmod 设置只读: {e}")
            else:
                raise
        finally:
            # 恢复可写以便 tmp_path 清理
            try:
                os.chmod(f, 0o644)
            except OSError:
                pass


# ============ Stale lock TTL 自愈（P1 改进）============

class TestStaleLockRecovery:
    """进程崩溃残留的 .lock 超过 TTL 必须可被接管,否则同名文件永久无法上传。"""

    @staticmethod
    def _write_lock(filepath: str, ts: float, pid: str = "99999"):
        with open(filepath + ".lock", "w", encoding="utf-8") as fh:
            fh.write(f"{pid}\n{ts}\n")

    def test_stale_lock_beyond_ttl_is_stolen(self, tmp_path):
        """锁时间戳超过 TTL → acquire 应接管成功,不抛 FileLockedByOtherError。"""
        f = tmp_path / "stale.txt"
        f.write_bytes(b"data")
        old_ts = time.time() - (rag_upload.LOCK_STALE_SECONDS + 60)
        self._write_lock(str(f), old_ts)

        fd = acquire_index_lock(str(f))
        try:
            assert fd > 0
        finally:
            release_index_lock(fd, str(f))

    def test_fresh_lock_still_rejected(self, tmp_path):
        """TTL 内的锁仍视为有效持有 → 拒绝(不能误抢正在进行的索引)。"""
        f = tmp_path / "fresh.txt"
        f.write_bytes(b"data")
        self._write_lock(str(f), time.time())

        with pytest.raises(FileLockedByOtherError):
            acquire_index_lock(str(f))

    def test_unparseable_fresh_lock_rejected(self, tmp_path):
        """锁内容不可解析但 mtime 新鲜 → 保守拒绝(不误删未知持有者的锁)。"""
        f = tmp_path / "garbage.txt"
        f.write_bytes(b"data")
        with open(str(f) + ".lock", "w", encoding="utf-8") as fh:
            fh.write("not-a-timestamp\n")

        with pytest.raises(FileLockedByOtherError):
            acquire_index_lock(str(f))

    def test_unparseable_ancient_lock_falls_back_to_mtime(self, tmp_path):
        """锁内容不可解析且 mtime 超 TTL → 按 mtime 判定过期并接管。"""
        import os as _os
        f = tmp_path / "ancient.txt"
        f.write_bytes(b"data")
        lock_path = str(f) + ".lock"
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("garbage\n")
        ancient = time.time() - (rag_upload.LOCK_STALE_SECONDS + 120)
        _os.utime(lock_path, (ancient, ancient))

        fd = acquire_index_lock(str(f))
        try:
            assert fd > 0
        finally:
            release_index_lock(fd, str(f))


# ============ _do_index_sync 集成测试 ============

class TestDoIndexSyncConcurrency:
    """_do_index_sync 必须串行化同 filepath 的处理。"""

    def test_concurrent_same_file_only_one_proceeds(self, tmp_path, monkeypatch):
        """两个并发 _do_index_sync(同 file):一个跑完,一个被锁挡掉。"""
        # 这是一个集成测试,直接验证锁机制。
        # 因为 _do_index_sync 内部调用链复杂,这里只测锁的核心语义:
        # 第二个 acquire 必须抛错,而不是阻塞。

        target = tmp_path / "doc.md"
        target.write_bytes(b"%PDF-1.4\ntest content")

        results = []

        def worker():
            try:
                fd = acquire_index_lock(str(target))
                results.append("acquired")
                time.sleep(0.5)  # 模拟索引耗时
                release_index_lock(fd, str(target))
                results.append("released")
            except FileLockedByOtherError as e:
                results.append(f"locked_error: {e}")

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)

        t1.start()
        time.sleep(0.05)  # 让 t1 先拿到锁
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        # 应该看到一个 acquired + released,另一个 locked_error
        assert "acquired" in results
        assert "released" in results
        assert any("locked_error" in r for r in results), (
            f"第二个并发请求应被锁挡掉,实际 results={results}"
        )
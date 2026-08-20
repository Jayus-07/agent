"""sync_upload_impl 核心上传逻辑 — 特征化测试（第一步安全网）。

背景:多智能体项目测试审计发现,rag_upload.py 中 ~140 行核心上传逻辑
(sync_upload_impl)几乎没有测试覆盖。现有测试只测了三个纯辅助函数
(_validate_mime / 文件锁 / _cleanup_failed_upload)。

本文件补齐核心路径测试(特征化测试 — 锁定当前行为,作为后续改造的安全网):
  1. 路径穿越防护(../、反斜杠、. 开头文件名)
  2. 流式大小限制(超限拒绝 + 临时文件清理)
  3. 空文件拒绝
  4. 魔数校验(PDF %PDF- / DOCX PK\x03\x04)
  5. 中文文件名编码修复(latin-1 mojibake → utf-8)
  6. 成功流程(atomic rename、覆盖检测、返回结构)
  7. HTTP 端点级(UploadFile 替换为 fake,不起真实索引)

不依赖 FastAPI app 的部分直接调 sync_upload_impl;端点级测试用 TestClient。
"""
import asyncio
import os
import time
from pathlib import Path

import pytest

import backend.config.database as config_database
from backend.app.api.routes import rag_upload
from backend.app.api.routes.rag_upload import sync_upload_impl, sha256_of_file


# ============ Fake 对象 ============

class FakeUploadFile:
    """最小 UploadFile 替身:async read(n) 按块返回。"""

    def __init__(self, filename: str, data: bytes, content_type: str = "text/markdown"):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._pos = 0

    async def read(self, n: int = -1) -> bytes:
        if n <= 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
        return chunk


class _FakeClient:
    host = "127.0.0.1"


class FakeRequest:
    """最小 Request 替身:headers + client(供 _extract_source)。"""

    def __init__(self, content_length: int | None = None, batch_id: str | None = None):
        self.client = _FakeClient()
        self.headers = {"user-agent": "pytest-fake"}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        if batch_id:
            self.headers["X-Batch-Id"] = batch_id


# ============ 测试基建 ============

@pytest.fixture
def upload_env(tmp_path, monkeypatch):
    """隔离的上传环境:docs_dir + tmp_dir + patch 模块级状态。

    返回 (docs_dir, tmp_dir)。DOCS_DIRECTORY 指向 tmp_path/docs,
    _progress_queues 替换为新 dict 避免测试间污染。
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    tmp_dir = tmp_path / "upload_tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(config_database, "DOCS_DIRECTORY", str(docs_dir))
    monkeypatch.setattr(rag_upload, "_progress_queues", {})
    return docs_dir, tmp_dir


def run_impl(docs_dir, tmp_dir, file, request=None, max_size: int = 10 * 1024 * 1024,
             chunk_size: int = 64, kb_id: str = "kb1", department: str = "general") -> dict:
    """直接调 sync_upload_impl(asyncio.run),返回结果 dict。"""
    request = request or FakeRequest()
    return asyncio.run(sync_upload_impl(
        file, request, max_size, str(tmp_dir), chunk_size,
        emit_bytes=1024 * 1024, emit_ms=10 ** 9,
        kb_id=kb_id, department=department,
    ))


def files_in(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# ============ 1. 路径穿越防护 ============

class TestPathTraversal:
    """恶意文件名绝不能落盘到 docs_dir 之外。"""

    def test_dotdot_filename_sanitized_inside_docs(self, upload_env):
        """../../evil.md → basename 净化为 evil.md,落在 docs 内。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("../../evil.md", b"# escaped?", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)

        assert result["ok"] is True, f"净化后应允许上传: {result}"
        # 关键:落盘路径必须在 docs_dir 内
        final = Path(result["filepath"])
        assert os.path.commonpath([str(docs_dir), str(final)]) == str(docs_dir)
        assert final.exists() and final.read_bytes() == b"# escaped?"
        # docs 上级目录绝不能有 evil.md
        assert not (docs_dir.parent / "evil.md").exists()

    def test_backslash_traversal_no_escape(self, upload_env):
        """..\\..\\evil.md — Windows 下 basename 净化;其它平台拒/净化均可,
        唯一硬性要求:不能逃逸到 docs_dir 外。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("..\\..\\evil.md", b"win escape", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)

        if result["ok"]:
            final = Path(result["filepath"])
            assert os.path.commonpath([str(docs_dir), str(final)]) == str(docs_dir)
        assert not (docs_dir.parent / "evil.md").exists()
        assert not (docs_dir.parent.parent / "evil.md").exists()

    def test_hidden_dotfile_rejected(self, upload_env):
        """'.hidden.md' 以点开头 → 拒绝(invalid filename)。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile(".hidden.md", b"x", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is False
        assert "invalid filename" in result["error"]

    def test_empty_filename_rejected(self, upload_env):
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("", b"x", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is False
        assert "invalid filename" in result["error"]

    def test_none_filename_rejected(self, upload_env):
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile(None, b"x", "text/markdown")
        f.filename = None
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is False

    def test_traversal_leaves_no_tmp_file(self, upload_env):
        """被拒的穿越文件名不应在 tmp_dir 留残留。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile(".secret.md", b"x", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is False
        assert files_in(tmp_dir) == [], f"tmp_dir 残留: {files_in(tmp_dir)}"


# ============ 2. 文件大小限制 ============

class TestSizeLimit:
    """流式写入中的大小限制(双保险的第二道)。"""

    def test_oversize_rejected_during_stream(self, upload_env):
        """max_size=100,上传 200 字节 → 中途拒绝。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("big.md", b"A" * 200, "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f, max_size=100, chunk_size=32)

        assert result["ok"] is False
        assert "too large" in result["error"]
        # 目标文件绝不能落盘
        assert not (docs_dir / "kb1" / "general" / "big.md").exists()

    def test_oversize_cleans_tmp_file(self, upload_env):
        """超限拒绝后,临时文件必须清理(防磁盘泄漏)。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("big.md", b"B" * 500, "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f, max_size=100, chunk_size=64)

        assert result["ok"] is False
        leftovers = files_in(tmp_dir)
        assert leftovers == [], (
            f"BUG: 超限拒绝后临时文件未清理,残留 {leftovers} "
            f"(sync_upload_impl 的 'too large' 分支缺 os.unlink)"
        )

    def test_undersize_passes(self, upload_env):
        """恰好 max_size 大小的文件应通过(边界:total == max_size 不算超)。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("edge.md", b"C" * 100, "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f, max_size=100, chunk_size=32)
        assert result["ok"] is True
        assert result["size"] == 100


# ============ 3. 空文件拒绝 ============

class TestEmptyFile:

    def test_empty_file_rejected(self, upload_env):
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("empty.md", b"", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)

        assert result["ok"] is False
        assert "empty" in result["error"]
        assert not (docs_dir / "kb1" / "general" / "empty.md").exists()

    def test_empty_file_cleans_tmp(self, upload_env):
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("empty.md", b"", "text/markdown")
        run_impl(docs_dir, tmp_dir, f)
        assert files_in(tmp_dir) == []


# ============ 4. 魔数校验 ============

class TestMagicNumber:
    """二进制格式(PDF/DOCX)必须校验文件头,防止伪装扩展名。"""

    def test_fake_pdf_rejected(self, upload_env):
        """内容不是 %PDF- 开头 → 拒。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("fake.pdf", b"this is not a pdf at all", "application/pdf")
        result = run_impl(docs_dir, tmp_dir, f)

        assert result["ok"] is False
        assert "magic" in result["error"] or "corrupted" in result["error"]
        assert files_in(tmp_dir) == [], "魔数拒绝后临时文件必须清理"

    def test_fake_docx_rejected(self, upload_env):
        """内容不是 PK\\x03\\x04(zip)开头 → 拒。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("fake.docx", b"plain text pretending to be docx",
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is False
        assert files_in(tmp_dir) == []

    def test_real_pdf_magic_accepted(self, upload_env):
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("real.pdf", b"%PDF-1.4\n%fake body", "application/pdf")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is True
        assert Path(result["filepath"]).read_bytes() == b"%PDF-1.4\n%fake body"

    def test_real_docx_magic_accepted(self, upload_env):
        docs_dir, tmp_dir = upload_env
        body = b"PK\x03\x04\x14\x00fake zip body"
        f = FakeUploadFile("real.docx", body,
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is True

    def test_md_binary_content_rejected(self, upload_env):
        """P2 改进:.md/.txt 含 NUL 字节 → 二进制伪装,拒绝(旧行为是放行,已翻转)。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("bin.md", b"\x00\x01\x02\xff\xfe", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is False, "含 NUL 字节的文本文件应被拒绝"
        assert files_in(tmp_dir) == [], "拒绝后临时文件必须清理"
        assert not (docs_dir / "kb1" / "general" / "bin.md").exists()

    def test_md_normal_utf8_content_passes(self, upload_env):
        """正常 UTF-8 中文内容不受二进制探测影响。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("ok.md", "# 标题\n中文内容".encode("utf-8"), "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is True


# ============ 5. 中文文件名编码修复 ============

class TestChineseFilename:

    def test_latin1_mojibake_decoded_to_utf8(self, upload_env):
        """'报告.md' 的 utf-8 字节被 latin-1 误解码 → 必须回编。"""
        docs_dir, tmp_dir = upload_env
        mojibake = "报告.md".encode("utf-8").decode("latin-1")
        assert mojibake != "报告.md"  # 前提:确实是乱码形态

        f = FakeUploadFile(mojibake, b"# content", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)

        assert result["ok"] is True
        assert result["filename"] == "报告.md", (
            f"中文文件名未回编为 utf-8,实际: {result['filename']!r}"
        )
        assert Path(result["filepath"]).name == "报告.md"

    def test_plain_ascii_filename_untouched(self, upload_env):
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("normal.md", b"x", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["filename"] == "normal.md"


# ============ 6. 成功流程与返回结构 ============

class TestSuccessFlow:

    def test_success_returns_full_structure(self, upload_env):
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("hello.md", b"# hello world", "text/markdown")
        req = FakeRequest(batch_id="batch-42")
        result = run_impl(docs_dir, tmp_dir, f, request=req)

        assert result["ok"] is True
        for key in ("upload_id", "filepath", "filename", "size", "source",
                    "batch_id", "upload_elapsed_ms", "was_overwrite"):
            assert key in result, f"返回结构缺字段: {key}"
        assert result["filename"] == "hello.md"
        assert result["size"] == len(b"# hello world")
        assert result["batch_id"] == "batch-42"
        assert result["was_overwrite"] is False
        assert "127.0.0.1" in result["source"]

    def test_atomic_rename_leaves_no_tmp(self, upload_env):
        """成功后 tmp_dir 必须为空(atomic rename 语义)。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("clean.md", b"content", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is True
        assert files_in(tmp_dir) == [], f"临时文件残留: {files_in(tmp_dir)}"

    def test_overwrite_detection(self, upload_env):
        """同名文件已存在 → was_overwrite=True 且内容被覆盖。"""
        docs_dir, tmp_dir = upload_env
        target_dir = docs_dir / "kb1" / "general"
        target_dir.mkdir(parents=True)
        (target_dir / "dup.md").write_text("old content", encoding="utf-8")

        f = FakeUploadFile("dup.md", b"new content", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)

        assert result["ok"] is True
        assert result["was_overwrite"] is True, "已存在同名文件必须标记 was_overwrite"
        assert (target_dir / "dup.md").read_bytes() == b"new content"

    def test_overwrite_keeps_old_version_as_bak(self, upload_env):
        """P2 改进:覆盖前必须备份旧版本到 .bak(索引失败时旧内容可恢复)。"""
        docs_dir, tmp_dir = upload_env
        target_dir = docs_dir / "kb1" / "general"
        target_dir.mkdir(parents=True)
        (target_dir / "bak.md").write_text("precious old content", encoding="utf-8")

        f = FakeUploadFile("bak.md", b"new content", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)

        assert result["ok"] is True
        bak = target_dir / "bak.md.bak"
        assert bak.exists(), "覆盖场景必须生成 .bak 备份"
        assert bak.read_bytes() == b"precious old content"

    def test_new_upload_creates_no_bak(self, upload_env):
        """首次上传(非覆盖)不应产生 .bak。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("fresh.md", b"x", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is True
        assert not (docs_dir / "kb1" / "general" / "fresh.md.bak").exists()

    def test_progress_queue_created(self, upload_env):
        """上传后 _progress_queues 应有对应 upload_id 的队列(供 SSE 订阅)。"""
        docs_dir, tmp_dir = upload_env
        f = FakeUploadFile("sse.md", b"x", "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f)
        assert result["ok"] is True
        assert result["upload_id"] in rag_upload._progress_queues

    def test_multichunk_streaming_integrity(self, upload_env):
        """多块流式写入后内容必须完整(chunk_size=16,数据 100 字节)。"""
        docs_dir, tmp_dir = upload_env
        data = b"X" * 100
        f = FakeUploadFile("chunks.md", data, "text/markdown")
        result = run_impl(docs_dir, tmp_dir, f, chunk_size=16)
        assert result["ok"] is True
        assert Path(result["filepath"]).read_bytes() == data


# ============ 8. 流式哈希 sha256_of_file(F4 修复) ============

class TestSha256OfFile:
    """分块流式哈希 — 结果必须与整块读取一致,且支持跨块边界。"""

    def test_matches_stdlib_whole_file_hash(self, tmp_path):
        import hashlib
        data = b"duplicate-detection payload" * 100
        p = tmp_path / "h.bin"
        p.write_bytes(data)
        assert sha256_of_file(str(p)) == hashlib.sha256(data).hexdigest()

    def test_chunked_read_crosses_block_boundary(self, tmp_path):
        """chunk_size=7(非 2 的幂)下结果仍正确 → 分块拼接无误。"""
        import hashlib
        data = bytes(range(256)) * 10  # 2560 字节
        p = tmp_path / "cross.bin"
        p.write_bytes(data)
        assert sha256_of_file(str(p), chunk_size=7) == hashlib.sha256(data).hexdigest()

    def test_empty_file_hash(self, tmp_path):
        import hashlib
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        assert sha256_of_file(str(p)) == hashlib.sha256(b"").hexdigest()

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            sha256_of_file(str(tmp_path / "nope.bin"))


# ============ 9. 启动清理 stale 锁/孤儿 tmp（P1 改进）============

class TestCleanupStaleArtifacts:
    """崩溃残留的 .lock 与孤儿 tmp 文件必须能被启动清理回收。"""

    def test_stale_lock_and_tmp_removed_fresh_kept(self, tmp_path):
        import os as _os
        from backend.app.api.routes.rag_upload import cleanup_stale_upload_artifacts

        docs = tmp_path / "docs" / "kb1" / "general"
        docs.mkdir(parents=True)
        tmpd = tmp_path / "upload_tmp"
        tmpd.mkdir()

        stale_lock = docs / "a.md.lock"
        stale_lock.write_text("1\n1000000.0\n")
        fresh_lock = docs / "b.md.lock"
        fresh_lock.write_text("1\n99999999999.0\n")
        (docs / "a.md").write_text("content")  # 正文文件绝不能被误删

        stale_tmp = tmpd / "old123.md"
        stale_tmp.write_text("orphan")
        fresh_tmp = tmpd / "new456.md"
        fresh_tmp.write_text("in-flight")

        ancient = time.time() - 7200
        _os.utime(stale_lock, (ancient, ancient))
        _os.utime(stale_tmp, (ancient, ancient))

        counts = cleanup_stale_upload_artifacts(
            str(tmp_path / "docs"), str(tmpd), max_age_seconds=3600)

        assert not stale_lock.exists(), "超龄 .lock 必须被清理"
        assert fresh_lock.exists(), "新鲜 .lock 不能误删"
        assert (docs / "a.md").exists(), "正文文件绝不能被误删"
        assert not stale_tmp.exists(), "孤儿 tmp 必须被清理"
        assert fresh_tmp.exists(), "进行中的 tmp 不能误删"
        assert counts["locks_removed"] == 1
        assert counts["tmp_removed"] == 1


# ============ 6.5 进度队列 TTL 清理（F11） ============

class TestProgressQueueGC:
    """过期 SSE 进度队列必须被定期清理（F11），新鲜队列绝不误删。"""

    def test_cleanup_removes_expired_keeps_fresh(self, monkeypatch):
        fake_queues: dict = {}

        def _mk(age: float):
            q = asyncio.Queue()
            q._created_at = time.time() - age
            return q

        fake_queues["expired_uid"] = _mk(rag_upload.PROGRESS_QUEUE_TTL_SECONDS + 60)
        fake_queues["fresh_uid"] = _mk(10)
        monkeypatch.setattr(rag_upload, "_progress_queues", fake_queues)

        removed = rag_upload.cleanup_expired_progress_queues()
        assert removed == 1
        assert "expired_uid" not in fake_queues, "超 TTL 队列必须被清理"
        assert "fresh_uid" in fake_queues, "新鲜队列不能误删"

    def test_cleanup_without_created_at_treated_as_expired(self, monkeypatch):
        """无 _created_at 的队列（异常残留）按过期处理。"""
        fake_queues = {"orphan_uid": asyncio.Queue()}  # getattr 默认 0 → 必过期
        monkeypatch.setattr(rag_upload, "_progress_queues", fake_queues)
        assert rag_upload.cleanup_expired_progress_queues() == 1
        assert not fake_queues

    def test_cleanup_empty_registry_is_noop(self, monkeypatch):
        monkeypatch.setattr(rag_upload, "_progress_queues", {})
        assert rag_upload.cleanup_expired_progress_queues() == 0

    def test_gc_loop_tolerates_exception_and_keeps_running(self, monkeypatch):
        """单轮 GC 异常只告警，绝不能终止循环（挂掉比队列滞留更糟）。"""
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return 0

        monkeypatch.setattr(rag_upload, "cleanup_expired_progress_queues", flaky)

        async def run():
            task = asyncio.create_task(
                rag_upload.progress_queue_gc_loop(interval_seconds=0.01))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        assert calls["n"] >= 2, "首轮异常后循环必须继续执行"


# ============ 7. HTTP 端点级(FastAPI TestClient) ============

@pytest.fixture
def client(monkeypatch):
    """端点级测试:patch 掉 RAG 依赖与后台索引,只测 HTTP 层逻辑。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api.routes import rag_upload as ru
    import backend.config.knowledge_base as kb_cfg
    import backend.config.rag as rag_cfg

    monkeypatch.setattr(ru, "require_rag_ready", lambda: None)
    monkeypatch.setattr(kb_cfg, "validate_kb_dept", lambda kb, dept: True)
    monkeypatch.setattr(rag_cfg, "RAG_MAX_FILE_SIZE", 50)

    captured = {}

    async def fake_sync_impl(file, request, max_size, tmp_dir, chunk_size,
                             emit_bytes, emit_ms, kb_id="policy_general",
                             department="general"):
        captured["called"] = True
        captured["filename"] = file.filename
        return {
            "ok": True, "upload_id": "uid123456789", "filepath": "/tmp/x.md",
            "filename": file.filename, "size": 1, "source": "test",
            "batch_id": None, "upload_elapsed_ms": 1, "was_overwrite": False,
        }

    async def fake_bg_index(*args, **kwargs):
        captured["bg_called"] = True

    monkeypatch.setattr(ru, "sync_upload_impl", fake_sync_impl)
    monkeypatch.setattr(ru, "_run_index_background", fake_bg_index)

    app = FastAPI()
    app.include_router(ru.router)
    return TestClient(app), captured


class TestUploadEndpoint:

    def test_success_returns_upload_id(self, client):
        tc, captured = client
        resp = tc.post("/upload",
                       files={"file": ("ok.md", b"# hi", "text/markdown")},
                       data={"kb_id": "policy_general", "department": "general"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["upload_id"] == "uid123456789"
        assert captured.get("called") is True

    def test_invalid_kb_dept_rejected_before_upload(self, client, monkeypatch):
        tc, captured = client
        import backend.config.knowledge_base as kb_cfg
        monkeypatch.setattr(kb_cfg, "validate_kb_dept", lambda kb, dept: False)
        resp = tc.post("/upload",
                       files={"file": ("ok.md", b"# hi", "text/markdown")},
                       data={"kb_id": "bad_kb", "department": "nope"})
        body = resp.json()
        assert body["ok"] is False
        assert "知识库" in body["error"]
        assert "called" not in captured, "KB 校验失败时不应进入上传实现"

    def test_invalid_mime_rejected_before_upload(self, client):
        tc, captured = client
        resp = tc.post("/upload",
                       files={"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")},
                       data={"kb_id": "policy_general", "department": "general"})
        body = resp.json()
        assert body["ok"] is False
        assert "called" not in captured, "MIME 校验失败时不应进入上传实现"

    def test_explicit_octet_stream_rejected(self, client):
        tc, _ = client
        resp = tc.post("/upload",
                       files={"file": ("a.md", b"x", "application/octet-stream")},
                       data={"kb_id": "policy_general", "department": "general"})
        assert resp.json()["ok"] is False

    def test_content_length_oversize_precheck(self, client, monkeypatch):
        """Content-Length 超上限 → 端点预检直接拒(不进入流式)。"""
        tc, captured = client
        import backend.config.rag as rag_cfg
        monkeypatch.setattr(rag_cfg, "RAG_MAX_FILE_SIZE", 0)  # max_size=0 → 任何长度都超
        # F10: 预检带 multipart 余量；本测试要触发预检直拒，需把余量归零
        monkeypatch.setattr(rag_upload, "_MULTIPART_OVERHEAD", 0)
        resp = tc.post("/upload",
                       files={"file": ("big.md", b"data", "text/markdown")},
                       data={"kb_id": "policy_general", "department": "general"})
        body = resp.json()
        assert body["ok"] is False
        assert "too large" in body["error"]
        assert "called" not in captured

    def test_content_length_margin_allows_boundary_file(self, client, monkeypatch):
        """F10: 预检余量放行贴上限的小请求，不误拒 multipart 封装开销。

        RAG_MAX_FILE_SIZE=0 但实际负载很小（cl < 16KB 余量）→ 预检放行，
        进入流式实现（精确上限由流式字节计数强制，此处 fake 实现直接成功）。
        """
        tc, captured = client
        import backend.config.rag as rag_cfg
        monkeypatch.setattr(rag_cfg, "RAG_MAX_FILE_SIZE", 0)
        # 保持默认 _MULTIPART_OVERHEAD（16KB），小文件 multipart cl 远低于余量
        resp = tc.post("/upload",
                       files={"file": ("ok.md", b"# hi", "text/markdown")},
                       data={"kb_id": "policy_general", "department": "general"})
        assert resp.json()["ok"] is True
        assert captured.get("called") is True, "预检余量内应进入流式实现"

    def test_rag_not_ready_returns_503(self, client, monkeypatch):
        tc, _ = client
        from fastapi import HTTPException
        import backend.app.api.routes.rag_upload as ru

        def not_ready():
            raise HTTPException(status_code=503, detail={"code": "SERVICE_NOT_READY"})

        monkeypatch.setattr(ru, "require_rag_ready", not_ready)
        resp = tc.post("/upload",
                       files={"file": ("ok.md", b"# hi", "text/markdown")},
                       data={"kb_id": "policy_general", "department": "general"})
        assert resp.status_code == 503


class TestUploadStreamEndpoint:

    def test_unknown_upload_id_returns_sse_error(self, client):
        tc, _ = client
        resp = tc.get("/upload/nonexistent/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        # _sse_encode 故意 ensure_ascii=True（防客户端 latin-1 乱码），
        # 解析 SSE data 行的 JSON 后断言，避免断言转义形态
        import json
        data_lines = [l for l in resp.text.splitlines() if l.startswith("data: ")]
        assert data_lines, f"SSE 流应有 data 行，实际: {resp.text!r}"
        payload = json.loads(data_lines[0][len("data: "):])
        assert "nonexistent" in payload["message"]
        assert "不存在或已过期" in payload["message"]

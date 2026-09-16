"""上传链路可靠性加固四项的回归测试。

1. 扫描件预检：_pdf_has_text_layer 三态（有文本/无文本/无法判定）
2. 锁心跳：持锁期间刷新时间戳；锁易主后停止、不复活已接管锁
3. 索引并发闸门：信号量限制同时执行的索引任务数
4. 一致性 Sweeper：孤儿判定排除"索引进行中"doc（防误删中间态向量），
   真孤儿/幽灵被检出并可修复
"""
import asyncio
import os
import threading
import time

import pytest
from types import SimpleNamespace

import backend.app.api.routes.rag_upload as ru
from backend.app.api.routes.rag_upload import (
    _pdf_has_text_layer,
    _start_lock_heartbeat,
)


@pytest.fixture(autouse=True)
def _sim_broker_down(monkeypatch):
    """Celery 队列化已固定为主路径：本文件测并发闸门等本机回退链路，
    模拟 broker 不可达触发回退（否则任务会投进真实 broker，闸门测不到）。
    """
    def _raise(*a, **kw):
        raise ConnectionError("simulated broker down (test fixture)")
    monkeypatch.setattr(ru, "_dispatch_index_to_celery", _raise)


# ============ 1. PDF 扫描件预检 ============

class TestPdfTextLayerPrecheck:

    @pytest.fixture
    def pdfs(self, tmp_path):
        import fitz
        text_pdf = tmp_path / "text.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "售后退货制度：用户提交申请后 48 小时内处理。")
        doc.save(str(text_pdf))
        doc.close()

        blank_pdf = tmp_path / "blank.pdf"  # 无文本层（模拟扫描件）
        doc = fitz.open()
        doc.new_page()
        doc.new_page()
        doc.save(str(blank_pdf))
        doc.close()
        return text_pdf, blank_pdf

    def test_text_pdf_has_layer(self, pdfs):
        assert _pdf_has_text_layer(str(pdfs[0])) is True

    def test_blank_pdf_has_no_layer(self, pdfs):
        assert _pdf_has_text_layer(str(pdfs[1])) is False

    def test_corrupt_file_returns_none(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        assert _pdf_has_text_layer(str(bad)) is None

    def test_max_pages_limits_scan(self, tmp_path):
        """文本出现在第 N+1 页、只查前 N 页 → 判为无文本层。"""
        import fitz
        p = tmp_path / "late.pdf"
        doc = fitz.open()
        for _ in range(3):
            doc.new_page()
        page = doc.new_page()  # 第 4 页才有文本
        page.insert_text((72, 72), "迟到出现的文本内容，足够长以通过阈值判定。")
        doc.save(str(p))
        doc.close()
        assert _pdf_has_text_layer(str(p), max_pages=3) is False
        assert _pdf_has_text_layer(str(p), max_pages=10) is True


# ============ 2. 锁心跳 ============

class TestLockHeartbeat:

    def _write_lock(self, path, pid):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{pid}\n{time.time()}\n")

    def _read_ts(self, path):
        with open(path, "r", encoding="utf-8") as f:
            f.readline()
            return float(f.readline().strip())

    def test_heartbeat_refreshes_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ru, "LOCK_HEARTBEAT_SECONDS", 0.05)
        lock = str(tmp_path / "a.md.lock")
        self._write_lock(lock, str(os.getpid()))  # 心跳校验本进程 pid
        ts0 = self._read_ts(lock)

        stop = threading.Event()
        _start_lock_heartbeat(lock, stop)
        time.sleep(0.25)
        stop.set()
        assert self._read_ts(lock) > ts0, "心跳必须持续刷新时间戳"

    def test_heartbeat_exits_on_pid_mismatch(self, tmp_path, monkeypatch):
        """锁被他人接管（pid 变化）→ 心跳退出且不覆盖接管者的内容。"""
        monkeypatch.setattr(ru, "LOCK_HEARTBEAT_SECONDS", 0.05)
        lock = str(tmp_path / "b.md.lock")
        self._write_lock(lock, "99999")  # 非本进程 pid

        stop = threading.Event()
        _start_lock_heartbeat(lock, stop)
        time.sleep(0.25)
        stop.set()
        with open(lock, encoding="utf-8") as f:
            assert f.readline().strip() == "99999", "不得覆盖已易主的锁"

    def test_heartbeat_exits_when_lock_gone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ru, "LOCK_HEARTBEAT_SECONDS", 0.05)
        import os
        lock = str(tmp_path / "c.md.lock")
        self._write_lock(lock, str(os.getpid()))
        stop = threading.Event()
        _start_lock_heartbeat(lock, stop)
        os.unlink(lock)  # 正常释放
        time.sleep(0.25)  # 心跳应静默退出而非重建锁文件
        stop.set()
        assert not os.path.exists(lock), "心跳不得复活已释放的锁文件"


# ============ 3. 索引并发闸门 ============

class TestIndexConcurrencyGate:

    @pytest.fixture(autouse=True)
    def _reset_semaphore(self):
        ru._index_semaphore = None
        yield
        ru._index_semaphore = None

    def test_gate_limits_concurrent_index_tasks(self, monkeypatch):
        import os as _os
        monkeypatch.setattr(ru, "_INDEX_CONCURRENCY_LIMIT", 1)
        queues = {}
        for i in range(3):
            q = asyncio.Queue()
            q._created_at = time.time()
            queues[f"u{i}"] = q
        monkeypatch.setattr(ru, "_progress_queues", queues)
        registry_stub = SimpleNamespace(
            get_by_path=lambda p: None,
            update_status=lambda p, s: None,
        )
        monkeypatch.setattr(ru, "_get_registry", lambda: registry_stub)
        monkeypatch.setattr(ru, "_safe_log_op", lambda *a, **k: None)

        track = {"cur": 0, "max": 0, "lock": threading.Lock()}

        def fake_index_sync(*args, **kwargs):
            with track["lock"]:
                track["cur"] += 1
                track["max"] = max(track["max"], track["cur"])
            time.sleep(0.1)
            with track["lock"]:
                track["cur"] -= 1
            raise RuntimeError("boom")  # 走 error 分支，绕开成功路径的依赖

        monkeypatch.setattr(ru, "_do_index_sync", fake_index_sync)

        async def run():
            tasks = [
                asyncio.create_task(ru._run_index_background(
                    f"u{i}", f"/tmp/doc{i}.md", f"doc{i}.md"))
                for i in range(3)
            ]
            await asyncio.gather(*tasks)

        asyncio.run(run())
        assert track["max"] == 1, f"并发闸门未生效，最大并发={track['max']}"

    def test_semaphore_reused_across_calls(self):
        s1 = ru._get_index_semaphore()
        s2 = ru._get_index_semaphore()
        assert s1 is s2
        ru._index_semaphore = None


# ============ 4. 一致性 Sweeper ============

class FakeVectorStore:
    def __init__(self, doc_ids):
        self._ids = list(doc_ids)
        self.deleted_where = []

    def get(self, where=None):
        metas = [{"doc_id": d} for d in self._ids]
        return {"ids": [f"id-{i}" for i in range(len(metas))], "metadatas": metas}

    def delete(self, where=None, ids=None, **kwargs):
        self.deleted_where.append(where)


class FakeBM25:
    def __init__(self, doc_ids):
        from langchain_core.documents import Document
        self.docs = [Document(page_content="x", metadata={"doc_id": d})
                     for d in doc_ids]
        self.removed = []

    def load_docs(self):
        return self.docs

    def remove_documents(self, doc_ids, k=20, file_paths=None):
        self.removed.extend(doc_ids)


@pytest.fixture
def checker_env(tmp_path, monkeypatch):
    """真实 registry(tmp) + fake stores 的 IndexConsistencyChecker。"""
    import backend.config as config_pkg
    from backend.rag.indexing.consistency import IndexConsistencyChecker
    from backend.rag.indexing.doc_registry import DocumentRegistry

    reg_db = str(tmp_path / "reg.db")
    monkeypatch.setattr(config_pkg, "DOC_REGISTRY_PATH", reg_db)
    registry = DocumentRegistry(reg_db)

    # active 文档（真实文件，避免 registry-disk 检查噪音）
    active_file = tmp_path / "active.md"
    active_file.write_text("body", encoding="utf-8")
    registry.register(file_path=str(active_file), doc_id="doc-active",
                      file_hash="h", kb_id="kb", chunk_ids=["c1"],
                      doc_db_id="dd1")
    # 进行中文档（parsing 占位行，向量已写但 registry 还不是 active）
    inflight_file = tmp_path / "wip.md"
    inflight_file.write_text("body", encoding="utf-8")
    registry.register_in_progress(str(inflight_file), doc_id="doc-wip",
                                  file_hash="h", kb_id="kb")

    vectordb = FakeVectorStore(["doc-active", "doc-wip", "doc-orphan"])
    doc_db = FakeVectorStore(["doc-active"])
    bm25 = FakeBM25(["doc-active", "doc-ghost"])
    pipeline = SimpleNamespace(
        vectordb=vectordb, doc_db=doc_db, bm25_store=bm25,
        refresh_bm25_from_store=lambda: None,
    )
    checker = IndexConsistencyChecker(pipeline)
    return SimpleNamespace(
        checker=checker, registry=registry, vectordb=vectordb,
        doc_db=doc_db, bm25=bm25,
    )


class TestConsistencySweeper:

    def _issue_ids(self, report, store):
        return {i.doc_id for i in report.issues
                if i.store == store and i.severity == "error"}

    def test_in_progress_doc_not_flagged_as_orphan(self, checker_env):
        """先写后删/新上传的中间态向量绝不能被 Sweeper 当孤儿删除。"""
        report = checker_env.checker.check()
        chroma_orphans = self._issue_ids(report, "chroma_chunk")
        assert "doc-wip" not in chroma_orphans
        assert "doc-wip" not in self._issue_ids(report, "chroma_doc")
        assert "doc-wip" not in self._issue_ids(report, "chunk_store")

    def test_true_orphan_and_bm25_ghost_detected(self, checker_env):
        report = checker_env.checker.check()
        assert "doc-orphan" in self._issue_ids(report, "chroma_chunk")
        assert "doc-ghost" in self._issue_ids(report, "bm25")

    def test_repair_removes_orphans_only(self, checker_env):
        report = checker_env.checker.check()
        checker_env.checker.repair(report)
        # 孤儿按 doc_id 条件删，进行中/active 不在删除列表里
        chroma_deletes = [w.get("doc_id") for w in checker_env.vectordb.deleted_where]
        assert "doc-orphan" in chroma_deletes
        assert "doc-active" not in chroma_deletes
        assert "doc-wip" not in chroma_deletes
        assert checker_env.bm25.removed == ["doc-ghost"]

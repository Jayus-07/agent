"""上传 → 入库 全链路端到端测试。

背景:此前测试只覆盖了"上传落盘"(sync_upload_impl)和"文件→chunks"
(parse_and_chunk)两段;中间的入库链路(_run_index_background →
_do_index_sync → IncrementalIndexer.reindex_file → registry/向量库/doc_db/BM25)
没有端到端验证。

设计(真实+fake 混合):
  真实:DocumentRegistry(tmp SQLite)、chunk_store(tmp SQLite)、
       parse_and_chunk 解析流水线、IncrementalIndexer 全部编排逻辑、
       sync_upload_impl 上传落盘、_run_index_background 进度事件。
  Fake:embedding(确定性)、vectordb/doc_db/bm25_store(内存记录型),
       避免 Chroma/模型依赖,同时可断言写入内容与删除语义。

覆盖:
  1. indexer 层:md 文件 reindex_file → registry active + 各 store 写入正确
  2. indexer 层:重复 reindex 同内容 → 旧数据清理 + registry 不重复
  3. 路由层:上传 → 后台索引 → SSE 事件序(done 含 doc_id)+ registry 落库
  4. 路由层:同内容二次上传 → duplicate 短路,不再入库
  5. 路由层:内容变化后重新上传 → 重新索引,hash 更新
"""
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.config as config_pkg
import backend.config.database as config_database
from backend.app.api.routes import rag_upload
from backend.app.api.routes._rag_shared import _sse_encode  # noqa: F401  (确保模块可用)
from backend.app.api.routes.rag_upload import sync_upload_impl
from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.rag.indexing.indexer import IncrementalIndexer
from backend.rag.indexing import chunk_store as chunk_store_mod
from backend.rag.indexing.chunk_store import ChunkStore
from backend.tests.fixtures.pg_env import (  # noqa: F401
    pg_clean_tables, pg_iso_env,
)


@pytest.fixture(autouse=True)
def _pg_iso(pg_clean_tables):
    """真实 ChunkStore/DocumentRegistry 走 PG —— 必须用 pgtest_ 前缀表，
    否则独立运行该文件会把测试文档写进生产 chunk_store/doc_registry
    （2026-09-18 实测：UTC 时间戳造成污染延迟暴露）。"""
    yield


@pytest.fixture(autouse=True)
def _sim_broker_down(monkeypatch):
    """Celery 队列化已固定为主路径：本文件验证上传→索引本机回退链路，
    模拟 broker 不可达触发回退（Celery 主路径由 test_rag_upload_celery_mode.py 覆盖）。
    不模拟的话 apply_async 会把任务投进真实 broker，本地索引不执行。
    """
    def _raise(*a, **kw):
        raise ConnectionError("simulated broker down (test fixture)")
    monkeypatch.setattr(rag_upload, "_dispatch_index_to_celery", _raise)


# ============ Fake 组件(内存记录型) ============

class FakeEmbedding:
    """确定性 fake:embed_query 返回基于文本 hash 的稳定 id。"""
    model_name = "fake-embedding"

    def embed_query(self, text: str) -> str:
        return "vec-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class FakeVectorDB:
    """记录 add_documents / delete；id 由 page_content hash 决定，可追踪删除。

    删除支持两种口径：where 条件删（_remove_document 内部清理）与 ids 精确删
    （reindex "先写后删"按 registry 旧 chunk_ids 清理，不碰同 doc_id 新向量）。
    """
    _collection_name = "fake_chunks"

    def __init__(self):
        self.added: list = []   # [(docs_batch, ids_batch)]
        self.deleted_where: list[dict] = []
        self.deleted_ids: list[list[str]] = []
        self._n = 0

    def add_documents(self, docs, **kwargs):
        ids = []
        for d in docs:
            self._n += 1
            ids.append(f"chunk-{self._n}-{hashlib.sha256(d.page_content.encode('utf-8')).hexdigest()[:8]}")
        self.added.append((list(docs), ids))
        return ids

    def delete(self, where=None, ids=None, **kwargs):
        if ids is not None:
            self.deleted_ids.append(list(ids))
        if where is not None:
            self.deleted_where.append(where)


class FakeDocDB:
    def __init__(self):
        self.texts: list[str] = []
        self.metas: list[dict] = []
        self.deleted_where: list[dict] = []
        self.deleted_ids: list[list[str]] = []
        self._n = 0

    def add_texts(self, texts, metadatas=None, **kwargs):
        ids = []
        for i, t in enumerate(texts):
            self._n += 1
            self.texts.append(t)
            self.metas.append((metadatas or [{}] * len(texts))[i])
            ids.append(f"doc-{self._n}")
        return ids

    def delete(self, where=None, ids=None, **kwargs):
        if ids is not None:
            self.deleted_ids.append(list(ids))
        if where is not None:
            self.deleted_where.append(where)


class FakeBM25Store:
    """对齐生产接口：索引主链路调 replace_documents（单次重建完成旧删新增）。
    旧 fake 只实现 add_documents，与生产漂移导致 BM25 断言恒失败。"""

    def __init__(self):
        self.batches: list[list] = []
        self.replaced: list[dict] = []
        self.removed: list[list] = []

    def add_documents(self, docs, k=5, **kwargs):
        self.batches.append(list(docs))

    def replace_documents(self, docs, k=20, *, doc_id="", file_path=""):
        self.batches.append(list(docs))
        self.replaced.append({"doc_id": doc_id, "file_path": file_path,
                              "count": len(docs)})

    def remove_documents(self, doc_ids, k=20, file_paths=None):
        self.removed.append(list(doc_ids))
        return None


class FakeUploadFile:
    """最小 UploadFile 替身(与 test_rag_upload_sync_impl 一致)。"""

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
    def __init__(self):
        self.client = _FakeClient()
        self.headers = {"user-agent": "pytest-e2e"}


# ============ 测试基建 ============

MD_CONTENT = (
    "# 售后退货制度\n\n"
    "## 退货流程\n\n"
    "用户提交退货申请后,客服须在 48 小时内核查并跟进处理。\n"
    "退货商品需保持完好,附带原始包装与发票。\n\n"
    "## 差评处理\n\n"
    "差评须在 24 小时内响应,由售后专员回访确认问题并给出解决方案。\n"
)


@pytest.fixture
def index_env(tmp_path, monkeypatch):
    """indexer 层隔离环境:真实 registry/chunk_store(tmp),fake stores。

    返回 SimpleNamespace(docs_dir, registry, chunk_store, vectordb, doc_db,
    bm25, embedding, make_indexer)。
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    registry = DocumentRegistry(str(tmp_path / "doc_registry.db"))
    monkeypatch.setattr(chunk_store_mod, "_store",
                        ChunkStore(str(tmp_path / "chunk_store.db")))

    vectordb, doc_db, bm25, embedding = (
        FakeVectorDB(), FakeDocDB(), FakeBM25Store(), FakeEmbedding(),
    )

    # _build_doc_metadata 即时替身：真实实现走 LLM 摘要/分类链，每次 sync()
    # 打真实 API 拖 2~5s；本文件只验证上传→索引→注册表的链路语义。
    async def _stub_doc_metadata(full_text, base_meta, parent_span_id="", chunks_text=None):
        return dict(base_meta)

    monkeypatch.setattr(IncrementalIndexer, "_build_doc_metadata", _stub_doc_metadata)

    def make_indexer(**overrides):
        kwargs = dict(
            docs_dir=str(docs_dir), vectordb=vectordb, doc_db=doc_db,
            embedding=embedding, registry=registry,
            kb_id="policy_general", department="general", bm25_store=bm25,
        )
        kwargs.update(overrides)
        return IncrementalIndexer(**kwargs)

    return SimpleNamespace(
        docs_dir=docs_dir, registry=registry, vectordb=vectordb,
        doc_db=doc_db, bm25=bm25, embedding=embedding, make_indexer=make_indexer,
    )


@pytest.fixture
def route_env(tmp_path, monkeypatch, index_env):
    """路由层隔离环境:index_env + patch 路由单例/配置,使上传→入库全链路可跑。

    patch 清单:
      - DOCS_DIRECTORY(config.database + backend.config 两处,_do_index_sync
        从 backend.config 导入,两处必须一致)
      - rag_upload.get_rag_pipeline → fake pipeline(index_env 的 stores)
      - rag_upload._get_registry → index_env 的真实 tmp registry
      - rag_upload._safe_log_op → 记录器(不写真实操作日志 DB)
      - rag_upload._progress_queues → 新 dict
    """
    docs_dir = tmp_path / "route_docs"
    docs_dir.mkdir()
    tmp_dir = tmp_path / "upload_tmp"
    tmp_dir.mkdir()

    # _do_index_sync 在函数内 from backend.config import DOCS_DIRECTORY（运行时读），
    # sync_upload_impl 在函数内 from backend.config.database import，
    # 所以 patch 这两处模块属性即可，无需 patch rag_upload（其无模块级绑定）
    monkeypatch.setattr(config_database, "DOCS_DIRECTORY", str(docs_dir))
    monkeypatch.setattr(config_pkg, "DOCS_DIRECTORY", str(docs_dir))

    fake_pipeline = SimpleNamespace(
        vectordb=index_env.vectordb, doc_db=index_env.doc_db,
        embedding=index_env.embedding, bm25_store=index_env.bm25,
    )
    monkeypatch.setattr(rag_upload, "get_rag_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr(rag_upload, "_get_registry", lambda: index_env.registry)

    op_logs: list[dict] = []

    def _record_op(doc_id, doc_name, operation, source, **kw):
        op_logs.append({"doc_id": doc_id, "doc_name": doc_name,
                        "operation": operation, **kw})

    monkeypatch.setattr(rag_upload, "_safe_log_op", _record_op)

    queues: dict = {}
    monkeypatch.setattr(rag_upload, "_progress_queues", queues)

    return SimpleNamespace(
        docs_dir=docs_dir, tmp_dir=tmp_dir, registry=index_env.registry,
        vectordb=index_env.vectordb, doc_db=index_env.doc_db,
        bm25=index_env.bm25, queues=queues, op_logs=op_logs,
    )


def _upload_and_index(route_env, filename: str, data: bytes) -> tuple[dict, list]:
    """跑完整链路:sync_upload_impl → _run_index_background,收集全部 SSE 事件。

    与生产 upload_document 端点的唯一差别:create_task 换成直接 await
    (确定性,无 task 竞态);其余代码路径完全相同。
    """

    async def flow():
        res = await sync_upload_impl(
            FakeUploadFile(filename, data), FakeRequest(),
            10 * 1024 * 1024, str(route_env.tmp_dir), 64,
            emit_bytes=1024 * 1024, emit_ms=10 ** 9,
            kb_id="policy_general", department="general",
        )
        assert res["ok"], f"上传失败: {res}"
        upload_id = res["upload_id"]
        # sync_upload_impl 已创建进度队列并挂入 _progress_queues，直接复用
        q = route_env.queues[upload_id]
        await asyncio.wait_for(
            rag_upload._run_index_background(
                upload_id, res["filepath"], res["filename"],
                source="pytest-e2e", kb_id="policy_general",
                department="general",
                upload_elapsed_ms=res.get("upload_elapsed_ms"),
                was_overwrite=bool(res.get("was_overwrite")),
            ),
            timeout=30,
        )
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return res, events

    return asyncio.run(flow())


# ============ 1. indexer 层:真实 registry + 全编排 ============

class TestIndexerIngestion:
    """reindex_file → registry/chunk_store/向量库/doc_db/BM25 全链路落库。"""

    def test_md_file_lands_in_registry_and_stores(self, index_env):
        fp = index_env.docs_dir / "退货制度.md"
        fp.write_text(MD_CONTENT, encoding="utf-8")
        indexer = index_env.make_indexer()

        result = indexer.reindex_file(str(fp))

        # 索引结果结构完整
        assert result["status"] == "active"
        assert result["doc_id"], "首次索引也必须返回真实 doc_id"
        assert result["chunk_count"] > 0
        assert result["file_hash"] == hashlib.sha256(
            fp.read_bytes()).hexdigest()
        assert result["doc_db_id"]

        # registry 落库:active + hash 一致
        row = index_env.registry.get_by_path(str(fp))
        assert row is not None
        assert row["status"] == "active"
        assert row["file_hash"] == result["file_hash"]
        assert row["chunk_count"] == result["chunk_count"]

        # 向量库:chunks 带 doc_id/kb_id metadata
        assert len(index_env.vectordb.added) == 1
        docs_batch, _ = index_env.vectordb.added[0]
        assert len(docs_batch) == result["chunk_count"]
        assert all(d.metadata.get("doc_id") for d in docs_batch)
        assert all(d.metadata.get("kb_id") == "policy_general"
                   for d in docs_batch)

        # doc_db:全文入库,metadata 含 doc_id
        assert len(index_env.doc_db.texts) == 1
        assert "48 小时" in index_env.doc_db.texts[0]
        assert index_env.doc_db.metas[0]["doc_id"]

        # BM25 同步(上传路径必传 bm25_store)
        assert len(index_env.bm25.batches) == 1
        assert len(index_env.bm25.batches[0]) == result["chunk_count"]

    def test_reindex_same_content_no_registry_dup_and_old_cleaned(self, index_env):
        """同内容重复 reindex:registry 不产生重复行,旧 chunk_store 数据先清。"""
        fp = index_env.docs_dir / "dup.md"
        fp.write_text(MD_CONTENT, encoding="utf-8")
        indexer = index_env.make_indexer()

        r1 = indexer.reindex_file(str(fp))
        r2 = indexer.reindex_file(str(fp))

        # 两次索引 doc_id 稳定(同一物理文件)
        assert r1["doc_id"] == r2["doc_id"]
        # registry 只有一行 active(路径唯一)
        row = index_env.registry.get_by_path(str(fp))
        assert row["status"] == "active"
        # 向量库写过两批；先写后删：第二次成功后按旧 chunk_ids 精确清理
        # （旧向量保留到新数据写完才删，期间无检索空窗）
        assert len(index_env.vectordb.added) == 2
        assert index_env.vectordb.deleted_ids, "重索引成功后应按旧 chunk_ids 清理旧向量"
        first_ids = index_env.vectordb.added[0][1]
        assert index_env.vectordb.deleted_ids[0] == first_ids
        # doc_db 同样按旧 doc_db_id 清理旧全文
        assert index_env.doc_db.deleted_ids == [["doc-1"]]

    def test_empty_content_file_raises_chunking_empty(self, index_env):
        """解析出 0 chunk 的文件绝不'假成功'入库(P1-4 契约)。"""
        from backend.rag.indexing.indexer import ChunkingEmptyError
        fp = index_env.docs_dir / "empty.md"
        fp.write_text("", encoding="utf-8")
        indexer = index_env.make_indexer()

        with pytest.raises(ChunkingEmptyError):
            indexer.reindex_file(str(fp))
        # registry 绝不能有该行
        assert index_env.registry.get_by_path(str(fp)) is None

    def test_reindex_file_accepts_precomputed_hash(self, index_env):
        """P2 改进:reindex_file 支持透传调用方已算好的 SHA256(避免全盘重读)。"""
        fp = index_env.docs_dir / "hashed.md"
        fp.write_text(MD_CONTENT, encoding="utf-8")
        pre = hashlib.sha256(fp.read_bytes()).hexdigest()
        indexer = index_env.make_indexer()

        result = indexer.reindex_file(str(fp), file_hash=pre)

        assert result["file_hash"] == pre
        row = index_env.registry.get_by_path(str(fp))
        assert row["file_hash"] == pre


# ============ 2. 路由层:上传 → 后台入库 → SSE 事件 ============

class TestUploadToIndexChain:
    """与生产同一代码路径:sync_upload_impl → _run_index_background。"""

    def test_upload_then_index_emits_done_and_registers(self, route_env):
        res, events = _upload_and_index(route_env, "售后制度.md",
                                        MD_CONTENT.encode("utf-8"))
        stages = [e.get("stage") for e in events if e is not None]

        # 事件序:uploading 开头 → done 收尾,无 error
        assert stages[0] == "uploading"
        assert stages[-1] == "done"
        assert "error" not in stages

        # done 事件携带入库后的文档信息(最后一个非 None 事件)
        done = next(e for e in reversed(events) if e is not None)
        assert done["doc"], "done 事件应携带 registry 文档信息"
        assert done["doc"]["status"] == "active"
        assert done["doc"]["chunk_count"] > 0
        assert done["total_ms"] >= 0

        # registry 真的落库(与 done.doc 一致)
        row = route_env.registry.get_by_path(res["filepath"])
        assert row and row["status"] == "active"
        assert row["file_hash"] == done["doc"]["file_hash"]

        # 各 store 都收到写入
        assert len(route_env.vectordb.added) == 1
        assert len(route_env.doc_db.texts) == 1
        assert len(route_env.bm25.batches) == 1

        # 操作日志记录 success 的 upload,且首次索引 doc_id 不为空
        # (旧实现首次索引返回空 doc_id,操作日志丢失文档身份)
        success_ops = [op for op in route_env.op_logs
                       if op["operation"] == "upload" and op["result"] == "success"]
        assert success_ops
        assert all(op["doc_id"] for op in success_ops)

        # 队列已 finalize:None 哨兵在尾部,SSE 流可正常结束
        assert events[-1] is None  # 哨兵收尾
        # f2b 后语义:_finalize_upload_queue 不主动 pop（保留终态事件给晚到的
        # SSE 订阅者）,回收靠 SSE 断连 pop + TTL GC —— 此处队列条目必须仍在
        assert res["upload_id"] in route_env.queues
        # 模拟 SSE 消费者断连回收（event_stream finally 行为）
        route_env.queues.pop(res["upload_id"], None)

    def test_same_content_second_upload_is_duplicate(self, route_env):
        """内容未变化的二次上传 → duplicate 短路,向量库不再写入。"""
        data = MD_CONTENT.encode("utf-8")
        _upload_and_index(route_env, "dup.md", data)
        adds_after_first = len(route_env.vectordb.added)

        res2, events2 = _upload_and_index(route_env, "dup.md", data)
        stages2 = [e.get("stage") for e in events2 if e is not None]

        assert "duplicate" in stages2, f"应命中 duplicate 短路: {stages2}"
        assert "done" not in stages2  # duplicate 是终态,不再走 done
        # 向量库/doc_db/BM25 均未新增写入
        assert len(route_env.vectordb.added) == adds_after_first
        assert len(route_env.doc_db.texts) == 1
        assert len(route_env.bm25.batches) == 1
        # 操作日志记录 duplicate
        assert any(op["result"] == "duplicate" for op in route_env.op_logs)
        # f2b 后语义:finalize 不主动 pop,duplicate 终态事件同样保留给晚到订阅者
        assert res2["upload_id"] in route_env.queues

    def test_changed_content_reindexes_and_updates_hash(self, route_env):
        """同名文件内容变化 → 重新索引,registry hash 更新为新内容。"""
        _upload_and_index(route_env, "evolve.md",
                          MD_CONTENT.encode("utf-8"))
        new_content = MD_CONTENT + "\n## 新增条款\n\n退换货时限延长至 7 天。\n"
        res2, events2 = _upload_and_index(route_env, "evolve.md",
                                          new_content.encode("utf-8"))
        stages2 = [e.get("stage") for e in events2 if e is not None]

        assert stages2[-1] == "done", f"变化内容应重新索引: {stages2}"
        assert "duplicate" not in stages2
        # registry hash = 新内容 hash
        row = route_env.registry.get_by_path(res2["filepath"])
        assert row["file_hash"] == hashlib.sha256(
            new_content.encode("utf-8")).hexdigest()
        # 向量库第二批写入,重索引成功后按旧 chunk_ids 清理旧向量(先写后删)
        assert len(route_env.vectordb.added) == 2
        assert route_env.vectordb.deleted_ids, "重索引成功后应按旧 chunk_ids 清理旧向量"

    def test_queue_vanished_still_indexes(self, route_env):
        """P1 修复:SSE 断连导致队列被 pop 后,索引必须继续,绝不静默跳过。

        旧行为:_do_index_sync 发现队列不在直接 return None,
        文件未被索引却被记为 success(chunk_count=0)。
        """
        res = asyncio.run(sync_upload_impl(
            FakeUploadFile("vanish.md", MD_CONTENT.encode("utf-8")),
            FakeRequest(), 10 * 1024 * 1024, str(route_env.tmp_dir), 64,
            emit_bytes=1024 * 1024, emit_ms=10 ** 9,
            kb_id="policy_general", department="general",
        ))
        assert res["ok"]
        # 模拟 SSE 断连:队列被 pop
        route_env.queues.pop(res["upload_id"], None)

        result = rag_upload._do_index_sync(
            res["upload_id"], res["filepath"], res["filename"],
            None, "policy_general", "general")

        assert result is not None, "队列消失不得静默跳过索引"
        assert result.get("chunk_count", 0) > 0
        row = route_env.registry.get_by_path(res["filepath"])
        assert row and row["status"] == "active", "文件必须真实落库"

    def test_overwrite_success_removes_bak(self, route_env):
        """P2 改进:覆盖上传成功后 .bak 应被清理(索引已确认新版本可用)。"""
        _upload_and_index(route_env, "bak.md", MD_CONTENT.encode("utf-8"))
        bak = Path(route_env.docs_dir) / "policy_general" / "general" / "bak.md.bak"

        new_content = MD_CONTENT + "\n## 修订\n\n时限调整。\n"
        res2, events2 = _upload_and_index(route_env, "bak.md",
                                          new_content.encode("utf-8"))

        stages2 = [e.get("stage") for e in events2 if e is not None]
        assert stages2[-1] == "done"
        assert not bak.exists(), "成功索引后 .bak 必须清理"

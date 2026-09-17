"""阶段4 — RAG 上传索引 Celery 队列化行为测试。

覆盖：
- _settle_index_result 终态收口（duplicate / error-chunking / error-清理 / done）
- execute_index_task_impl（成功 / 瞬态异常重试通知 / 业务终态不重试）
- _run_index_background 的 Celery 分流与 broker 失败回退
"""
import asyncio
from unittest.mock import MagicMock

import pytest

import backend.app.api.routes.rag_upload as ru
import backend.tasks.index_tasks as it


def _mk_queue_ctx(monkeypatch):
    """构造 _progress_queues + Redis 写捕获环境，返回 (upload_id, events, redis_writes)。"""
    upload_id = "u-test-1"
    q = asyncio.Queue()
    monkeypatch.setitem(ru._progress_queues, upload_id, q)
    # 路由标记集合按测试隔离（_run_index_background 入队成功会写入）
    monkeypatch.setattr(ru, "_celery_routed", set())
    events = []
    redis_writes = []

    def fake_redis_write(uid, stage, message="", **extra):
        redis_writes.append({"upload_id": uid, "stage": stage,
                             "message": message, **extra})

    monkeypatch.setattr(ru, "_write_progress_redis", fake_redis_write)
    return upload_id, q, events, redis_writes


# ═══════════════════════════════════════════════════
# _settle_index_result
# ═══════════════════════════════════════════════════

class TestSettleIndexResult:

    def test_duplicate_terminal(self, monkeypatch):
        upload_id, _q, _ev, _rw = _mk_queue_ctx(monkeypatch)
        emitted = []
        bak_removed = []
        monkeypatch.setattr(ru, "_remove_bak", lambda p: bak_removed.append(p))
        op_log = MagicMock()
        monkeypatch.setattr(ru, "_safe_log_op", op_log)

        ru._settle_index_result(
            upload_id, "/docs/f.docx", "f.docx", "web", None, "kb1",
            120, False, 1000.0,
            result={"terminal": "duplicate", "doc": {"doc_id": "d1", "chunk_count": 3}},
            emit_fn=lambda s, m="", **ex: emitted.append((s, m, ex)))

        assert emitted[0][0] == "duplicate"
        assert emitted[0][2]["doc"]["doc_id"] == "d1"
        assert bak_removed == ["/docs/f.docx"]
        op_log.assert_called_once()
        assert op_log.call_args.kwargs["result"] == "duplicate"

    def test_error_chunking_empty_keeps_file(self, monkeypatch):
        from backend.rag.indexing.indexer import ChunkingEmptyError
        upload_id, _q, _ev, _rw = _mk_queue_ctx(monkeypatch)
        emitted = []
        cleaned = []
        monkeypatch.setattr(ru, "_cleanup_failed_upload_sync",
                            lambda p, was_overwrite=False: cleaned.append(p))
        monkeypatch.setattr(ru, "_safe_log_op", MagicMock())

        ru._settle_index_result(
            upload_id, "/docs/scan.pdf", "scan.pdf", "web", None, "kb1",
            None, False, 1000.0, result=None,
            emit_fn=lambda s, m="", **ex: emitted.append((s, m, ex)),
            exc=ChunkingEmptyError("0 chunks"))

        stage, msg, extra = emitted[0]
        assert stage == "error"
        assert extra.get("recoverable") is True
        assert cleaned == [], "chunking_empty 必须保留源文件"

    def test_error_file_locked_keeps_file(self, monkeypatch):
        """P0-2:锁冲突 = 同文件另一请求正在索引,源文件绝不能删（持锁方可能正在读）。"""
        upload_id, _q, _ev, _rw = _mk_queue_ctx(monkeypatch)
        emitted = []
        cleaned = []
        monkeypatch.setattr(ru, "_cleanup_failed_upload_sync",
                            lambda p, was_overwrite=False: cleaned.append(p))
        monkeypatch.setattr(ru, "_safe_log_op", MagicMock())

        ru._settle_index_result(
            upload_id, "/docs/same.pdf", "same.pdf", "web", None, "kb1",
            None, False, 1000.0, result=None,
            emit_fn=lambda s, m="", **ex: emitted.append((s, m, ex)),
            exc=ru.FileLockedByOtherError("locked by another request"))

        stage, _msg, extra = emitted[0]
        assert stage == "error"
        assert extra.get("error_type") == "file_locked"
        assert extra.get("recoverable") is True
        assert cleaned == [], "锁冲突必须保留源文件"

    def test_error_generic_cleans_file(self, monkeypatch):
        upload_id, _q, _ev, _rw = _mk_queue_ctx(monkeypatch)
        emitted = []
        cleaned = []
        monkeypatch.setattr(ru, "_cleanup_failed_upload_sync",
                            lambda p, was_overwrite=False: cleaned.append(p))
        monkeypatch.setattr(ru, "_mark_registry_failed", lambda p: None)
        monkeypatch.setattr(ru, "_safe_log_op", MagicMock())

        ru._settle_index_result(
            upload_id, "/docs/x.pdf", "x.pdf", "web", None, "kb1",
            None, False, 1000.0, result=None,
            emit_fn=lambda s, m="", **ex: emitted.append((s, m, ex)),
            exc=RuntimeError("boom"))

        assert emitted[0][0] == "error"
        assert cleaned == ["/docs/x.pdf"]

    def test_done_terminal_invalidates_cache(self, monkeypatch):
        upload_id, _q, _ev, _rw = _mk_queue_ctx(monkeypatch)
        emitted = []
        reg = MagicMock()
        reg.get_by_path.return_value = {"doc_id": "d9", "doc_type": "policy"}
        monkeypatch.setattr(ru, "_get_registry", lambda: reg)
        monkeypatch.setattr(ru, "_remove_bak", lambda p: None)
        op_log = MagicMock()
        monkeypatch.setattr(ru, "_safe_log_op", op_log)
        cache = MagicMock()
        monkeypatch.setattr(
            "backend.rag.answer_cache.get_answer_cache", lambda: cache)

        ru._settle_index_result(
            upload_id, "/docs/y.pdf", "y.pdf", "web", None, "kb1",
            100, False, 1000.0,
            result={"terminal": "done", "trace_id": "t1", "chunk_count": 5,
                    "stage_elapsed": {"index_parse": 11}},
            emit_fn=lambda s, m="", **ex: emitted.append((s, m, ex)))

        stage, msg, extra = emitted[0]
        assert stage == "done"
        assert extra["trace_id"] == "t1"
        assert extra["stage_elapsed"]["parsing"] == 11   # span 键已映射为前端键
        assert extra["stage_elapsed"]["uploading"] == 100
        cache.invalidate_kb.assert_called_once_with("kb1")
        assert op_log.call_args.kwargs["result"] == "success"


# ═══════════════════════════════════════════════════
# execute_index_task_impl
# ═══════════════════════════════════════════════════

class TestExecuteIndexTaskImpl:

    def test_success_returns_terminal(self, monkeypatch):
        redis_writes = []
        monkeypatch.setattr(ru, "_write_progress_redis",
                            lambda uid, stage, message="", **ex:
                            redis_writes.append({"stage": stage, **ex}))
        monkeypatch.setattr(ru, "_do_index_sync",
                            lambda *a, **kw: {"terminal": "done", "trace_id": "t7",
                                              "doc": {"doc_id": "d7"}})
        settle = MagicMock()
        monkeypatch.setattr(ru, "_settle_index_result", settle)

        out = it.execute_index_task_impl("u1", "/docs/a.pdf", "a.pdf")

        assert out["status"] == "done"
        assert out["trace_id"] == "t7"
        assert out["doc_id"] == "d7"
        settle.assert_called_once()
        # main_loop 必须是 None（Worker 进程无事件循环）
        assert settle.call_args.args[0] == "u1"

    def test_transient_error_notifies_retry_and_reraises(self, monkeypatch):
        redis_writes = []
        monkeypatch.setattr(ru, "_write_progress_redis",
                            lambda uid, stage, message="", **ex:
                            redis_writes.append({"stage": stage, "message": message}))
        monkeypatch.setattr(ru, "_do_index_sync",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("embed down")))
        monkeypatch.setattr(ru, "_settle_index_result", MagicMock())

        with pytest.raises(RuntimeError):
            it.execute_index_task_impl("u1", "/docs/a.pdf", "a.pdf", retries=0)

        stages = [w["stage"] for w in redis_writes]
        assert "error" not in stages, "重试期间不得发终态 error 事件"
        assert any("自动重试" in w["message"] for w in redis_writes)

    def test_chunking_empty_settles_terminal(self, monkeypatch):
        from backend.rag.indexing.indexer import ChunkingEmptyError
        redis_writes = []
        monkeypatch.setattr(ru, "_write_progress_redis",
                            lambda uid, stage, message="", **ex:
                            redis_writes.append({"stage": stage}))
        monkeypatch.setattr(ru, "_do_index_sync",
                            lambda *a, **kw: (_ for _ in ()).throw(ChunkingEmptyError("0")))
        # 走真实 _settle_index_result，只 mock 外部副作用
        monkeypatch.setattr(ru, "_mark_registry_failed", lambda p: None)
        monkeypatch.setattr(ru, "_cleanup_failed_upload_sync", lambda p, was_overwrite=False: None)
        monkeypatch.setattr(ru, "_safe_log_op", MagicMock())

        with pytest.raises(ChunkingEmptyError):
            it.execute_index_task_impl("u1", "/docs/a.pdf", "a.pdf", retries=0)

        assert any(w["stage"] == "error" for w in redis_writes), \
            "业务终态必须立即发 error 事件"

    def test_lock_conflict_reraises_for_retry(self, monkeypatch):
        """P0-2 清单4:锁冲突是瞬态——重试期间只发进度不发终态,
        交给 Celery autoretry,锁释放后重跑会经 SHA256 检测收敛为 duplicate。"""
        redis_writes = []
        monkeypatch.setattr(ru, "_write_progress_redis",
                            lambda uid, stage, message="", **ex:
                            redis_writes.append({"stage": stage, "message": message}))
        monkeypatch.setattr(ru, "_do_index_sync",
                            lambda *a, **kw: (_ for _ in ()).throw(
                                ru.FileLockedByOtherError("locked")))
        monkeypatch.setattr(ru, "_settle_index_result", MagicMock())

        with pytest.raises(ru.FileLockedByOtherError):
            it.execute_index_task_impl("u1", "/docs/a.pdf", "a.pdf", retries=0)

        assert not any(w["stage"] == "error" for w in redis_writes), \
            "锁冲突重试期间不得发终态 error"
        assert any("自动重试" in w["message"] for w in redis_writes)

    def test_lock_conflict_final_failure_keeps_file(self, monkeypatch):
        """P0-2 清单4:末次重试仍锁冲突 → 终态收口但保留源文件
        （持锁方可能正在读它）。"""
        redis_writes = []
        monkeypatch.setattr(ru, "_write_progress_redis",
                            lambda uid, stage, message="", **ex:
                            redis_writes.append({"stage": stage}))
        monkeypatch.setattr(ru, "_do_index_sync",
                            lambda *a, **kw: (_ for _ in ()).throw(
                                ru.FileLockedByOtherError("locked")))
        # 走真实 _settle_index_result,只 mock 外部副作用
        monkeypatch.setattr(ru, "_mark_registry_failed", lambda p: None)
        cleaned = []
        monkeypatch.setattr(ru, "_cleanup_failed_upload_sync",
                            lambda p, was_overwrite=False: cleaned.append(p))
        monkeypatch.setattr(ru, "_safe_log_op", MagicMock())

        with pytest.raises(ru.FileLockedByOtherError):
            it.execute_index_task_impl("u1", "/docs/a.pdf", "a.pdf",
                                       retries=it.CELERY_MAX_RETRIES)

        assert any(w["stage"] == "error" for w in redis_writes)
        assert cleaned == [], "锁冲突终态也必须保留源文件"


# ═══════════════════════════════════════════════════
# _run_index_background 分流
# ═══════════════════════════════════════════════════

class TestRunIndexBackgroundDispatch:

    @pytest.mark.asyncio
    async def test_celery_dispatch_enqueues_and_returns(self, monkeypatch):
        upload_id, q, _ev, _rw = _mk_queue_ctx(monkeypatch)
        apply_async = MagicMock()
        fake_task = MagicMock()
        fake_task.apply_async = apply_async
        monkeypatch.setattr(it, "execute_index_task", fake_task)
        # 若真的跑了进程内索引即为 bug
        monkeypatch.setattr(ru, "_do_index_sync",
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应执行")))

        await ru._run_index_background(upload_id, "/docs/a.pdf", "a.pdf",
                                       kb_id="kb1", department="general")

        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs
        assert kwargs["queue"] == "rag_index"
        assert kwargs["kwargs"]["upload_id"] == upload_id
        assert kwargs["kwargs"]["filepath"] == "/docs/a.pdf"
        # 队列里只有入队提示事件，无终态
        assert q.qsize() >= 1
        first = q._queue[0]
        assert first["stage"] == "uploading"

    @pytest.mark.asyncio
    async def test_broker_down_falls_back_inproc(self, monkeypatch):
        upload_id, q, _ev, _rw = _mk_queue_ctx(monkeypatch)
        fake_task = MagicMock()
        fake_task.apply_async = MagicMock(side_effect=ConnectionError("broker down"))
        monkeypatch.setattr(it, "execute_index_task", fake_task)
        monkeypatch.setattr(ru, "_do_index_sync",
                            lambda *a, **kw: {"terminal": "duplicate",
                                              "doc": {"doc_id": "d2", "chunk_count": 1}})
        monkeypatch.setattr(ru, "_remove_bak", lambda p: None)
        monkeypatch.setattr(ru, "_safe_log_op", MagicMock())
        monkeypatch.setattr(ru, "_get_index_semaphore", lambda: asyncio.Semaphore(2))

        await ru._run_index_background(upload_id, "/docs/a.pdf", "a.pdf")

        stages = []
        while not q.empty():
            evt = q.get_nowait()
            if evt is not None:
                stages.append(evt["stage"])
        assert "duplicate" in stages, "回退路径必须走到终态收口"

    @pytest.mark.asyncio
    async def test_fallback_lock_conflict_switches_to_worker_channel(self, monkeypatch):
        """P0-2 双重投递兜底:broker 模糊失败 + 本机回退抢锁失败 →
        不收口、不删文件,打标切 Redis 轮询消费 Worker 的终态。"""
        upload_id, q, _ev, _rw = _mk_queue_ctx(monkeypatch)
        fake_task = MagicMock()
        fake_task.apply_async = MagicMock(side_effect=ConnectionError("ambiguous"))
        monkeypatch.setattr(it, "execute_index_task", fake_task)
        monkeypatch.setattr(ru, "_do_index_sync",
                            lambda *a, **kw: (_ for _ in ()).throw(
                                ru.FileLockedByOtherError("locked by worker")))
        monkeypatch.setattr(ru, "_get_index_semaphore", lambda: asyncio.Semaphore(2))
        cleaned = []
        monkeypatch.setattr(ru, "_cleanup_failed_upload_sync",
                            lambda p, was_overwrite=False: cleaned.append(p))
        settle = MagicMock()
        monkeypatch.setattr(ru, "_settle_index_result", settle)

        await ru._run_index_background(upload_id, "/docs/a.pdf", "a.pdf")

        assert upload_id in ru._celery_routed, "必须打标让 SSE 切到 Worker 通道"
        assert cleaned == [], "锁冲突绝不能删源文件（Worker 正在索引它）"
        assert not settle.called, "不能按失败收口（Worker 终态还没来）"
        stages = []
        while not q.empty():
            evt = q.get_nowait()
            if evt is not None:
                stages.append(evt["stage"])
        assert "error" not in stages, "锁冲突不得产生本侧 error 终态"


# ═══════════════════════════════════════════════════
# SSE 通道路由（上传级 _celery_routed 标记）
# ═══════════════════════════════════════════════════

class TestSSEChannelRouting:

    @pytest.mark.asyncio
    async def test_marked_upload_goes_redis_polling(self, monkeypatch):
        """已入队 Celery 的 upload → SSE 端点直接走 Redis 轮询通道。"""
        monkeypatch.setattr(ru, "_celery_routed", {"u-marked"})
        monkeypatch.setattr(ru, "_progress_queues", {})
        called = {}
        monkeypatch.setattr(ru, "_redis_poll_stream_response",
                            lambda uid, last_sig=None: called.setdefault("uid", uid))

        resp = await ru.stream_upload_progress("u-marked")

        assert called["uid"] == "u-marked", "命中标记必须走 Redis 轮询通道"

    @pytest.mark.asyncio
    async def test_queue_mode_switches_to_redis_on_mark(self, monkeypatch):
        """队列模式消费中 upload 被打标 → 无缝切换 Redis 轮询并携带 last_sig 去重。"""
        import json as _json
        upload_id = "u-switch"
        q = asyncio.Queue()
        monkeypatch.setattr(ru, "_celery_routed", set())
        monkeypatch.setattr(ru, "_progress_queues", {upload_id: q})

        # 前置事件先入队（入队 Celery 前的本地事件）
        await q.put({"stage": "uploading", "message": "已保存"})
        # SSE 连接后，后台任务入队成功：先打标再发跨通道事件（实现顺序）
        async def delayed_mark():
            await asyncio.sleep(0.05)
            ru._celery_routed.add(upload_id)
            await q.put({"stage": "uploading", "message": "已入队（Celery Worker 执行）"})
        asyncio.get_running_loop().create_task(delayed_mark())

        # Redis 轮询源替换为可控事件序列：验证 last_sig 去重 + Worker 事件到达
        worker_evt = {"stage": "parsing", "message": "worker 事件"}
        async def fake_poll_events(uid, last_sig=None):
            assert uid == upload_id
            # last_sig 必须携带队列模式最后一条事件的签名（跨通道去重）
            assert last_sig is not None
            yield _ru_sse(ru, worker_evt)
        monkeypatch.setattr(ru, "_redis_poll_events", fake_poll_events)

        chunks = []
        resp = await ru.stream_upload_progress(upload_id)
        async for chunk in resp.body_iterator:
            chunks.append(chunk)

        text = "".join(chunks)
        # SSE data 为 JSON 转义文本，解析后断言
        import json as _json
        msgs = [_json.loads(line[len("data: "):])["message"]
                for line in text.splitlines()
                if line.startswith("data: ") and "message" in line]
        assert any("已保存" in m for m in msgs)
        assert any("已入队" in m for m in msgs), "切换前的队列事件必须完整推送"
        assert any("worker 事件" in m for m in msgs), "打标后必须切换到 Redis 轮询消费 Worker 事件"


def _ru_sse(ru, evt):
    return ru._sse_encode("message", evt)


class TestRedisPollEvents:
    """P1-1 清单5:Redis 轮询通道的过期判定——
    镜像已有事件（入队即写）时不得误报「不存在或已过期」
    （覆盖 Worker 冷启动/排队超过 10s empty-grace 窗口的场景）。"""

    @staticmethod
    def _messages(chunks):
        """解析 SSE data 行为 JSON（与前端 onmessage 同口径）。"""
        import json as _json
        return [_json.loads(line[len("data: "):])
                for line in "".join(chunks).splitlines()
                if line.startswith("data: ")]

    @pytest.mark.asyncio
    async def test_existing_mirror_never_reports_expired(self, monkeypatch):
        monkeypatch.setattr(ru, "_SSE_REDIS_POLL_SECONDS", 0.01)
        monkeypatch.setattr(ru, "_SSE_REDIS_POLL_MAX_SECONDS", 0.05)
        mirror = {"stage": "uploading", "message": "已入队"}
        monkeypatch.setattr(ru, "_read_progress_redis", lambda uid: dict(mirror))

        chunks = []
        async for chunk in ru._redis_poll_events("u-cold"):
            chunks.append(chunk)

        msgs = self._messages(chunks)
        assert not any("不存在或已过期" in str(m) for m in msgs), \
            "镜像存在时绝不能走 empty-grace 过期判定"
        assert any("轮询超时" in m.get("message", "") for m in msgs), \
            "deadline 到达后应走轮询超时兜底"

    @pytest.mark.asyncio
    async def test_empty_mirror_after_grace_reports_expired(self, monkeypatch):
        """镜像持续为空（upload_id 真不存在 / TTL 已过期）→ grace 耗尽明确报过期。"""
        monkeypatch.setattr(ru, "_SSE_REDIS_POLL_SECONDS", 0.01)
        monkeypatch.setattr(ru, "_SSE_REDIS_EMPTY_GRACE_POLLS", 3)
        monkeypatch.setattr(ru, "_read_progress_redis", lambda uid: None)

        chunks = []
        async for chunk in ru._redis_poll_events("u-gone"):
            chunks.append(chunk)

        msgs = self._messages(chunks)
        assert any("不存在或已过期" in m.get("message", "") for m in msgs)

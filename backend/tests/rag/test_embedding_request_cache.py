"""R1-A：请求级 embedding 复用（去重 / 单飞 / 模型隔离 / 失败不入缓存 / 失败计费）。

验收口径（任务书 §四 + PR #41 审查 P1-A/P1-B/P1-C）：
  - 相同 query 的 Doc/Chunk 向量化只出站一次；不同 k 不重复 embedding；
  - 不同模型/维度/文本变体不串用向量；
  - 并发同 key 只出站一次（>=65 key 并发、进程缓存开关两态、失败可恢复）；
  - 失败不入缓存，且失败出站必须留用量审计行（token 记 0，不虚构消费）；
  - 请求级统计跨线程完整（池线程 scope 归并到父请求），并区分请求内命中与进程级命中；
  - 缓存键不保留原始 query 明文。
"""
from __future__ import annotations

import contextvars
import threading
import time
from types import SimpleNamespace

import pytest

from backend.rag import embedding_singleton as es


class _FakeInner:
    def __init__(self):
        self.query_calls: list[str] = []
        self.doc_calls: list[list[str]] = []
        self._lock = threading.Lock()

    def embed_query(self, text):
        with self._lock:
            self.query_calls.append(text)
        return [float(len(text)), 1.0, 2.0]

    def embed_documents(self, texts):
        with self._lock:
            self.doc_calls.append(list(texts))
        return [[float(len(t)), 1.0, 2.0] for t in texts]


# 测试基线签名：_refresh_if_changed 会把当前签名与 _config_signature 比对，
# 不一致就重建客户端（需要 DB 凭据）。测试里把两边固定成同一常量。
_TEST_SIGNATURE = ("test-baseline",)


class _NoopTracker:
    def record(self, *_args, **_kwargs):
        return None


def _make_tracked(inner=None, *, model="qwen3.7-text-embedding", provider="cloud-1",
                  signature=_TEST_SIGNATURE, keep_record: bool = False
                  ) -> es._TrackedEmbedding:
    """构造最小 _TrackedEmbedding：绕开构造器（不碰 DB/凭据），只填缓存所需字段。

    keep_record=True 保留真实 _record 实现（验证失败/成功用量行写入），
    此时 tracker 用空实现，只需要它可调用。
    """
    tracked = es._TrackedEmbedding.__new__(es._TrackedEmbedding)
    tracked._inner = inner or _FakeInner()
    tracked._tracker = _NoopTracker()
    tracked._refresh_lock = threading.RLock()
    tracked._config_signature = _TEST_SIGNATURE
    tracked._model_name = model
    tracked._provider = provider
    tracked.embed_batch_size = 10
    if not keep_record:
        tracked._record = lambda *a, **k: None  # type: ignore[assignment]
    return tracked


def _reset_embed_state():
    es.reset_embedding_cache()
    es._REQUEST_EMBEDS.set(None)
    es._EMBED_REQUEST_RECORD.set(None)
    es._EMBED_SCOPE_OWNER.set(False)
    es._EMBED_INFLIGHT.clear()


@pytest.fixture(autouse=True)
def _fixed_signature(monkeypatch):
    """固定运行时配置签名：防止 _refresh_if_changed 触发真实客户端重建。"""
    monkeypatch.setattr(es, "_embedding_runtime_signature", lambda: _TEST_SIGNATURE)


@pytest.fixture(autouse=True)
def _clean_cache():
    _reset_embed_state()
    yield
    _reset_embed_state()


# ==================== 基础去重语义 ====================

def test_same_query_embedded_once_per_request():
    """同一请求内同一 query 重复取向量只出站一次（k 不同也复用）。"""
    tracked = _make_tracked()
    es.begin_embedding_request(reset=True)
    first = tracked.embed_query("退款审核时间是多少？")
    second = tracked.embed_query("退款审核时间是多少？")
    stats = es.end_embedding_request()
    assert first == second == [10.0, 1.0, 2.0]
    assert len(tracked._inner.query_calls) == 1
    assert stats is not None
    assert stats.requested == 2 and stats.computed == 1
    assert stats.request_hits == 1 and stats.process_hits == 0
    assert stats.unique_inputs == 1
    assert stats.dedup_ratio == pytest.approx(0.5)


def test_distinct_query_variants_keep_own_vectors():
    """不同文本变体（MultiQuery 改写）各自出站，不得共用向量。"""
    tracked = _make_tracked()
    es.begin_embedding_request(reset=True)
    a = tracked.embed_query("退款审核时间")
    b = tracked.embed_query("退款需要多久审核")
    stats = es.end_embedding_request()
    assert a != b
    assert len(tracked._inner.query_calls) == 2
    assert stats is not None and stats.computed == 2 and stats.request_hits == 0


def test_process_cache_reuses_across_requests():
    tracked = _make_tracked()
    es.begin_embedding_request(reset=True)
    tracked.embed_query("发货时间是多久")
    es.end_embedding_request()
    assert len(tracked._inner.query_calls) == 1

    es.begin_embedding_request(reset=True)
    vector = tracked.embed_query("发货时间是多久")
    stats = es.end_embedding_request()
    assert vector == [7.0, 1.0, 2.0]
    assert len(tracked._inner.query_calls) == 1
    assert stats is not None and stats.process_hits == 1 and stats.computed == 0
    assert stats.hit_source == "process"


def test_model_identity_change_never_shares_vectors():
    """换模型/维度/配置签名 → 缓存命名空间变化，禁止跨模型复用。"""
    es.begin_embedding_request(reset=True)
    tracked_a = _make_tracked(model="model-a")
    tracked_a.embed_query("同一个问题")
    es.end_embedding_request()

    es.begin_embedding_request(reset=True)
    tracked_b = _make_tracked(model="model-b")
    tracked_b.embed_query("同一个问题")
    stats = es.end_embedding_request()
    assert len(tracked_b._inner.query_calls) == 1
    assert stats is not None and stats.computed == 1 and stats.process_hits == 0


def test_request_cache_holds_hash_not_raw_query():
    """请求级/进程级缓存键不含原始 query 明文（§四.8）。"""
    tracked = _make_tracked()
    secret = "用户手机号 13800000000 的退款进度"
    es.begin_embedding_request(reset=True)
    tracked.embed_query(secret)
    stats = es.current_embedding_stats()
    assert stats is not None
    assert all(secret not in key for key in stats.values)
    assert all(secret not in key for key in es._PROCESS_EMBED_CACHE)


def test_process_cache_is_bounded():
    """进程级缓存有上限（LRU 淘汰），不随请求数无界增长。"""
    monkey = es._EMBED_PROCESS_CACHE_MAX
    es._EMBED_PROCESS_CACHE_MAX = 3
    try:
        tracked = _make_tracked()
        for i in range(6):
            tracked.embed_query(f"问题{i}")
        assert len(es._PROCESS_EMBED_CACHE) == 3
    finally:
        es._EMBED_PROCESS_CACHE_MAX = monkey
        es.reset_embedding_cache()


def test_embed_documents_behaviour_unchanged():
    """embed_documents（索引路径）保持原语义，只增加出站计数。"""
    tracked = _make_tracked()
    es.begin_embedding_request(reset=True)
    out = tracked.embed_documents(["a", "b"])
    stats = es.end_embedding_request()
    assert out == [[1.0, 1.0, 2.0], [1.0, 1.0, 2.0]]
    assert tracked._inner.doc_calls == [["a", "b"]]
    assert stats is not None and stats.computed == 2


# ==================== P1-B：请求级统计跨线程汇总 ====================

def test_pool_thread_scope_merges_into_parent_request():
    """池线程自建 scope 必须被父请求汇总（P1-B 核心回归）。

    模拟真实形态：主线程 begin 建总账 → copy_context 提交到线程池 → 池线程
    的 contextvar 为空、自建 scope → 父请求 end 时必须看到池线程的出站次数。
    """
    inner = _FakeInner()
    tracked = _make_tracked(inner)
    parent_stats = es.begin_embedding_request(reset=True)

    # 真实形态（与 multi_query._submit_isolated 一致）：**提交方**线程
    # copy_context，池线程用该快照运行——因此池线程能看到请求总账并归属它。
    child_ctx = contextvars.copy_context()

    def pool_worker():
        return child_ctx.run(tracked.embed_query, "池线程问题")

    t = threading.Thread(target=pool_worker)
    t.start()
    t.join(timeout=5.0)

    assert len(inner.query_calls) == 1
    # 父请求的实时视图即可看到子线程贡献
    live = es.current_embedding_stats()
    assert live is not None and live.computed == 1 and live.requested == 1
    final = es.end_embedding_request()
    assert final is not None and final.computed == 1
    assert final.unique_inputs == 1
    # 总账对象是实时的：池线程贡献在父请求视角可读
    assert parent_stats.requested == 1 and parent_stats.computed == 1


def test_kinds_matches_real_call_count():
    """kinds 必须等于真实出站次数。

    历史缺陷：_bump 同时累加字段与 kinds，调用点又分别调用，导致
    kinds=query:4 而 requested=2 的口径失真。
    """
    inner = _FakeInner()
    tracked = _make_tracked(inner)
    es.begin_embedding_request(reset=True)
    tracked.embed_query("一次调用")
    stats = es.end_embedding_request()
    assert stats is not None
    assert stats.requested == 1 and stats.computed == 1
    assert stats.kinds == {"query": 1}, stats.kinds
    assert len(inner.query_calls) == 1


def test_request_level_stats_count_every_branch():
    """Doc 级 + 并行 Chunk 级 + 变体的调用都计入同一请求总账，且不重不漏。"""
    tracked = _make_tracked()
    es.begin_embedding_request(reset=True)
    texts = ["退款审核时间是多少"] * 3 + ["退款需要多久"]

    ctxs = [contextvars.copy_context() for _ in texts]
    threads = [
        threading.Thread(target=lambda c=c, t=t: c.run(tracked.embed_query, t))
        for c, t in zip(ctxs, texts)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5.0)
    stats = es.end_embedding_request()

    assert stats is not None
    # 4 次请求，2 个唯一输入（其中 3 次同文本）
    assert stats.requested == 4
    assert stats.computed == 2
    assert stats.unique_inputs == 2
    assert stats.request_hits + stats.process_hits == 2
    assert stats.computed + stats.request_hits + stats.process_hits == stats.requested


# ==================== P1-C：singleflight 并发边界 ====================

def test_concurrent_same_key_singleflight():
    """并发同 key 只出站一次（其余线程等待后复用）。"""
    gate = threading.Event()
    inner = _FakeInner()
    original = inner.embed_query

    def slow(text):
        gate.wait(timeout=2.0)
        return original(text)

    inner.embed_query = slow  # type: ignore[assignment]
    tracked = _make_tracked(inner)
    es.begin_embedding_request(reset=True)
    results: list = []
    errors: list = []
    ctxs = [contextvars.copy_context() for _ in range(4)]

    def worker(ctx):
        try:
            ctx.run(lambda: results.append(tracked.embed_query("并发同一问题")))
        except Exception as exc:  # noqa: BLE001 — 测试收集异常
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(c,)) for c in ctxs]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, errors
    assert len(results) == 4 and all(r == results[0] for r in results)
    assert len(inner.query_calls) == 1
    stats = es.end_embedding_request()
    assert stats is not None and stats.computed == 1
    assert stats.request_hits + stats.process_hits == 3


def test_many_keys_gt_65_concurrent_no_duplicate_calls():
    """>=65 个不同 key 并发：每 key 恰好出站一次（旧锁表淘汰会重复出站）。"""
    inner = _FakeInner()
    tracked = _make_tracked(inner)
    es.begin_embedding_request(reset=True)
    keys = [f"key-{i}" for i in range(80)]
    barrier = threading.Barrier(80, timeout=10)
    errors: list = []
    ctxs = [contextvars.copy_context() for _ in keys]

    def worker(ctx, text):
        try:
            barrier.wait()
            ctx.run(tracked.embed_query, text)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(c, t)) for c, t in zip(ctxs, keys)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20.0)

    assert not errors, errors[:3]
    # 每个 key 只应出站一次；重复即说明锁被淘汰后重建导致并发重复 compute
    assert len(inner.query_calls) == 80
    assert len(set(inner.query_calls)) == 80
    stats = es.end_embedding_request()
    assert stats is not None and stats.computed == 80 and stats.requested == 80


def test_same_key_maps_to_same_stripe_lock():
    """同一 key 恒映射同一把锁（固定条纹，无淘汰）。"""
    key = "stable-key"
    idx = es._stripe_index(key)
    assert idx == es._stripe_index(key)
    assert 0 <= idx < es._EMBED_LOCK_STRIPES
    assert es._stripe_lock(key) is es._stripe_lock(key)
    # 锁池宽度恒定，不随 key 数增长
    assert len(es._EMBED_STRIPE_LOCKS) == es._EMBED_LOCK_STRIPES


def test_process_cache_disabled_still_dedups_in_request():
    """进程缓存关闭时：请求内去重与同 key 并发单飞仍然生效。"""
    monkey = es._EMBED_PROCESS_CACHE_ENABLED
    es._EMBED_PROCESS_CACHE_ENABLED = False
    try:
        inner = _FakeInner()
        tracked = _make_tracked(inner)
        es.begin_embedding_request(reset=True)
        gate = threading.Event()
        original = inner.embed_query

        def slow(text):
            gate.wait(timeout=2.0)
            return original(text)

        inner.embed_query = slow  # type: ignore[assignment]
        results: list = []
        ctxs = [contextvars.copy_context() for _ in range(4)]

        def worker(ctx):
            ctx.run(lambda: results.append(tracked.embed_query("关缓存同key")))

        threads = [threading.Thread(target=worker, args=(c,)) for c in ctxs]
        for t in threads:
            t.start()
        gate.set()
        for t in threads:
            t.join(timeout=5.0)
        # 同请求内重复调用也不应再出站
        tracked.embed_query("关缓存同key")
        stats = es.end_embedding_request()

        assert len(inner.query_calls) == 1
        assert len(results) == 4
        assert stats is not None and stats.computed == 1
    finally:
        es._EMBED_PROCESS_CACHE_ENABLED = monkey


def test_different_keys_not_serialized_by_global_lock():
    """不同 key 保留并发能力：32 个 slot 在 0.2s 内并行完成，不应被串行化。"""
    inner = _FakeInner()
    tracked = _make_tracked(inner)
    es.begin_embedding_request(reset=True)
    slots = 32
    barrier = threading.Barrier(slots, timeout=10)

    def slow(text):
        time.sleep(0.2)
        return [1.0, 1.0, 1.0]

    inner.embed_query = slow  # type: ignore[assignment]
    started = time.monotonic()

    def worker(text):
        barrier.wait()
        contextvars.copy_context().run(tracked.embed_query, text)

    keys = [f"parallel-key-{i}" for i in range(slots)]
    ctxs = [contextvars.copy_context() for _ in keys]
    threads = [
        threading.Thread(target=lambda c=c, k=k: c.run(worker, k))
        for c, k in zip(ctxs, keys)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    elapsed = time.monotonic() - started
    es.end_embedding_request()
    # 串行需要 32*0.2=6.4s；这里只要证明远低于串行
    assert elapsed < 2.0, f"不同 key 被串行化: {elapsed:.2f}s"


# ==================== 失败语义与失败计费（P1-A） ====================

def test_failure_is_not_cached_and_waiters_retry():
    """失败不入缓存；持锁者失败后等待者能自行重试成功（失败即解除）。"""
    inner = _FakeInner()
    attempts = {"n": 0}
    lock = threading.Lock()

    def flaky(text):
        with lock:
            attempts["n"] += 1
            n = attempts["n"]
        if n == 1:
            time.sleep(0.05)
            raise RuntimeError("provider down")
        return [3.0, 3.0, 3.0]

    inner.embed_query = flaky  # type: ignore[assignment]
    tracked = _make_tracked(inner)
    es.begin_embedding_request(reset=True)
    ok: list = []
    failed: list = []
    ctxs = [contextvars.copy_context() for _ in range(2)]

    def worker(ctx):
        try:
            ctx.run(lambda: ok.append(tracked.embed_query("会失败的问题")))
        except Exception as exc:  # noqa: BLE001
            failed.append(exc)

    threads = [threading.Thread(target=worker, args=(c,)) for c in ctxs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    # 首轮失败被抛出，等待者随后重试成功（不会因他人失败永久拿不到值）
    assert len(failed) == 1
    assert ok == [[3.0, 3.0, 3.0]]
    assert attempts["n"] == 2
    stats = es.end_embedding_request()
    # computed = 真实出站次数（失败 1 次 + 成功 1 次），与 provider 对账一致
    assert stats is not None and stats.computed == 2


def test_provider_failure_records_error_usage_row(monkeypatch):
    """Provider 失败必须留用量审计行（P1-A）：token=0、status=error、不虚构消费。"""
    recorded: list = []
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(record=recorded.append),
    )
    inner = _FakeInner()

    def boom(text):
        raise RuntimeError("provider down")

    inner.embed_query = boom  # type: ignore[assignment]
    tracked = _make_tracked(inner, keep_record=True)
    es.begin_embedding_request(reset=True)
    with pytest.raises(RuntimeError):
        tracked.embed_query("会失败的问题")
    stats = es.end_embedding_request()

    assert recorded, "失败出站没有写用量行"
    row = recorded[0]
    assert row["finish_reason"] == "error"
    assert row["prompt_tokens"] == 0 and row["total_tokens"] == 0
    assert row["component"] == "embedding"
    # 失败也要计入"出站次数"，否则调用数与 provider 对不上
    assert stats is not None and stats.computed == 1


def test_embed_documents_failure_records_error_row(monkeypatch):
    """embed_documents 失败同样留用量行（与 embed_query 口径一致）。"""
    recorded: list = []
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(record=recorded.append),
    )
    inner = _FakeInner()

    def boom(texts):
        raise RuntimeError("batch down")

    inner.embed_documents = boom  # type: ignore[assignment]
    tracked = _make_tracked(inner, keep_record=True)
    es.begin_embedding_request(reset=True)
    with pytest.raises(RuntimeError):
        tracked.embed_documents(["a", "b"])
    stats = es.end_embedding_request()

    assert recorded and recorded[0]["finish_reason"] == "error"
    assert recorded[0]["prompt_tokens"] == 0
    assert stats is not None and stats.computed == 2


def test_cache_hit_produces_no_new_provider_cost(monkeypatch):
    """命中复用不得产生新的用量/计费行（只有真实出站才记账）。"""
    recorded: list = []
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(record=recorded.append),
    )
    tracked = _make_tracked(keep_record=True)
    es.begin_embedding_request(reset=True)
    tracked.embed_query("只出站一次")
    tracked.embed_query("只出站一次")
    tracked.embed_query("只出站一次")
    es.end_embedding_request()
    assert len(recorded) == 1
    assert len(tracked._inner.query_calls) == 1

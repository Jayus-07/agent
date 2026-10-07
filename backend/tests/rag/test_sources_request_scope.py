"""RAG sources 请求级返回（2026-09-23 D1-6 回归）。

修复前 sources/answer_meta 挂在 RAGChain / RAGPipeline 单例实例属性上，
路由在 to_thread 返回后回读单例 → 并发请求互相覆盖（A 的响应带 B 的
来源）。锁定新契约：

- chain._last_sources 实际存储于请求级 context（barrier 交错下各自读写
  互不可见对方写入）；
- pipeline.ask_result 把本请求的 sources/answer_meta 随返回值带回。
"""
import threading

import pytest

from backend.rag.chain import RAGChain
from backend.rag.context import RagRequestState, clear_context, set_context
from backend.rag.pipeline import RAGPipeline

_ROUNDS = 40


def _make_chain() -> RAGChain:
    return RAGChain.__new__(RAGChain)


def test_context_backed_sources_isolated_under_barrier():
    """两个线程交错写读 _last_sources：各自只能看到自己的写入。

    修复前 _last_sources 是单例实例属性——barrier 后双方都读到后写者的
    值（串扰）；修复后随 contextvar 按线程隔离，必然各自一致。
    """
    chain = _make_chain()
    barrier = threading.Barrier(2)
    failures: list[str] = []

    def _worker(marker: str) -> None:
        set_context(RagRequestState(metadata_filter={}, intent_label="",
                                    query=marker))
        for _ in range(_ROUNDS):
            chain._last_sources = [{"marker": marker}]
            barrier.wait()
            got = chain._last_sources
            if [s["marker"] for s in got] != [marker]:
                failures.append(f"{marker} 读到 {got}")
            barrier.wait()

    t1 = threading.Thread(target=_worker, args=("A",))
    t2 = threading.Thread(target=_worker, args=("B",))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert failures == [], f"并发串扰 {len(failures)} 次: {failures[:3]}"


def _make_stub_pipeline(answer: str, sources: list):
    """__new__ 绕过重初始化，桩掉 ask_result 依赖的全部协作方法。"""
    pipe = RAGPipeline.__new__(RAGPipeline)
    pipe.last_answer_meta = {}
    pipe._seen_sessions = {}
    pipe._prepare_context = lambda *a, **k: set_context(
        RagRequestState(metadata_filter={}, intent_label="", query="stub"))
    pipe._check_resources = lambda: True
    pipe._session_has_history = lambda session_id: False
    pipe._check_answer_cache = lambda question, kb_id: None
    pipe._mark_session_seen = lambda session_id: None

    def _execute_chain(question, session_id):
        # 模拟 chain 执行期写入请求级 sources（contextvar 在本线程内）
        set_context(RagRequestState(
            metadata_filter={}, intent_label="", query=question))
        from backend.rag.context import get_context
        get_context().sources = list(sources)
        return answer

    pipe._execute_chain = _execute_chain
    # 真实 _snapshot_answer_meta 把 ctx.sources 打包进 last_answer_meta
    # （截 8 条）；ask_result 的读取点已收进 _ask_inner 返回值（清场前打包，
    # 修复清场后 get_context() 恒新实例导致 sources 恒空的缺陷）
    pipe._snapshot_answer_meta = lambda: setattr(
        pipe, "last_answer_meta", {"can_answer": True, "confidence": 0.9,
                                   "sources": list(sources)})
    pipe._is_rejection = lambda answer: False
    pipe._write_answer_cache = lambda question, kb_id, answer, meta=None: None
    pipe._cleanup = lambda: None
    return pipe


def test_ask_result_returns_request_scoped_sources():
    clear_context()
    pipe = _make_stub_pipeline("答案A", [{"title": "来源A", "snippet": "s"}])

    outcome = pipe.ask_result("问题A", "sess-1", kb_id="default")

    assert outcome.answer == "答案A"
    assert outcome.sources == [{"title": "来源A", "snippet": "s"}]
    assert outcome.answer_meta == {"can_answer": True, "confidence": 0.9,
                                   "sources": [{"title": "来源A", "snippet": "s"}]}


def test_ask_compat_returns_answer_string():
    clear_context()
    pipe = _make_stub_pipeline("兼容答案", [{"title": "s"}])
    assert pipe.ask("问题", "sess-1") == "兼容答案"


def test_concurrent_ask_results_do_not_cross():
    """两线程并发 ask_result：各自的 sources 只能是自己的（barrier 交错）。"""
    barrier = threading.Barrier(2)
    failures: list[str] = []

    def _worker(marker: str) -> None:
        pipe = _make_stub_pipeline(f"答案{marker}", [{"marker": marker}])
        for _ in range(_ROUNDS):
            outcome = pipe.ask_result(f"问题{marker}", f"sess-{marker}")
            barrier.wait()
            got = [s.get("marker") for s in outcome.sources]
            if got != [marker]:
                failures.append(f"{marker} 拿到 {got}")
            barrier.wait()

    t1 = threading.Thread(target=_worker, args=("A",))
    t2 = threading.Thread(target=_worker, args=("B",))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert failures == [], f"并发串扰 {len(failures)} 次: {failures[:3]}"


@pytest.fixture(autouse=True)
def _clean_ctx():
    clear_context()
    yield
    clear_context()

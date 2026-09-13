"""MultiQueryRetriever 并发 context 回归测试（2026-09-14）。

bug：并发变体检索共享同一个 contextvars.Context 对象，Context.run()
不可重入——线程池里第二个变体启动即抛
"cannot enter context: ... is already entered"，变体检索成批失败，
整轮召回为空，前端永远走"知识库暂无相关资料"兜底（单条 delta）。

修复：每个任务独立 copy_context() 携带调用方上下文。

2026-09-14 冒烟补充（安全）：copy 必须发生在提交方（请求）线程——在池
线程任务函数内 copy，拷到的是池线程的空 context，变体检索丢失全部请求
状态（metadata_filter/主体授权/检索缓存失效），禁入库内容可经变体路径
漏出。本文件新增传播断言锁死该行为。
"""
import time

import pytest
from langchain_core.documents import Document

from backend.rag.retrieval import multi_query as mq_mod
from backend.rag.retrieval.multi_query import MultiQueryRetriever


class _SlowFakeRetriever:
    """假底层检索器：按 query 返回独立文档，故意放慢放大并发窗口。"""

    def invoke(self, query: str, *args, **kwargs) -> list:
        time.sleep(0.15)
        return [Document(
            page_content=f"关于{query}的资料",
            metadata={"chunk_id": f"c-{query}", "kb_id": "kb1"},
        )]


@pytest.fixture
def _multi_mode(monkeypatch):
    """强制走多查询路径，改写固定产出 3 个变体。"""
    monkeypatch.setattr(mq_mod, "need_multi_query", lambda q: (True, "always"))
    monkeypatch.setattr(mq_mod, "_rewrite", lambda q: [f"{q} 变体1", f"{q} 变体2", f"{q} 变体3"])


def test_concurrent_variants_all_retrieve(_multi_mode):
    """3 个并发变体都应召回成功（共享 ctx 的旧实现会成批抛 already entered）。"""
    retriever = MultiQueryRetriever(base_retriever=_SlowFakeRetriever())
    docs = retriever._get_relevant_documents("报销流程")
    ids = {d.metadata["chunk_id"] for d in docs}
    assert len(docs) == 3
    assert ids == {"c-报销流程 变体1", "c-报销流程 变体2", "c-报销流程 变体3"}


# ==================== 请求状态传播（安全契约） ====================

class _CtxProbeRetriever:
    """在池线程内读取请求上下文，记录各变体实际看到的 metadata_filter。"""

    def __init__(self):
        self.seen = []

    def invoke(self, query: str, *args, **kwargs) -> list:
        from backend.rag.context import get_context
        time.sleep(0.05)
        self.seen.append((query, dict(get_context().metadata_filter)))
        return [Document(page_content=query, metadata={"chunk_id": query})]


def test_variants_see_request_metadata_filter(_multi_mode, monkeypatch):
    """变体检索必须看到调用方线程的 metadata_filter（授权/filter 前提）。

    池线程内 copy 的实现会拿到惰性默认空 state——filter 为空即意味着
    主体授权与检索过滤整体失效（禁入库内容可漏出）。
    """
    from backend.rag.context import RagRequestState, clear_context, set_context

    probe = _CtxProbeRetriever()
    retriever = MultiQueryRetriever(base_retriever=probe)
    marker_filter = {"kb_id": "policy_general", "doc_type": "financial"}
    set_context(RagRequestState(metadata_filter=marker_filter))
    try:
        retriever._get_relevant_documents("报销流程")
    finally:
        clear_context()
    assert len(probe.seen) == 3, f"3 个变体都应执行: {probe.seen}"
    for q, mf in probe.seen:
        assert mf == marker_filter, (
            f"变体 {q!r} 丢失请求状态: metadata_filter={mf!r}"
        )


def test_variants_see_borrowed_identity(_multi_mode):
    """变体检索必须能借读到权威身份（主体授权的第一输入）。"""
    from backend.core.request_context import RequestContext
    from backend.rag.context import RagRequestState, clear_context, set_context

    probe = _CtxProbeRetriever()
    # 改用 identity 探针
    seen_identity = []
    probe.seen = []
    original_invoke = probe.invoke

    def invoke_with_identity(query, *a, **kw):
        from backend.rag.context import get_context
        seen_identity.append(get_context().identity.department)
        return original_invoke(query, *a, **kw)

    probe.invoke = invoke_with_identity
    retriever = MultiQueryRetriever(base_retriever=probe)
    identity = RequestContext(subject_type="employee", department="hr")
    set_context(RagRequestState(identity=identity))
    try:
        retriever._get_relevant_documents("报销流程")
    finally:
        clear_context()
    assert seen_identity == ["hr"] * 3, f"变体丢失身份借读: {seen_identity}"

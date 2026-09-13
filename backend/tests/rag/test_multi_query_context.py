"""MultiQueryRetriever 并发 context 回归测试（2026-09-14）。

bug：并发变体检索共享同一个 contextvars.Context 对象，Context.run()
不可重入——线程池里第二个变体启动即抛
"cannot enter context: ... is already entered"，变体检索成批失败，
整轮召回为空，前端永远走"知识库暂无相关资料"兜底（单条 delta）。

修复：每个任务独立 copy_context() 携带调用方上下文。
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

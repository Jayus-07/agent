"""RAG 链 LLM 流式回归测试（打字机失效根因，2026-09-14）。

根因：create_stuff_documents_chain 内部是 prompt | llm | StrOutputParser()。
llm 是 _LLMProxy（可调用对象，非 Runnable），LCEL coerce_to_runnable 会把它
包成 RunnableLambda——.stream() 走 invoke 整段生成，只产出 1 个整段 chunk，
前端整段一次性显示，打字机失效；TTFT ≈ 全程生成时间，TPOT 因 delta<2 不采样。

修复：以 generator function（chain._llm_stream）作为模型步传入，coerce 走
RunnableGenerator 分支，.stream() 逐 chunk 拉取代理的 stream。

这些测试锁定两条行为，防止改回直接传代理对象（或改成"返回 generator 的普通
函数"——同样会掉进 RunnableLambda 单 chunk 路径）。
"""
import inspect

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_classic.chains.combine_documents import create_stuff_documents_chain

from backend.infra.llm import proxy as proxy_mod
from backend.rag.chain import _llm_stream

_TOKENS = ["你好", "，", "这是", "逐", "token", "输出", "的", "回答", "内容"]


class _FakeChunkedLLM:
    """假 LLM：stream 逐 token 产出（模拟真 token 级流式 provider）。"""

    def stream(self, *args, **kwargs):
        for t in _TOKENS:
            yield AIMessageChunk(content=t)


class _FakeCallableProxy:
    """模拟直接传 _LLMProxy 的错误用法：可调用 + stream 方法，非 Runnable。"""

    def __call__(self, msgs, *args, **kwargs):
        return AIMessage(content="".join(_TOKENS))

    def invoke(self, msgs, *args, **kwargs):
        return AIMessage(content="".join(_TOKENS))

    def stream(self, msgs, *args, **kwargs):
        yield from _FakeChunkedLLM().stream(msgs)


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    """把 proxy 背后的活跃 LLM 换成假流式模型，并隔离 sink。"""
    proxy_mod.reset_stream_sink()
    monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: _FakeChunkedLLM())
    yield
    proxy_mod.reset_stream_sink()


def _make_chain(model_step):
    prompt = ChatPromptTemplate.from_messages([("human", "问题: {q}\n\n{context}")])
    return create_stuff_documents_chain(
        model_step, prompt,
        document_prompt=PromptTemplate.from_template("{page_content}"),
        document_separator="\n\n---\n\n",
    )


def test_llm_stream_is_generator_function():
    """coerce 走 RunnableGenerator 的前提：_llm_stream 必须是 generator function。

    改成"返回 generator 的普通函数"会掉进 RunnableLambda 单 chunk 路径。
    """
    assert inspect.isgeneratorfunction(_llm_stream)


def test_stuff_chain_streams_per_token():
    """修复行为：链上 .stream() 逐 chunk 产出，拼接 = 完整回答。"""
    chain = _make_chain(_llm_stream)
    chunks = list(chain.stream({
        "q": "退货政策是什么",
        "context": [Document(page_content="退货窗口 30 天。")],
    }))
    assert len(chunks) > 1, "真流式应逐 token 产出多个 chunk（单 chunk = 回退到 RunnableLambda 整段路径）"
    assert "".join(chunks) == "".join(_TOKENS)


def test_stuff_chain_invoke_aggregates():
    """invoke 路径（流式关闭/兜底）行为不变：聚合为完整回答。"""
    chain = _make_chain(_llm_stream)
    r = chain.invoke({
        "q": "退货政策是什么",
        "context": [Document(page_content="退货窗口 30 天。")],
    })
    assert str(r) == "".join(_TOKENS)


def test_callable_proxy_direct_is_single_chunk():
    """锁定上游行为：直接传可调用代理 → RunnableLambda → 单个整段 chunk。

    这是打字机失效的根因机制。若该断言失败说明 langchain 的 coerce 行为
    变化，需重新评估 _llm_stream 包装是否仍必要。
    """
    chain = _make_chain(_FakeCallableProxy())
    chunks = list(chain.stream({
        "q": "退货政策是什么",
        "context": [Document(page_content="退货窗口 30 天。")],
    }))
    assert len(chunks) == 1
    assert str(chunks[0]) == "".join(_TOKENS)

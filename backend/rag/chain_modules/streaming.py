"""RAG Chain 流式响应生成器。"""
from __future__ import annotations

from collections.abc import Iterator

from backend.infra.llm.proxy import _last_call_meta_var
from backend.rag.chain_modules.compat import get_chain_context, get_chain_llm


def _llm_stream(msgs):
    """generator function 包装：LCEL coerce 时走 RunnableGenerator，链上
    .stream() 真正逐 chunk 拉取。直接传 llm（可调用代理，非 Runnable）会被
    coerce 成 RunnableLambda——invoke 整段生成后只 yield 1 个整段 chunk，
    打字机效果失效（2026-09-14）。

    RunnableGenerator 的 transform 语义把"输入迭代器"（langchain 为 tracing
    会包成 itertools._tee）传给本函数，invoke 语义传单个 PromptValue——
    两种形态都归一成逐个 PromptValue 消费，否则真实模型会校验报错。
    """
    state = get_chain_context()
    state.generation_call_meta = {}
    state.generation_input_chars = 0
    inputs = msgs if isinstance(msgs, Iterator) else (msgs,)
    active_llm = get_chain_llm()
    for m in inputs:
        # 只记录大小，不记录资料正文；流结束后在同一上下文读取代理结算信息。
        if hasattr(m, "to_string"):
            state.generation_input_chars = len(m.to_string())
        elif isinstance(m, str):
            state.generation_input_chars = len(m)
        token = _last_call_meta_var.set({})
        try:
            yield from active_llm.stream(m)
        finally:
            state.generation_call_meta = dict(_last_call_meta_var.get() or {})
            _last_call_meta_var.reset(token)

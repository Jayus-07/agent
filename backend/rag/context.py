"""RequestContext — contextvars-backed, coroutine-safe request state.

contextvars.ContextVar is the Python 3.7+ mechanism for isolating state
across asyncio Tasks within a single thread. Unlike threading.local(),
it survives await suspension points: Task A setting a filter will never
leak into Task B, even when both are scheduled on the same event loop.

Usage:
    from backend.rag.context import set_context, get_context

    # Gateway / Pipeline.search():
    ctx = RequestContext(metadata_filter={"person_names": "MeridiHome"}, intent_label="entity_query")
    set_context(ctx)

    # Inside ChunkLevelRetriever._get_relevant_documents():
    ctx = get_context()
    filter_dict = ctx.metadata_filter
"""

import contextvars
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestContext:
    """Per-request metadata carried through the retrieval pipeline.

    除检索过滤外，还承载 RAG 决策中间态（P1 并发隔离）：
      - meta / faithfulness / mq_triggered 随请求上下文隔离，
        避免 RAGChain 单例在并发请求下互相串扰。
    """

    metadata_filter: dict = field(default_factory=dict)
    intent_label: str = ""
    query: str = ""

    # ── 决策中间态（并发隔离：随 contextvar 按请求隔离）──
    meta: dict = field(default_factory=dict)   # LLM 输出 <!--META--> 解析结果
    faithfulness: Any = None                   # FaithfulnessResult（评估结果）
    mq_triggered: bool = False                 # MultiQuery 本次是否触发

    def to_dict(self) -> dict:
        return {
            "metadata_filter": self.metadata_filter,
            "intent_label": self.intent_label,
            "query": self.query,
        }


_request_ctx: contextvars.ContextVar = contextvars.ContextVar("rag_request_ctx")


def set_context(ctx: RequestContext) -> None:
    """Set the current coroutine's request context."""
    _request_ctx.set(ctx)


def clear_context() -> None:
    """重置请求上下文（防止跨请求污染）"""
    _request_ctx.set(RequestContext(metadata_filter={}, intent_label="", query=""))


def get_context() -> RequestContext:
    """Retrieve the current coroutine's request context. Never returns None.

    未 set 时惰性创建并 set 一个稳定实例：contextvars.ContextVar.get(default)
    在未 set 时每次返回传入的 default 对象，若直接返回新实例会导致
   『写一次读一次拿到不同对象』，因此首次访问先 set 自身。
    """
    try:
        return _request_ctx.get()
    except LookupError:
        ctx = RequestContext()
        _request_ctx.set(ctx)
        return ctx

"""RequestContext — contextvars-backed, coroutine-safe request state.

contextvars.ContextVar is the Python 3.7+ mechanism for isolating state
across asyncio Tasks within a single thread. Unlike threading.local(),
it survives await suspension points: Task A setting a filter will never
leak into Task B, even when both are scheduled on the same event loop.

Usage:
    from retrieval.context import set_context, get_context

    # Gateway / Pipeline.search():
    ctx = RequestContext(metadata_filter={"person_names": "MeridiHome"}, intent_label="entity_query")
    set_context(ctx)

    # Inside ChunkLevelRetriever._get_relevant_documents():
    ctx = get_context()
    filter_dict = ctx.metadata_filter
"""

import contextvars
from dataclasses import dataclass, field


@dataclass
class RequestContext:
    """Per-request metadata carried through the retrieval pipeline."""

    metadata_filter: dict = field(default_factory=dict)
    intent_label: str = ""
    query: str = ""

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
    """Retrieve the current coroutine's request context. Never returns None."""
    return _request_ctx.get(RequestContext())

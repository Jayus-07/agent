"""RAG 请求运行态 — contextvars-backed, coroutine-safe request state.

contextvars.ContextVar is the Python 3.7+ mechanism for isolating state
across asyncio Tasks within a single thread. Unlike threading.local(),
it survives await suspension points: Task A setting a filter will never
leak into Task B, even when both are scheduled on the same event loop.

统一上下文重构（2026-09-14）：身份/授权字段不在本模块定义——集中在权威
上下文 backend.core.request_context.RequestContext，本模块经 identity
字段组合借读（图路径由 RequestContext.bind() 注入；直连路径由
pipeline._prepare_context 以调用方声明回填）。叶子层读取 API 形状不变。

Usage:
    from backend.rag.context import set_context, get_context

    # Gateway / Pipeline.search():
    ctx = RagRequestState(metadata_filter={"person_names": "MeridiHome"}, intent_label="entity_query")
    set_context(ctx)

    # Inside ChunkLevelRetriever._get_relevant_documents():
    ctx = get_context()
    filter_dict = ctx.metadata_filter
"""

import contextvars
from dataclasses import dataclass, field
from typing import Any

from backend.core.request_context import RequestContext


@dataclass
class RagRequestState:
    """Per-request retrieval inputs + runtime decision state.

    检索过滤输入 + RAG 决策中间态随请求上下文隔离（P1 并发隔离），
    避免 RAGChain 单例在并发请求下互相串扰。身份主体/部门等授权字段
    不在此定义，经 identity 引用权威上下文借读（组合而非复制）。
    """

    # ── 请求输入（检索过滤与路由）──
    metadata_filter: dict = field(default_factory=dict)
    intent_label: str = ""
    query: str = ""

    # ── 身份（权威上下文实例，组合借读）──
    # 图路径：RequestContext.bind() 注入 graph 级实例；直连路径（CS/eval/
    # API）：使用默认实例，由 pipeline._prepare_context 以调用方声明回填。
    identity: RequestContext = field(default_factory=RequestContext)

    # ── 决策中间态（并发隔离：随 contextvar 按请求隔离）──
    meta: dict = field(default_factory=dict)   # LLM 输出 <!--META--> 解析结果
    faithfulness: Any = None                   # FaithfulnessResult（评估结果）
    mq_triggered: bool = False                 # MultiQuery 本次是否触发

    # ── QueryAnalyzer 缓存（避免同一请求内多次调用）──
    query_analysis: Any = None

    # ── 证据门控 & 自纠正（每请求独立实例，避免并发串扰）──
    gate: Any = None
    corrector: Any = None

    # ── 请求内检索缓存（2026-09-03 P1-5）──
    # key=(query, filter_items, k) → 最终检索结果；跨请求随 context 隔离。
    # 治理 MultiQuery 变体 × 同义词扩展组合出的重复检索调用。
    retrieval_cache: dict = field(default_factory=dict)
    retrieval_cache_hits: int = 0

    def to_dict(self) -> dict:
        return {
            "metadata_filter": self.metadata_filter,
            "intent_label": self.intent_label,
            "query": self.query,
        }


_request_ctx: contextvars.ContextVar = contextvars.ContextVar("rag_request_ctx")


def set_context(ctx: RagRequestState) -> None:
    """Set the current coroutine's request context."""
    _request_ctx.set(ctx)


def clear_context() -> None:
    """重置请求上下文（防止跨请求污染）"""
    _request_ctx.set(RagRequestState(metadata_filter={}, intent_label="", query=""))


def get_context() -> RagRequestState:
    """Retrieve the current coroutine's request context. Never returns None.

    未 set 时惰性创建并 set 一个稳定实例：contextvars.ContextVar.get(default)
    在未 set 时每次返回传入的 default 对象，若直接返回新实例会导致
   『写一次读一次拿到不同对象』，因此首次访问先 set 自身。
    """
    try:
        return _request_ctx.get()
    except LookupError:
        ctx = RagRequestState()
        _request_ctx.set(ctx)
        return ctx


def attach_identity(identity: RequestContext) -> None:
    """把权威上下文挂到当前 RAG 运行态（身份组合借读的注入点）。

    仅由 RequestContext.bind() 调用——orchestration → RAG 的唯一身份
    转换点。contextvar 线程/任务本地，不影响其他并发请求；重复注入
    同一实例幂等。
    """
    get_context().identity = identity

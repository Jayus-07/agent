"""审核可见性过滤 — 全部普通检索出口的单一公共实现（Batch 1A）。

覆盖三类出口（2026-10-12 审计 P0-2 收口）：
- ``hybrid_retrieve``：/chat ask 全链（原有 4.1b 过滤，改为委托本模块）；
- ``RAGPipeline.retrieve_knowledge``：/retrieve、旅游/选品/经营分析/客服内部消费；
- ``RAGPipeline.retrieve_documents``：/rag/search、/retrieve_docs（MCP 检索消费方
  经 search_knowledge_tool 走 ask 链路，同样被覆盖）。

可见性规则沿用 DocumentRegistry 既有状态与生命周期规则，不新增状态机：
不可见集合 = pending_review ∪ deprecated ∪ 已过期 active（list_expired）。

fail-closed 契约（修复原 hybrid 实现的 fail-open，审计 P1-3）：
- Registry（PG）不可用 → 抛 :class:`ReviewVisibilityUnavailableError`，由检索
  出口显式失败；状态无法确认的文档一律不对外返回，绝不静默放行。
- Redis 版本键只是跨进程失效的加速通道：审批/下线方 INCR，检索方节流轮询，
  命中即提前失效集合缓存。Redis 不可用 → 退回 ``_COLLECT_TTL`` 兜底，正确性
  不依赖 Redis（可见性事实源是 PG）。
"""
from __future__ import annotations

import time

from backend.shared.logger import logger

__all__ = [
    "ReviewVisibilityUnavailableError",
    "get_review_blocked_doc_ids",
    "filter_review_blocked",
    "invalidate_pending_review_cache",
]

_BLOCK_STATUSES = ("pending_review", "deprecated")
# 集合缓存上限：Registry 故障场景的失效与 TTL 兜底窗口。
_COLLECT_TTL = 30.0
# 版本号轮询节流：跨进程变更的最快感知间隔。
_VERSION_POLL_TTL = 5.0
_VERSION_KEY = "rag:review_block:ver"


class ReviewVisibilityUnavailableError(RuntimeError):
    """审核可见性事实源（DocumentRegistry/PG）不可用——检索必须显式失败。"""


# 进程内两层缓存：版本号（节流轮询）+ 不可见集合（短 TTL）。
_version_cache: dict = {"value": None, "ts": 0.0}
_block_cache: dict = {"ids": frozenset(), "ts": 0.0}


def invalidate_pending_review_cache() -> None:
    """审核/生命周期状态变更后调用：清本进程缓存并广播跨进程失效信号。

    Redis 写失败只影响其他进程的失效速度（退回 TTL 兜底），本进程立即生效；
    不改变 fail-closed 语义。
    """
    _block_cache["ids"] = frozenset()
    _block_cache["ts"] = 0.0
    _version_cache["ts"] = 0.0
    try:
        from backend.infra.redis.client import get_redis

        get_redis().incr(_VERSION_KEY)
    except Exception as e:  # noqa: BLE001 - 广播是尽力而为，见模块 docstring
        logger.debug(f"[ReviewFilter] 跨进程失效广播失败（TTL 兜底）: {e}")


def _read_remote_version():
    """读跨进程版本号；Redis 不可用返回 None（调用方按"无信号"处理）。"""
    try:
        from backend.infra.redis.client import get_redis

        value = get_redis().get(_VERSION_KEY)
        return int(value) if value is not None else None
    except Exception:  # noqa: BLE001 - 无 Redis 时纯 TTL 兜底
        return None


def _build_blocked_ids() -> frozenset[str]:
    """查 Registry 构建不可见集合；任何查询异常都视为事实源不可用。"""
    from backend.config import DOC_REGISTRY_PATH
    from backend.rag.indexing.doc_registry import DocumentRegistry

    registry = DocumentRegistry(DOC_REGISTRY_PATH)
    blocked: set[str] = set()
    for row in registry.list_by_statuses(_BLOCK_STATUSES):
        doc_id = row.get("doc_id", "")
        if doc_id:
            blocked.add(doc_id)
    # 过期时效知识（list_expired 只查 active 行，按日粒度）
    for row in registry.list_expired():
        doc_id = row.get("doc_id", "")
        if doc_id:
            blocked.add(doc_id)
    return frozenset(blocked)


def get_review_blocked_doc_ids() -> frozenset[str]:
    """返回当前不可检索的 doc_id 集合（fail-closed）。

    常规路径 30s 集合缓存 + 5s 节流的版本探测；版本变化或缓存过期才重建。
    Registry 查询失败抛 :class:`ReviewVisibilityUnavailableError`——调用方
    必须让本次检索显式失败，不得把"状态未知"当成"全部可见"或空结果。
    """
    now = time.monotonic()

    if now - _version_cache["ts"] >= _VERSION_POLL_TTL:
        _version_cache["value"] = _read_remote_version()
        _version_cache["ts"] = now
        remote = _version_cache["value"]
        if (
            remote is not None
            and _block_cache["ids"]
            and _version_cache.get("last_seen") is not None
            and remote != _version_cache["last_seen"]
        ):
            logger.info("[ReviewFilter] 检测到跨进程审核状态变更，失效集合缓存")
            _block_cache["ids"] = frozenset()
            _block_cache["ts"] = 0.0
        if remote is not None:
            _version_cache["last_seen"] = remote

    if _block_cache["ids"] or now - _block_cache["ts"] < _COLLECT_TTL:
        return _block_cache["ids"]

    try:
        ids = _build_blocked_ids()
    except Exception as e:  # noqa: BLE001 - fail-closed：转换后向上抛
        raise ReviewVisibilityUnavailableError(
            f"审核可见性事实源不可用，已拒绝本次检索: {e}") from e
    _block_cache["ids"] = ids
    _block_cache["ts"] = now
    return ids


def filter_review_blocked(docs: list, *, context: str = "") -> list:
    """剔除 doc_id ∈ 不可见集合的结果；Registry 不可用时向上抛（不降级放行）。

    保留原 hybrid 实现的两条既有行为：无 metadata 的对象按"不属于 blocked"
    处理；空集合时原样返回（零开销路径）。
    """
    if not docs:
        return docs
    blocked = get_review_blocked_doc_ids()
    if not blocked:
        return docs
    filtered = [
        d for d in docs
        if (d.metadata.get("doc_id") if hasattr(d, "metadata") else None) not in blocked
    ]
    if len(filtered) < len(docs):
        logger.info(
            f"[ReviewFilter] 过滤审核态/下线/过期文档命中 "
            f"{len(docs) - len(filtered)} 条{f'（{context}）' if context else ''}"
        )
    return filtered

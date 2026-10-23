"""审核可见性过滤 — 全部普通检索出口的单一公共实现（Batch 1A + 返修）。

覆盖三类出口（2026-10-12 审计 P0-2 收口；同日 GPT 复核返修）：
- ``hybrid_retrieve``：/chat ask 全链（对客，严格模式）；
- ``RAGPipeline.retrieve_knowledge``：/retrieve、旅游/选品/经营分析/客服内部消费；
- ``RAGPipeline.retrieve_documents``：/rag/search、/retrieve_docs（MCP 检索消费方
  经 search_knowledge_tool 走 ask 链路，同样被覆盖）。

可见性规则沿用 DocumentRegistry 既有状态与生命周期规则，不新增状态机：
可见 = status=active 且未过期；不可见 = pending_review ∪ deprecated ∪
已过期 active ∪ deleted（正向校验天然覆盖删除残留）。

双层防线（返修后语义，修复原 fail-open 与黑名单缓存窗口）：
1. 黑名单快路径：``get_review_blocked_doc_ids``（进程内 TTL 缓存 + Redis
   版本键跨进程失效加速；非空集合同样受 TTL 约束）；
2. **实时批量正向校验**：``verify_visible_doc_ids`` 对候选 doc_id 一条 SQL
   查 Registry 当前状态——Redis 故障/TTL 窗口内的新下线、发布即待审、删除
   残留由此兜底；结果只包含校验通过的文档。

fail-closed 契约：
- Registry（PG）任何查询失败 → :class:`ReviewVisibilityUnavailableError`，
  检索显式失败；状态无法确认的文档一律不返回，绝不用过期安全状态放行。
- 缺 doc_id 的结果（无法确认生命周期）：对客主体（严格模式）剔除；内部
  系统主体（``system_subject`` 非空）存量兼容放行并 warning 留痕。
- Redis 版本键只是失效加速通道，正确性不依赖 Redis（事实源是 PG）。
"""
from __future__ import annotations

import time
from contextvars import ContextVar

from backend.shared.logger import logger

__all__ = [
    "ReviewVisibilityUnavailableError",
    "get_review_blocked_doc_ids",
    "verify_visible_doc_ids",
    "apply_review_visibility",
    "invalidate_pending_review_cache",
]

_BLOCK_STATUSES = ("pending_review", "deprecated")
# 集合缓存 TTL（非空集合同样过期，返修缺陷 1）。
_COLLECT_TTL = 30.0
# 版本号轮询节流：跨进程变更的最快感知间隔。
_VERSION_POLL_TTL = 5.0
_VERSION_KEY = "rag:review_block:ver"


class ReviewVisibilityUnavailableError(RuntimeError):
    """审核可见性事实源（DocumentRegistry/PG）不可用——检索必须显式失败。"""

    CODE = "REVIEW_VISIBILITY_UNAVAILABLE"
    HEADER = "X-RAG-Error-Code"


# 进程内两层缓存：版本号（节流轮询）+ 不可见集合（短 TTL）。
_version_cache: dict = {"value": None, "ts": 0.0, "last_seen": None}
visibility_snapshot: ContextVar = ContextVar("rag_visibility_snapshot", default=None)
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
    """返回当前不可检索的 doc_id 黑名单（fail-closed，快路径）。

    集合缓存按 ``_COLLECT_TTL`` 过期——**非空集合同样过期重建**（返修缺陷 1：
    此前非空集合绕过 TTL，Redis 故障期间新下线文档可能长期不在黑名单）。
    跨进程版本信号命中即提前失效。Registry 查询失败抛
    :class:`ReviewVisibilityUnavailableError`。黑名单只作快路径，最终可见性
    由 :func:`verify_visible_doc_ids` 实时正向校验决定。
    """
    if visibility_snapshot.get() is not None:
        return frozenset()
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

    # 返修：非空集合与空集合一律按 TTL 过期，杜绝"构建一次永不重建"。
    if now - _block_cache["ts"] < _COLLECT_TTL:
        return _block_cache["ids"]

    try:
        ids = _build_blocked_ids()
    except Exception as e:  # noqa: BLE001 - fail-closed：转换后向上抛
        raise ReviewVisibilityUnavailableError(
            f"审核可见性事实源不可用，已拒绝本次检索: {e}") from e
    _block_cache["ids"] = ids
    _block_cache["ts"] = now
    return ids


def verify_visible_doc_ids(doc_ids) -> set[str]:
    """批量正向状态校验（实时、不走任何缓存）：返回确认可见的 doc_id 子集。

    可见 = Registry 当前行 status='active' 且未过期。pending_review/deprecated/
    deleted/过期的 doc_id 不在返回集内——包括黑名单 TTL/Redis 中断窗口内
    刚发生状态变更的文档。Registry 异常抛 :class:`ReviewVisibilityUnavailableError`。
    """
    ids = {d for d in (doc_ids or []) if d}
    frozen = visibility_snapshot.get()
    if frozen is not None:
        return ids & set(frozen)
    if not ids:
        return set()
    from backend.config import DOC_REGISTRY_PATH
    from backend.rag.indexing.doc_registry import DocumentRegistry

    try:
        # 构造与查询同在 try 内（最终收口第二项）：Registry 初始化失败
        # （PG 不可达等）与查询失败都必须包装为结构化异常，禁止裸异常
        # 逃逸后被软降级吞掉、伪装成空结果。
        registry = DocumentRegistry(DOC_REGISTRY_PATH)
        return registry.list_visible_doc_ids(ids)
    except ReviewVisibilityUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001 - fail-closed：转换后向上抛
        raise ReviewVisibilityUnavailableError(
            f"审核可见性正向校验不可用，已拒绝本次检索: {e}") from e


def apply_review_visibility(
    docs: list, *, allow_missing_doc_id: bool = False, context: str = ""
) -> list:
    """检索出口统一收口：黑名单快路径 + 实时正向校验 + 缺 doc_id 策略。

    - 黑名单命中（pending_review/deprecated/过期）→ 剔除；
    - 其余候选 doc_id 批量正向校验（一条 SQL），只保留 Registry 确认
      当前 active 且未过期的文档——deleted 残留、窗口期变更全部拦下；
    - 缺 doc_id（无法确认生命周期）：``allow_missing_doc_id=True``（内部
      system_subject 存量兼容）放行并 warning；否则剔除（对客 fail-closed）；
    - Registry 任何一层故障 → :class:`ReviewVisibilityUnavailableError`，
      不降级、不返回部分结果。
    """
    if not docs:
        return docs
    blocked = get_review_blocked_doc_ids()

    with_id: list = []
    missing: list = []
    for d in docs:
        doc_id = d.metadata.get("doc_id") if hasattr(d, "metadata") else None
        if not doc_id:
            missing.append(d)
        elif doc_id in blocked:
            continue  # 黑名单快路径
        else:
            with_id.append(d)

    visible_ids = verify_visible_doc_ids(
        d.metadata.get("doc_id") for d in with_id)
    visible = [d for d in with_id if d.metadata.get("doc_id") in visible_ids]

    if missing:
        if allow_missing_doc_id:
            logger.warning(
                "[ReviewFilter] %d 条结果缺 doc_id，内部主体存量兼容放行%s",
                len(missing), f"（{context}）" if context else "")
            visible.extend(missing)
        else:
            logger.info(
                "[ReviewFilter] 剔除 %d 条缺 doc_id 结果（对客 fail-closed%s）",
                len(missing), f"，{context}" if context else "")

    dropped = len(docs) - len(visible)
    if dropped:
        logger.info(
            "[ReviewFilter] 审核可见性过滤命中 %d/%d 条%s",
            dropped, len(docs), f"（{context}）" if context else "")
    return visible

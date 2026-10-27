"""bundle_registry — 能力包与静态能力产品授权的进程内快照（Batch 2）。

数据流：ai.capability_bundles / ai.capability_bundle_items /
ai.product_bundle_grants（事实源）→ 15s 轮询派生 → 进程内快照。

授权语义（ADR-0011 D5 扩展到静态能力）：
  - 动态 mcp.*：执行契约仍是 mcp_registry 快照的 grants（不变）；
  - 静态能力：product_bundle_grants 非空时按"产品被授权的包的成员并集"
    白名单判定；表为空 → 回退既有硬编码域黑名单（兼容现状，见
    product_context.product_allows_capability 的静态分支）；
  - 判定失败 fail-closed。
"""
from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.shared.logger import logger

_REFRESH_INTERVAL_S = 15.0

# 只读常量查询（无任何外部输入插值；与 mcp_registry.store 同模式）。
_SELECT_BUNDLES = (
    "SELECT id, name, domain, enabled FROM ai.capability_bundles"
)
_SELECT_ITEMS = (
    "SELECT bundle_id, capability FROM ai.capability_bundle_items"
)
_SELECT_GRANTS = (
    "SELECT bundle_id, product_key, tenant_id FROM ai.product_bundle_grants"
    " WHERE enabled AND revoked_at IS NULL"
)

_lock = threading.Lock()
_last_signature: str = ""
_change_callbacks: list[Callable[[], None]] = []


@dataclass(frozen=True)
class BundleSnapshot:
    loaded: bool = False
    # bundle_id → {"name": ..., "domain": ..., "enabled": bool}
    bundles: dict[str, dict[str, Any]] = field(default_factory=dict)
    # bundle_id → frozenset(capability)
    items: dict[str, frozenset[str]] = field(default_factory=dict)
    # (product_key, bundle_id, tenant_id) 有效授权
    grants: frozenset[tuple[str, str, str]] = frozenset()
    refreshed_at: float = 0.0

    def signature(self) -> str:
        payload = (
            tuple(sorted(
                (bid, b.get("name", ""), bool(b.get("enabled")))
                for bid, b in self.bundles.items()
            )),
            tuple(sorted(
                (bid, tuple(sorted(caps)))
                for bid, caps in self.items.items()
            )),
            sorted(self.grants),
        )
        return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:16]

    def product_capabilities(self, product_id: str, tenant_id: str) -> frozenset[str]:
        """产品被授权（未撤销）的全部 capability 成员并集。"""
        caps: set[str] = set()
        for pid, bundle_id, tid in self.grants:
            if pid != product_id:
                continue
            if tid not in ("", "default", tenant_id):
                continue
            bundle = self.bundles.get(bundle_id)
            if bundle is None or not bundle.get("enabled"):
                continue
            caps |= self.items.get(bundle_id, frozenset())
        return frozenset(caps)


_snapshot = BundleSnapshot()


def snapshot() -> BundleSnapshot:
    with _lock:
        return _snapshot


def register_on_change(callback: Callable[[], None]) -> Callable[[], None]:
    _change_callbacks.append(callback)

    def _unregister() -> None:
        try:
            _change_callbacks.remove(callback)
        except ValueError:
            pass

    return _unregister


def _invalidate_consumers() -> None:
    for callback in list(_change_callbacks):
        try:
            callback()
        except Exception:  # noqa: BLE001 — 失效钩子软失败
            logger.warning("[BundleRegistry] 失效回调执行失败", exc_info=True)


async def load_snapshot() -> BundleSnapshot:
    """读三段表派生快照；表未建 / DB 不可用 → loaded=False（回退黑名单）。"""
    import time as _time

    from sqlalchemy import text

    from backend.memory.database import get_session

    try:
        async for session in get_session():
            brows = (await session.execute(text(_SELECT_BUNDLES))).mappings().all()
            irows = (await session.execute(text(_SELECT_ITEMS))).mappings().all()
            grows = (await session.execute(text(_SELECT_GRANTS))).mappings().all()
            break
        else:
            raise RuntimeError("get_session 未产出会话")
    except Exception:
        logger.warning(
            "[BundleRegistry] DB 读取失败，保留上次快照（表未建时先执行 "
            "python scripts/init_db.py，宿主机加 --port 5433）", exc_info=True,
        )
        return BundleSnapshot(loaded=False)

    bundles = {
        str(r["id"]): {
            "name": str(r["name"] or ""),
            "domain": str(r["domain"] or "general"),
            "enabled": bool(r["enabled"]),
        }
        for r in brows
    }
    items: dict[str, set[str]] = {bid: set() for bid in bundles}
    for r in irows:
        bid = str(r["bundle_id"])
        if bid in items:
            items[bid].add(str(r["capability"] or ""))
    grants = {
        (str(r["product_key"]), str(r["bundle_id"]), str(r["tenant_id"] or "default"))
        for r in grows
    }
    return BundleSnapshot(
        loaded=True,
        bundles=bundles,
        items={bid: frozenset(caps) for bid, caps in items.items()},
        grants=frozenset(grants),
        refreshed_at=_time.time(),
    )


async def refresh() -> bool:
    global _snapshot, _last_signature
    snap = await load_snapshot()
    if not snap.loaded:
        return False
    signature = snap.signature()
    with _lock:
        changed = signature != _last_signature
        _snapshot = snap
        if changed:
            _last_signature = signature
    if changed:
        logger.info(
            "[BundleRegistry] 快照已更新: %d 个能力包 / %d 条有效授权",
            len(snap.bundles), len(snap.grants),
        )
        _invalidate_consumers()
    return True


async def refresh_loop(interval: float = _REFRESH_INTERVAL_S) -> None:
    while True:
        try:
            await refresh()
        except Exception:
            logger.warning("[BundleRegistry] 刷新循环异常，保留上次快照",
                           exc_info=True)
        await asyncio.sleep(interval)


def bundle_allows(product_id: str, capability: str, tenant_id: str) -> bool | None:
    """静态能力的包授权判定。返回 None = 表未加载或无任何授权行（回退），
    True/False = 白名单判定结果。"""
    if not capability:
        return False
    snap = snapshot()
    if not snap.loaded or not snap.grants:
        return None
    return capability in snap.product_capabilities(
        product_id, tenant_id or "default")


def inject_snapshot_for_tests(snap: BundleSnapshot) -> None:
    global _snapshot, _last_signature
    with _lock:
        _snapshot = snap
        _last_signature = snap.signature()


def reset_for_tests() -> None:
    global _snapshot, _last_signature
    with _lock:
        _snapshot = BundleSnapshot()
        _last_signature = ""


__all__ = [
    "BundleSnapshot",
    "bundle_allows",
    "inject_snapshot_for_tests",
    "load_snapshot",
    "refresh",
    "refresh_loop",
    "register_on_change",
    "reset_for_tests",
    "snapshot",
]

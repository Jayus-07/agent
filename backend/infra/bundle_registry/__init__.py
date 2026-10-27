"""bundle_registry — 能力包注册表（Batch 2，ADR-0011 D5 扩展）。"""
from backend.infra.bundle_registry.store import (
    BundleSnapshot,
    bundle_allows,
    inject_snapshot_for_tests,
    load_snapshot,
    refresh,
    refresh_loop,
    register_on_change,
    reset_for_tests,
    snapshot,
)

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

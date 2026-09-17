"""market_research/store.py — 市场调研任务与证据持久化（接口层，PG 实现）

- mr_tasks：调研任务（列表接口不返回 report_md 大字段）
- mr_evidence：标准化证据行（task_id + evidence_id 联合主键，支持同一证据跨任务复用）
- 2026-09-17 SQLite 轨已删除，唯一实现为 PostgresMarketResearchStore（store_pg.py）。
"""
from __future__ import annotations


class MarketResearchStore:
    """市场调研任务 + 证据存储接口（唯一实现：PostgresMarketResearchStore）。"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.market_research.store_pg import PostgresMarketResearchStore
        return super().__new__(PostgresMarketResearchStore)

    # ── 任务 ────────────────────────────────────
    # ── 证据 ────────────────────────────────────


_store: MarketResearchStore | None = None


def get_market_research_store() -> MarketResearchStore:
    """模块级单例（2026-09-17 SQLite 轨删除，直连 PG 实现）。"""
    global _store
    if _store is None:
        from backend.market_research.store_pg import PostgresMarketResearchStore
        _store = PostgresMarketResearchStore()
    return _store

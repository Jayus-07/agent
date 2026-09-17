"""competitor/store.py — 竞品监控存储（接口层，PG 实现）

两表设计:
  - competitor_watchlist  — 用户配置的监控项（竞品 URL / 名称 / 平台）
  - competitor_snapshots  — 每次抓取的结构化快照（支撑价格趋势 / 变价告警）

设计要点:
  - 快照 append-only：竞品网站改版后历史数据不丢，可回溯
  - 原始正文存档（raw_excerpt），抽取规则出错时可溯源排查
  - 2026-09-17 SQLite 轨已删除，唯一实现为 PostgresCompetitorStore（store_pg.py）。
"""
from typing import Optional


class CompetitorStore:
    """竞品数据存储接口（唯一实现：PostgresCompetitorStore）"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.competitor.store_pg import PostgresCompetitorStore
        return super().__new__(PostgresCompetitorStore)

    # ── watchlist CRUD ──────────────────────────────

    # ── snapshots ───────────────────────────────────

    # ── config (key-value) ─────────────────────────

    # ── 风控事件日志（防封策略观测/降级依据） ─────────────


_store: Optional[CompetitorStore] = None


def get_store() -> CompetitorStore:
    """全局单例（2026-09-17 SQLite 轨删除，直连 PG 实现）"""
    global _store
    if _store is None:
        from backend.competitor.store_pg import PostgresCompetitorStore
        _store = PostgresCompetitorStore()
    return _store


def reset_store() -> None:
    """重置全局单例（测试隔离 / 重新初始化时使用）"""
    global _store
    _store = None

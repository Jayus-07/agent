"""selection/store.py — 选品引擎存储（接口层，PG 实现）

两表设计:
  - selection_scores   — 评分结果缓存（快照无更新时命中缓存）
  - selection_weights  — 评分权重配置（可调）
  - 2026-09-17 SQLite 轨已删除，唯一实现为 PostgresSelectionStore
    （store_pg.py）；本模块保留接口与默认权重常量。
"""
import os
from typing import Optional

# 与 spec §5.1 一致的默认权重
DEFAULT_WEIGHTS: dict[str, float] = {
    "reputation": 0.25,
    "heat": 0.25,
    "price": 0.20,
    "differentiation": 0.15,
    "stability": 0.15,
}


class SelectionStore:
    """选品引擎存储接口（唯一实现：PostgresSelectionStore）"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.selection.store_pg import PostgresSelectionStore
        return super().__new__(PostgresSelectionStore)

    pass


_store: Optional[SelectionStore] = None


def get_selection_store() -> SelectionStore:
    """全局单例（2026-09-17 SQLite 轨删除，直连 PG 实现）"""
    global _store
    if _store is None:
        from backend.selection.store_pg import PostgresSelectionStore
        _store = PostgresSelectionStore()
    return _store


def reset_selection_store() -> None:
    """重置全局单例（测试隔离）"""
    global _store
    _store = None

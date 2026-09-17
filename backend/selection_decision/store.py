"""selection_decision/store.py — 选品决策任务持久化（接口层，PG 实现）

列表接口不返回 report_md 大字段，详情接口才返回。
2026-09-17 SQLite 轨已删除，唯一实现为 PostgresSelectionDecisionStore
（store_pg.py）；本模块保留接口与共享行转换工具。
"""
from __future__ import annotations

import json as _json
from typing import Any


class SelectionDecisionStore:
    """选品决策任务存储接口（唯一实现：PostgresSelectionDecisionStore）。"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.selection_decision.store_pg import PostgresSelectionDecisionStore
        return super().__new__(PostgresSelectionDecisionStore)

    # ==================== decision_log（批次3：决策留痕与反馈闭环） ====================
    # 证据/评分快照一旦写入不可变；可变字段仅限
    # user_decision / actual_metrics / feedback_at，且各有专用方法。

    @staticmethod
    def _decision_row_to_dict(row) -> dict[str, Any]:
        d = dict(row)
        for key in ("evidence_snapshot", "score_snapshot", "actual_metrics"):
            try:
                d[key] = _json.loads(d[key]) if d.get(key) else {}
            except (TypeError, ValueError):
                d[key] = {}
        return d


_store: SelectionDecisionStore | None = None


def get_selection_decision_store() -> SelectionDecisionStore:
    """模块级单例（2026-09-17 SQLite 轨删除，直连 PG 实现）。"""
    global _store
    if _store is None:
        from backend.selection_decision.store_pg import PostgresSelectionDecisionStore
        _store = PostgresSelectionDecisionStore()
    return _store

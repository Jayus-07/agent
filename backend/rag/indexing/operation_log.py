"""DocumentOperationLogger — 文档管理操作审计日志（接口层，PG 实现）。

记录每次文档管理操作（upload / reindex / delete）：
  谁(user_id + source) + 什么时候 + 对哪个文档 + 什么操作 + 关联 trace_id + 结果。

与 trace_collector（内存，重启丢）的区别：
  本表持久化，重启后操作历史仍可查；trace_id 关联是 best-effort，
  近期操作能跳转 trace 详情，老操作/重启后 trace 可能已过期。

2026-09-17 SQLite 轨已删除，唯一实现为 PostgresDocumentOperationLogger
（operation_log_pg.py），工厂在 app/api/routes/_rag_shared.py。
"""

from __future__ import annotations

OPERATIONS = ("upload", "reindex", "delete")


class DocumentOperationLogger:
    """文档操作审计日志接口（唯一实现：PostgresDocumentOperationLogger）。"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.rag.indexing.operation_log_pg import PostgresDocumentOperationLogger
        return super().__new__(PostgresDocumentOperationLogger)

    # ---- 写入 ----

    # ---- 查询 ----

"""Chunk 文本持久化存储 — 用于 Trace 详情页展示完整 Chunk 内容（PG 实现）。

设计：模块级单例。向量库不适合做「按 doc_id 查所有 chunk 文本」这种
OLTP 查询，因此独立建表存储，仅供 trace 页查询，不参与检索链路。
2026-09-17 SQLite 轨已删除，唯一实现为 PostgresChunkStore（chunk_store_pg.py）。
"""
from __future__ import annotations


class ChunkStore:
    """Chunk 文本存储接口（唯一实现：PostgresChunkStore）。"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.rag.indexing.chunk_store_pg import PostgresChunkStore
        return super().__new__(PostgresChunkStore)

    # ── 写入 ──

    # ── 查询 ──

# 模块级单例
_store: ChunkStore | None = None


def get_chunk_store(db_path: str | None = None) -> ChunkStore:
    """存储工厂（2026-09-17 SQLite 轨删除，直连 PG 实现）。db_path 参数保留兼容旧签名。"""
    global _store
    if _store is None:
        from backend.rag.indexing.chunk_store_pg import PostgresChunkStore
        _store = PostgresChunkStore(db_path)
    return _store

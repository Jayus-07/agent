"""DocumentRegistry — 文档元数据注册表（接口层，PG 实现）。

记录每篇已索引文档的路径、SHA256、状态等元数据。
增量索引器依赖此注册表判断文档的新增/修改/删除。

2026-09-17 SQLite 轨已删除，唯一实现为 PostgresDocumentRegistry
（doc_registry_pg.py）。`DocumentRegistry(path)` 仍为唯一入口，
构造时直接返回 PG 实现实例（调用方零改动）。
"""

from __future__ import annotations


# 文档状态枚举（完整生命周期，2026-08-11 加 pending_review）
DOC_STATUSES = ("uploading", "parsing", "embedding", "pending_review", "active", "failed", "deleted")

# 版本治理字段（双后端共用语义；PG 侧见 doc_registry_pg）。
#   document_id → doc_id（已有）    version_id → version_id（新）
#   content_hash → file_hash（已有）status → status（已有）
#   effective_from/effective_to/supersedes_version_id/source_priority/
#   quality_status → 同名列（新）   department/kb_id → 已有
VERSION_GOVERNANCE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (列名, SQLite 类型 + DEFAULT, 说明)
    ("version_id", "TEXT DEFAULT ''", "版本标识（如 v1/v2/v3）"),
    ("effective_from", "TEXT", "生效日期（ISO date）"),
    ("effective_to", "TEXT", "失效日期（NULL/空 = 现行版本）"),
    ("supersedes_version_id", "TEXT DEFAULT ''", "被本版本取代的前版 doc_id"),
    ("source_priority", "INTEGER DEFAULT 0", "来源权威级（越大越权威，冲突裁决用）"),
    ("quality_status", "TEXT DEFAULT 'unknown'", "质量门禁裁决 unknown/pass/soft_warning/failed"),
)


class DocumentRegistry:
    """文档注册表接口（构造直接返回 PostgresDocumentRegistry 实例）。

    用法:
        registry = DocumentRegistry()
        registry.register("/path/to/doc.txt", "abc123", "sha256...", "hr", ["id1","id2"], "did1")
        row = registry.get_by_path("/path/to/doc.txt")
        registry.mark_deleted("/path/to/doc.txt")
    """

    def __new__(cls, db_path: str | None = None):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 参数保留兼容旧签名）。
        from backend.rag.indexing.doc_registry_pg import PostgresDocumentRegistry
        return super().__new__(PostgresDocumentRegistry)

    # ---- 查询 ----

    # ---- 写入 ----

    # ── 文档生命周期（2026-08-11 P2）──

# 增量知识索引（Incremental Knowledge Indexing）— 设计文档

> **版本**: v0.1.0
> **日期**: 2026-07-02
> **状态**: 已完成 — 文档级增量索引

---

## 1. 背景

当前 RAG 入库采用全量重建：对所有文件算一个 MD5 → 任何变更就 `rmtree` → 全量重做（加载 → 分块 → LLM 摘要 → Embedding → 写 Chroma）。对 2000 篇种子文档来说完全不可行。

## 2. 设计目标

1. **文档级增量** — SHA256 检测变更，未变文件跳过
2. **保留现有 API** — Retriever / RAG API 零修改
3. **可回退** — 增量失败自动降级全量重建
4. **可扩展** — 架构预留 chunk 级 diff（后续 PR）

## 3. 架构

```
data/docs/ ──→ IncrementalIndexer.sync()
                  │
                  ├── _scan_disk()     → {path: (sha256, size, mtime)}
                  ├── _compute_delta() → Delta(added, modified, deleted, unchanged)
                  └── _apply_delta()
                       ├── ADDED:    _index_file() → chunk → embed → add → register
                       ├── MODIFIED: _remove_document() → _index_file() → update registry
                       ├── DELETED:  _remove_document() → mark_deleted
                       └── UNCHANGED: 跳过
                              │
                              ▼
                      KnowledgeStore (ChromaDB)
                    (add_documents / add_texts / delete)
                              │
                              ▼
                      DocumentRegistry (SQLite)
                    (path | sha256 | chunk_ids | status)
```

## 4. 核心组件

### 4.1 DocumentRegistry

SQLite 单表，字段：`file_path`(PK), `file_name`, `kb_id`, `doc_id`, `file_hash`, `file_size`, `file_mtime`, `chunk_count`, `chunk_ids`(JSON), `doc_db_id`, `status`(active/deleted), `last_indexed`, `created_at`, `updated_at`。

### 4.2 IncrementalIndexer

- `_sha256(file_path)` — 文件内容哈希
- `_scan_disk()` — 遍历 docs_dir，返回 `{path: (sha256, size, mtime)}`
- `_compute_delta(disk, registry)` — 对比分类
- `_index_file(file_path)` — 单文件加载→分块→metadata→embed→写入
- `_remove_document(doc_id)` — 从 vectordb + doc_db 删除

### 4.3 Pipeline 集成

`_init_vector_dbs_incremental()` — 加载已有库 → 注册表 → indexer.sync()。
失败时回退 `_init_vector_dbs_full()`（全量重建）。

## 5. 回退策略

| 条件 | 行为 |
|---|---|
| `ENABLE_INCREMENTAL_INDEXING=false` | 全量重建 |
| registry 不存在 | 全量重建 + 建 registry |
| 向量库目录不存在 | 全量重建 |
| 增量过程异常 | 清空 registry → 全量重建 |

## 6. 不做什么

- ❌ Chunk 级 diff（本 PR 只做文档级）
- ❌ BM25 持久化
- ❌ RAG API / Retriever 变更

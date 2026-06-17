# Memory System 企业级升级 — 设计文档

**Date**: 2026-06-17
**Status**: Design approved, auto-executing
**Scope**: 8 phases, 14 files, SQLAlchemy Async + PostgreSQL + pgvector

## 目标

将三层记忆系统（L1 Python List / L2 SQLite / L3 ChromaDB）升级为生产级 Agent Memory Service。

## 目录结构

```
memory/
├── __init__.py              # 公开 API: MemoryService 单例 + MemoryManager 兼容层
├── service.py               # MemoryService 统一入口
├── manager.py               # MemoryManager（L1+L2+L3 生命周期编排）
├── short_term.py            # L1 ShortTermBuffer（保留）
├── session.py               # L2 SessionMemory → SessionRepository
├── long_term.py             # L3 LongTermMemory → pgvector only
├── trigger.py               # MemoryWorthinessClassifier
├── importance.py            # ImportanceScorer
├── retriever.py             # HybridRetriever
├── decay.py                 # MemoryDecayService
├── dedup.py                 # 保留，适配新 schema
├── pii_filter.py            # 保留
├── repository/
│   ├── session_repo.py
│   └── memory_repo.py
├── models/
│   ├── session.py           # ChatSession, ChatMessage ORM
│   └── memory.py            # MemoryRecord ORM
└── migrations/
    └── 001_init.sql
```

## 数据库表

- `chat_sessions`: id, session_id (UNIQUE), user_id, summary, created_at, updated_at
- `chat_messages`: id, session_id (FK), role (user/assistant/system), content, created_at
- `memory_records`: id (UUID), user_id, session_id, memory_type, content, embedding (vector(512)), importance_score, confidence_score, access_count, created_at, last_access_at, expire_at, is_active, superseded_by

索引: IVFFlat cosine, user+active partial, memory_type, importance_score DESC, last_access_at DESC

## 核心类

| 类 | 职责 |
|---|---|
| MemoryService | 统一入口 search/store/update/archive，Agent 唯一接入点 |
| MemoryManager | L1+L2+L3 编排，向后兼容旧 API |
| SessionRepository | chat_sessions + chat_messages CRUD (Async) |
| MemoryRepository | memory_records CRUD + pgvector hybrid search (Async) |
| MemoryWorthinessClassifier | 规则优先 → LLM 回退，STORE/IGNORE |
| ImportanceScorer | 5 维评分 (user_fact 1.0 → casual 0.2)，阈值 0.6 |
| HybridRetriever | embedding(50%) + importance(30%) + recency(20%) → top_5 |
| MemoryDecayService | 定时衰减: >90d ×0.95, >180d ×0.9, <0.2 归档 |

## 接口约束

- Agent 禁止直接访问 repository/models
- 所有读写通过 MemoryService
- store() 内部 asyncio.create_task()，不阻塞主流程
- 写入管线: extract → PII → classify → score(≥0.6) → dedup → insert

## 向后兼容

`memory/__init__.py` 保留 `MemoryManager` 类和 `memory_manager` 单例，内部委托给 `MemoryService`，同步包装 asyncio。

## 迁移步骤

1. DDL → 2. pip install asyncpg pgvector → 3. models/ + repository/ → 4. 重构 session.py → 5. 重构 long_term.py → 6. trigger/importance/retriever/decay → 7. service.py → 8. 更新 __init__.py → 9. 更新 config.py → 10. 更新 Agent 调用方

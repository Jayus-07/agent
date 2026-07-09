# 三层记忆系统

> L1 短期（内存） + L2 会话（PG） + L3 长期（pgvector）。统一入口 `MemoryService`。

## 1. 总览

```
memory/
├── service.py              # MemoryService 统一入口（Agent 唯一接入点）
├── manager.py              # MemoryManager 兼容层（sync 包装器，持久事件循环线程）
├── short_term.py           # L1 ShortTermBuffer（环形缓冲区，内存）
├── session.py              # L2 SessionMemory（PG async）
├── long_term.py            # L3 LongTermMemory（pgvector，事实提取+检索）
├── trigger.py              # MemoryWorthinessClassifier（规则+LLM：STORE/IGNORE）
├── importance.py           # ImportanceScorer（5 维评分 0.0-1.0，阈值 0.6）
├── retriever.py            # HybridRetriever（0.5×sim + 0.3×imp + 0.2×recency）
├── decay.py                # MemoryDecayService（>90d ×0.95, >180d ×0.9, <0.2 归档）
├── pii_filter.py           # PII 正则过滤器
├── database.py             # AsyncEngine + AsyncSessionLocal（连接池 20+10）
├── repository/
│   ├── session_repo.py     # chat_sessions + chat_messages CRUD
│   └── memory_repo.py      # memory_records CRUD + pgvector hybrid search
├── models/
│   ├── session.py          # ChatSession, ChatMessage ORM
│   └── memory.py           # MemoryRecord ORM (Vector(512))
└── migrations/
    └── 001_init.sql        # DDL：3 表 + 7 索引
```

## 2. 三层架构

| 层级 | 数据 | 容量 | 用途 | 检索方式 |
|---|---|---|---|---|
| L1 ShortTerm | 当前会话消息 | 环形缓冲 20 条 | 上下文窗口 | 直接读 |
| L2 Session | 历史会话消息 | 全部持久化 | 会话恢复 | `load_messages(session_id)` |
| L3 LongTerm | 事实/偏好/决策/知识 | 长期 | 跨会话记忆 | pgvector hybrid search |

## 3. 统一入口：`MemoryService` 与 `MemoryManager`

`MemoryService`（`service.py`）是**Agent 唯一接入点**，提供完整 async API。`MemoryManager`（`manager.py`）是 sync 包装层，把 `MemoryService` 的 async 方法桥接到 sync 调用方（FastAPI 同步 worker）。

```
MultiAgentSystem.ask() [sync context]
  → memory_manager.start_session(...)  [sync]
    → MemoryManager._run(coro)         [持久后台 loop]
      → MemoryService.start_session(...)  [async]
        → L1 + L2 + L3 协调
```

**约束**：Agent 禁止直接访问 `memory/repository/` 或 `memory/models/`，统一通过 `MemoryService`。

## 4. L3 写入管线（后台异步）

```
LLM Extract Facts → PII Filter → Trigger(STORE/IGNORE)
  → Importance Score(≥0.6) → Vector Dedup → pgvector Write
```

每步详解：

| 步骤 | 文件 | 作用 |
|---|---|---|
| 1. LLM Extract Facts | `long_term.py:LongTermMemory.extract_facts` | 从对话提取 `{type, content}` 事实列表（`user_fact` / `preference` / `decision` / `knowledge`） |
| 2. PII Filter | `pii_filter.py:scan_and_sanitize` | 身份证/手机号/银行卡/邮箱脱敏 |
| 3. Trigger | `trigger.py:MemoryWorthinessClassifier.classify` | 规则+LLM 决定 STORE vs IGNORE |
| 4. Importance Score | `importance.py:ImportanceScorer.score` | 5 维评分（perception / relevance / novelty / confidence / urgency），阈值 0.6 |
| 5. Vector Dedup | (内联于 `long_term.py:store_single`) | 余弦相似度 > `L3_DEDUP_COSINE_THRESHOLD` (0.85) 去重；> `L3_SUPERSEDE_THRESHOLD` (0.92) 替换旧事实 |
| 6. pgvector Write | `repository/memory_repo.py:insert` | 写 `memory_records` 表（Vector(512)） |

## 5. L3 检索：`HybridRetriever`

`memory/retriever.py:HybridRetriever.retrieve(query, k=10)`：

```python
score = 0.5 × semantic_similarity + 0.3 × importance + 0.2 × recency_decay
```

- semantic_similarity: pgvector cosine
- importance: `MemoryRecord.importance_score` (0-1)
- recency_decay: `exp(-age_days / 30)` (30 天半衰期)

**注意**：类名 `HybridRetriever` 易误会为"BM25+vector 混合"，实际只是加权求和。如未来实现真正的混合检索，建议改名为 `WeightedRetriever`。

## 6. 关键类 / 函数

### 6.1 MemoryService (`service.py`)

```python
class MemoryService:
    def __init__(self): ...
    async def start_session(self, session_id) -> ShortTermBuffer: ...
    async def end_turn(self, session_id, question, answer) -> None: ...
    async def search(self, query, k=5) -> list[MemoryFact]: ...
    async def store(self, facts: list[MemoryFact]) -> int: ...
    async def update(self, fact_id, **fields) -> bool: ...
    async def archive(self, fact_id) -> None: ...
    async def run_decay(self) -> dict: ...
```

### 6.2 MemoryManager (`manager.py`)

```python
class MemoryManager:
    """Sync bridge with persistent background event loop.
    
    SQLAlchemy async engine pools are event-loop-bound. Using asyncio.run()
    per call destroys the loop each time, corrupting the pool. Instead we
    keep one dedicated thread with one event loop alive for the process
    lifetime.
    """
    def __init__(self):
        self._service = MemoryService()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._executor.submit(self._init_loop).result(timeout=10)
        atexit.register(self._shutdown)

    def start_session(self, session_id, question) -> ShortTermBuffer: ...
    def end_turn(self, session_id, question, answer) -> None: ...
```

`memory_manager` 是单例（`memory/__init__.py:memory_manager`），Agent 直接 import。

### 6.3 ShortTermBuffer (`short_term.py`)

环形缓冲，容量 20（`SHORT_TERM_MAX_MESSAGES`）。消息进出有 `add` / `add_turn` / `get_recent` / `clear` 方法。

### 6.4 SessionMemory (`session.py`)

L2 会话消息持久化到 PG。提供 `load_messages` / `save_turn` / `summarize` / `needs_summarization` / `message_count`。

### 6.5 LongTermMemory (`long_term.py`)

L3 长期事实存储：
- `extract_facts(text) -> list[MemoryFact]`: LLM 抽取事实
- `_parse_facts(text)`: 解析 LLM 输出（`| ` 分隔）
- `store_single(fact) -> int`: 单条事实的完整写入管线（PII → trigger → score → dedup → write）
- `retrieve(query, k) -> list[MemoryFact]`: hybrid 检索
- `format_for_prompt(facts) -> str`: 注入 LLM prompt 的格式化

### 6.6 MemoryWorthinessClassifier (`trigger.py`)

`classify(text, context) -> bool`：决定事实是否值得存。规则 + LLM 双重判定。

### 6.7 ImportanceScorer (`importance.py`)

`score(fact) -> float`：5 维评分加权后归一化到 0-1，阈值 0.6。

### 6.8 PII Filter (`pii_filter.py`)

`scan_and_sanitize(text) -> str`：正则匹配身份证/手机号/银行卡/邮箱 → 替换为占位符。

`PiiScanResult` 返回 `findings` 列表（每个含 type / position / replacement）。

## 7. 数据库 schema

```sql
-- 3 张主表（来自 migrations/001_init.sql）
chat_sessions    (id, user_id, title, mode, created_at, updated_at)
chat_messages    (id, session_id, role, content, timestamp, ...)
memory_records   (id, content, embedding Vector(512), importance_score,
                  is_active, last_access_at, access_count, ...)
```

7 个索引（含 pgvector `ivfflat cosine` 索引）。

## 8. 关键配置

| 变量 | 默认 | 作用 |
|---|---|---|
| `SHORT_TERM_MAX_MESSAGES` | 20 | L1 容量 |
| `SESSION_MAX_MESSAGES` | 50 | L2 单会话消息数 |
| `ENABLE_LONG_TERM_MEMORY` | true | L3 总开关 |
| `L3_PII_FILTER_ENABLED` | true | PII 脱敏开关 |
| `L3_DEDUP_COSINE_THRESHOLD` | 0.85 | 去重阈值 |
| `L3_SUPERSEDE_THRESHOLD` | 0.92 | 替换旧事实阈值 |
| `MEMORY_ASYNC_POOL_SIZE` | 20 | async engine 连接池 |
| `MEMORY_ASYNC_MAX_OVERFLOW` | 10 | 溢出连接池 |

`OVERALL_REQUEST_TIMEOUT`、`L3_CLEANUP_MIN_DAYS`、`L3_CLEANUP_MIN_ACCESS` 已废弃（保留注释说明）。

## 9. 修改指南

- **加新事实类型**：改 `multi_agent/long_term.py:_FACT_EXTRACTION_PROMPT` 的 `类型` 部分
- **改重要性评分维度**：改 `importance.py:_DIMENSIONS` 和 `score()` 公式
- **改去重阈值**：`.env` 中调整 `L3_DEDUP_COSINE_THRESHOLD`
- **改检索权重**：改 `retriever.py:retrieve` 中的 `0.5 / 0.3 / 0.2` 系数
- **禁用 L3**：`.env` 中 `ENABLE_LONG_TERM_MEMORY=false`
- **手动跑衰减**：
  ```bash
  PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -c "
  import asyncio
  from memory.service import MemoryService
  asyncio.run(MemoryService().run_decay())
  "
  ```

## 10. 已知问题 / 待优化

- `MemoryService` 与 `MemoryManager` 是双层（设计如此），但同步桥增加复杂度。**当前不重构**
- `MemoryManager._run` 在 `asyncio.gather(*pending)` 阶段会**强制等 L3 后台写入完成**（破坏"后台异步"承诺）— P0 候选修复但**暂不动**
- `MemoryDecayService` **无调度器**（无 APScheduler / cron），需要手动调用 `run_decay()`
- `decay.py` 的两次 `apply_decay(180, 0.9)` + `apply_decay(90, 0.95)` 实际是**重叠衰减**（同一记录被乘 0.9 × 0.95 = 0.855）— 已知 bug，**暂不修**
- `MemoryRepository` 抽象层被 `long_term.py` 部分绕过（直接构造 ORM 对象）— 架构问题，**暂不动**
- L1 `ShortTermBuffer` 在生产路径上**不调用 `add()`**，仅通过 `start_session()` 读 — 死路径之一

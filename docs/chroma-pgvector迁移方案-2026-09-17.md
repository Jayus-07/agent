# Chroma → pgvector 迁移方案（落地细化版）

> 日期：2026-09-17　|　状态：待评审
> 定位：对《迁移实施计划-sqlite-pg-chroma-pgvector-2026-09-17.md》§3（里程碑二）的落地细化——SQLite 侧 Batch A~D 已完成，本方案只覆盖向量库切换。
> 前置实测（沿用总计划）：7 个 Chroma 实例 / ~10 collection / ≈1,611 条向量；embedding 双轨 1024 维（text-embedding-v3）+ 512 维（bge-small-zh-v1.5）；pgvector/pgvector:pg16 容器已在运行。

## 一、现状盘点（本次核查）

| 项 | 现状 | 结论 |
|---|---|---|
| 抽象接口 | `backend/rag/vectorstore/knowledge_store.py` 已有 `KnowledgeStore` ABC，注释明确「预留实现: PgVectorKnowledgeStore（后续 PR）」 | **接口已备好，无需重设计** |
| 直接使用点 | `rag/pipeline.py`（3 处）、`seed/importers/knowledge_importer.py`（2 处）、`selection/market_index.py`（间接） | 收口到一个工厂即可 |
| PG 基础设施 | `pgvector/pgvector:pg16` 已在 docker-compose 运行（agent_memory 库）；LangGraph checkpoint 已在用 | 零新增组件 |
| 存量 PG 驱动 | 业务/可观测存储统一 `psycopg2` + `psycopg2.extras` | 新实现跟随 psycopg2 |
| 迁移开关模式 | Batch A~D 惯例：`<STORE>_BACKEND=sqlite\|postgres` 环境变量 + 工厂分发 + 回滚开关 | 完全复用 |
| 检索 filter 形态 | flat dict（`kb_id` / `$or` 等）→ `normalize_where()` 转 Chroma 语法 → store 层 | pgvector 实现需接同样输入，内部翻译 SQL |

## 二、目标设计

### 2.1 配置（backend/config/database.py 增量）

```python
# === 向量库存储引擎开关（迁移计划 2026-09-17 Chroma → pgvector）===
VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "chroma").strip().lower()  # chroma | pgvector
VECTOR_PG_CONFIG = _pg_cfg("VECTOR_PGDATABASE", "agent_memory")  # 与 chunk_store/doc_registry 同库
VECTOR_PG_TABLE_PREFIX = os.getenv("VECTOR_PG_TABLE_PREFIX", "")  # 测试隔离用
```

### 2.2 表设计（agent_memory 库）

沿用总计划 §3.2，按维度分表（1024 / 512 各一张，避免列约束打架）：

```sql
CREATE TABLE IF NOT EXISTS rag_vectors (
    id           TEXT PRIMARY KEY,              -- 确定性 ID：{collection}:{doc_id}:{chunk_idx}
    collection   TEXT NOT NULL,
    doc_id       TEXT,
    content      TEXT,
    metadata     JSONB NOT NULL DEFAULT '{}',
    embedding    vector(1024) NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rag_vectors_hnsw  ON rag_vectors USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
CREATE INDEX IF NOT EXISTS idx_rag_vectors_scope ON rag_vectors (collection, doc_id);
CREATE INDEX IF NOT EXISTS idx_rag_vectors_meta  ON rag_vectors USING gin (metadata);
-- rag_vectors_bge 表结构同构，embedding 为 vector(512)——仅当确认 bge 本地轨仍在用时建（见前置确认 2）
```

要点：
- **建表由 store 首次连接时幂等执行**（`CREATE TABLE IF NOT EXISTS`），与 LangGraph PostgresSaver.setup 同风格，无需独立迁移框架。
- 1,611 条规模 HNSW 参数无需调优；< 5 万条全表扫描也在 10ms 级。

### 2.3 新实现 `backend/rag/vectorstore/pgvector_store.py`

`PgVectorKnowledgeStore(KnowledgeStore)`，逐方法对齐现有接口：

| 方法 | 实现要点 |
|---|---|
| `from_documents` / `from_texts` | 遍历调用 `add_documents` / `add_texts`（先嵌入后批量 INSERT，保留预嵌入失败预检语义） |
| `add_documents(embeddings=...)` | 复用预计算向量，`INSERT ... ON CONFLICT (id) DO UPDATE`（**天然幂等**，优于 Chroma 的追加语义） |
| `similarity_search_with_score` | `WHERE collection=%s [AND 过滤] ORDER BY embedding <=> %s LIMIT k`；pgvector cosine 距离 = 1 − cos_sim，**与 Chroma cosine 距离量纲一致**，下游分数消费方无需改动 |
| `similarity_search` | 上述方法的去分数包装 |
| `get(where)` | SELECT id/metadata/content → `{ids, metadatas, documents}` 三键结构与 Chroma 对齐 |
| `delete(ids/where)` | `DELETE ... RETURNING`，**返回精确删除数**（Chroma 的 where 删除只能返回 0，此处是行为增强，下游无依赖风险） |
| `update_metadata_where` | `UPDATE ... SET metadata = metadata \|\| %s WHERE ...`，RETURNING 计数 |

**where → SQL 翻译器**（模块内纯函数，可独立单测）：
- flat dict 多键 → `AND` 组合；`$and` / `$or` → 递归括号组
- 标量值 → `metadata @> '{"kb_id": "x"}'`（JSONB GIN 索引命中）
- `$in` → `metadata->>'k' = ANY(%s)`；`$eq/$ne/$gt/$gte/$lt/$lte` → 参数化比较；`$contains` → `content ILIKE '%...%'`
- 翻译器输入即现有 `normalize_where()` 的输出形态，**业务层零改动**

**连接管理**：仿照 `rag/indexing/chunk_store_pg.py` 的 psycopg2 连接风格 + `DB_POOL_*` 参数；每方法短连接/池化取一，事务即取即还。

### 2.4 工厂收口（唯一切换面）

新增 `backend/rag/vectorstore/factory.py`：

```python
def create_knowledge_store(persist_directory, embedding, embedding_metadata=None, backend=None):
    backend = backend or VECTOR_BACKEND
    if backend == "pgvector":
        return PgVectorKnowledgeStore(persist_directory, embedding)
    return ChromaKnowledgeStore.from_*(...)   # 现行为不变
```

改动点（共 6 处调用，全部改调工厂，`persist_directory` 语义映射为 `collection` 名）：
1. `rag/pipeline.py` L181（from_documents）、L188（from_texts）、L260（加载）
2. `seed/importers/knowledge_importer.py` L81、L87
3. `selection/market_index.py`（独立小库，走同一工厂）

默认 `VECTOR_BACKEND=chroma` → **合入后行为零变化**，切换只动一个环境变量。

### 2.5 迁移脚本（一次性工具，进 git）

1. `backend/scripts/export_chroma.py`：逐 collection `get(include=['embeddings','metadatas','documents'])` → JSONL 落盘 `data/_migration/`（既是中间产物也是校验基准）。
2. `backend/scripts/import_pgvector.py`：读 JSONL → 确定性 ID 重写 → 分维度入对应表 → 末尾逐 collection **条数对账，差值非零 exit 1**。全程幂等可重跑。

## 三、实施顺序（预计 1~1.5 天）

| # | 事项 | 产出 |
|---|---|---|
| 1 | 前置确认（见 §五） | 僵尸 collection 处置决定、bge 轨去留 |
| 2 | 表 + PgVectorKnowledgeStore + where 翻译器 + 单测（翻译器全分支 + 双 backend 参数化跑 test_knowledge_store.py） | pgvector_store.py 可用 |
| 3 | export/import 脚本 + 对账 | 数据落库对账 = 0 |
| 4 | 工厂收口 + 6 处调用改造（此 commit 默认 chroma，行为零变化） | 切换面就绪 |
| 5 | 抽样校验：50 条同 query 双跑 Chroma vs pgvector，recall@5 对比 ≥ 0.95 | 校验记录 |
| 6 | `VECTOR_BACKEND=pgvector` 切换 + 评测回归 | 指标不降证明 |
| 7 | 验收后 Chroma 目录 tar 归档至 `data/_migrated_20260917/` | 归档包 |

> 协作纪律：开工前确认无其他会话在跑全量 pytest / 重建索引；改动单独成 commit，与工作区现有未提交改动（e2e.py / economics.py / 前端若干）严格隔离。

## 四、验收与回滚

**验收标准**：
- [ ] 逐 collection 条数对账 = 0
- [ ] 抽样 recall@5 ≥ 0.95，无维度/度量错配报错
- [ ] `VECTOR_BACKEND=pgvector` 下 RAG 36 例评测 ≥ 真实基线（Recall@5 0.9394 / MRR 0.9242 / Top-1 0.9667 / 36 通过）
- [ ] 过滤检索用例（每个 collection ≥ 1 条带 metadata filter）结果与 Chroma 一致
- [ ] init-dbs.sh / docker-compose / .env.example 同步，新人一键可复现

**回滚**：`VECTOR_BACKEND=chroma` + revert 配置；Chroma 归档包保留至下个迭代结束。默认值即 chroma，合并当天零风险。

## 五、前置确认（开工前需拍板，沿用总计划 §6）

1. **3 个疑似僵尸 collection**：chunk_db(0 条)、chroma_market(2 条)、long_term_memory(31 条)——删 / 归档 / 迁？
2. **bge-small-zh-v1.5 本地嵌入轨是否仍在使用**——决定是否建第二张 512 维表（不用则只建 rag_vectors 一张）。
3. **迁移窗口锁定 EMBEDDING_PROVIDER / EMBEDDING_MODEL**（.env 既有规则，重申）。

## 六、明确不做（同总计划 §5）

Milvus/Qdrant、向量分片与副本、增量 embedding 同步管道（1.6k 条全量重建成本可忽略）、分区表（数据到百万级再议）。

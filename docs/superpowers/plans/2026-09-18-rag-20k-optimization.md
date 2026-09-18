# RAG 2 万份文档检索与入库优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提升 2 万份文档规模下的候选召回率、BM25 范围过滤正确性、PGVector 查询吞吐，并修正一致性清扫对旧 SQLite 的依赖。

**Architecture:** 保留现有“文档级候选 → 片段混合检索 → 父片段扩展 → Rerank → Evidence Gate”链路。通过可配置候选池、BM25 过采样后过滤、PG 连接池与 HNSW `ef_search` 提升规模化稳定性；不在本轮引入分表或替换 BM25 后端，避免改变权限和结果协议。

**Tech Stack:** Python 3、FastAPI、LangChain、PostgreSQL + pgvector、psycopg2、pytest。

**Spec:** `docs/2026-09-18-全站存储收口交接报告.md` 与项目根目录 `AGENTS.md` 中的 RAG 设计和验证约束。

## Global Constraints

- 所有新增配置必须从 `backend/config/rag.py` 暴露，并在 `backend/config/__init__.py` re-export。
- 所有代码备注使用中文，说明原因、默认值、性能/召回权衡和兼容性。
- 不改动当前工作区中与 RAG 无关的客服、前端、启动脚本和学习记录变更。
- 不删除现有 PGVector 表数据；DDL 必须幂等，查询参数必须参数化。
- 局部 pytest 必须带 `--no-cov`，避免项目级覆盖率阈值干扰定向回归。

---

### Task 1: 候选池与 BM25 过采样配置

**Files:**
- Modify: `backend/config/rag.py`
- Modify: `backend/config/__init__.py`
- Modify: `backend/rag/retrieval/retrievers.py`
- Modify: `backend/rag/retrieval/bm25_store.py`
- Modify: `backend/rag/pipeline.py`
- Modify: `backend/rag/retrieval/hybrid.py`
- Test: `backend/tests/rag/test_retrieval_scale_config.py`

**Interfaces:**
- Produces `RAG_DOC_CANDIDATE_K`（默认 50）和 `BM25_CANDIDATE_K`（默认 100）。
- 文档级检索继续以最终 `k` 返回，但 Stage 1 通过 `RAG_DOC_CANDIDATE_K` 取得候选。
- BM25 retriever 以 `BM25_CANDIDATE_K` 返回候选，混合检索过滤后再截取最终数量。

- [ ] **Step 1: Write the failing tests**

```python
def test_doc_stage_uses_configured_candidate_k(monkeypatch):
    monkeypatch.setattr(retrievers, "RAG_DOC_CANDIDATE_K", 50)
    doc_db = FakeDocDb()
    retriever = make_retriever(doc_db=doc_db)
    retriever._authorized_doc_search(Staging("问题"))
    assert doc_db.calls[-1]["k"] == 50


def test_bm25_candidate_k_is_larger_than_final_k(monkeypatch):
    monkeypatch.setattr(bm25_store, "BM25_CANDIDATE_K", 100)
    store = BM25Store(tmp_path / "bm25")
    retriever = store.build([Document(page_content="甲", metadata={})])
    assert retriever.k == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_retrieval_scale_config.py -q --no-cov`

Expected: FAIL because the current Stage 1 still uses hardcoded `15`, and BM25 defaults to `BM25_SEARCH_K`。

- [ ] **Step 3: Write the minimal implementation**

在 `rag.py` 增加带中文备注的两个配置项；在 `config/__init__.py` re-export。将 `ChunkLevelRetriever._authorized_doc_search` 的默认值改为配置项，并将 pipeline 的 BM25 load/build/refresh/remove/replace 调用统一改用 `BM25_CANDIDATE_K`。在 hybrid 路径中保留过滤顺序：先按 `doc_ids` 与 metadata 过滤，再执行最终 `k` 截断；vector-only 的 BM25 fallback 同样遵循该顺序。加入候选数量 trace metric，便于观察候选池是否不足。

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_retrieval_scale_config.py backend/tests/rag/test_bm25_stale_fix.py -q --no-cov`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/config/rag.py backend/config/__init__.py backend/rag/retrieval/retrievers.py backend/rag/retrieval/bm25_store.py backend/rag/pipeline.py backend/rag/retrieval/hybrid.py backend/tests/rag/test_retrieval_scale_config.py
git commit -m "perf: enlarge rag candidate pools safely"
```

### Task 2: 启用人名倒排索引并保持请求隔离

**Files:**
- Modify: `backend/rag/pipeline.py`
- Test: `backend/tests/rag/test_person_index_initialization.py`

**Interfaces:**
- `RAGPipeline._build_person_index()` 在 retriever 初始化阶段构建一次进程内索引。
- `RAGChain` 和 `ChunkLevelRetriever` 继续共享同一 dict，不改变请求上下文协议。

- [ ] **Step 1: Write the failing test**

```python
def test_retriever_initialization_builds_person_index(monkeypatch):
    pipeline = object.__new__(RAGPipeline)
    pipeline._person_to_doc_cache = {"张三": ["doc-1"]}
    monkeypatch.setattr(pipeline, "_build_person_index", lambda: {"张三": ["doc-1"]})
    pipeline._init_retrievers()
    assert pipeline.person_index == {"张三": ["doc-1"]}
    assert pipeline.lc_chain.person_index is pipeline.person_index
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_person_index_initialization.py -q --no-cov`

Expected: FAIL because `_init_retrievers()` currently assigns an empty dict and never invokes `_build_person_index()`。

- [ ] **Step 3: Write minimal implementation**

在 `_init_retrievers()` 中调用现有 `_build_person_index()`；构建失败时记录中文 warning 并使用空索引，保证冷启动不因非关键倒排索引不可用而失败。保留“人名索引只作为候选缩小，最终结果仍走授权和 Evidence Gate”的备注。

- [ ] **Step 4: Run test to verify it passes**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_person_index_initialization.py backend/tests/test_retrieval_request_cache.py -q --no-cov`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/rag/pipeline.py backend/tests/rag/test_person_index_initialization.py
git commit -m "perf: initialize rag person inverted index"
```

### Task 3: PGVector 连接池与 HNSW 查询召回参数

**Files:**
- Modify: `backend/config/rag.py`
- Modify: `backend/config/__init__.py`
- Modify: `backend/rag/vectorstore/pgvector_store.py`
- Test: `backend/tests/rag/test_pgvector_store.py`

**Interfaces:**
- 新增 `VECTOR_PG_POOL_MIN`、`VECTOR_PG_POOL_MAX`、`VECTOR_HNSW_EF_SEARCH` 配置。
- `PgVectorKnowledgeStore._conn()` 保持 context manager 签名，内部从进程级池借还连接。
- 每次相似度查询在同一事务内执行 `SET LOCAL hnsw.ef_search`，不会污染连接池中的后续请求。

- [ ] **Step 1: Write the failing tests**

```python
def test_similarity_search_sets_hnsw_ef_search(fake_store, monkeypatch):
    monkeypatch.setattr(pgvector_store, "VECTOR_HNSW_EF_SEARCH", 80)
    fake_store.similarity_search_with_score("问题", k=5)
    assert any("SET LOCAL hnsw.ef_search" in sql for sql in fake_store.cursor.executed_sql)


def test_connections_are_returned_to_pool(fake_store):
    with fake_store._conn() as conn:
        assert conn is not None
    assert fake_store.pool.putconn_called == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_pgvector_store.py -q --no-cov`

Expected: FAIL because the current implementation calls `psycopg2.connect()` and closes every connection, and does not set `hnsw.ef_search`。

- [ ] **Step 3: Write minimal implementation**

使用 `psycopg2.pool.ThreadedConnectionPool` 做按配置 key 复用的进程级连接池；初始化失败时保持明确异常，不静默切回短连接。借出的连接在 context manager 退出时 rollback 清理事务状态后 `putconn()`，异常时 discard 坏连接。DDL 和写操作继续使用现有锁。相似度查询执行参数化的 `SET LOCAL hnsw.ef_search = %s`，并在代码备注中说明 `ef_search` 提高过滤场景召回、会增加 CPU/延迟，默认值可通过环境变量调节。

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_pgvector_store.py backend/tests/rag/test_pipeline_rebuild_pg.py -q --no-cov`

Expected: PASS；若本机 PG 不可用，只允许使用已有 fake/monkeypatch 单元测试验证，不把连接失败误判为代码通过。

- [ ] **Step 5: Commit**

```bash
git add backend/config/rag.py backend/config/__init__.py backend/rag/vectorstore/pgvector_store.py backend/tests/rag/test_pgvector_store.py
git commit -m "perf: pool pgvector connections and tune hnsw search"
```

### Task 4: 一致性检查迁移到 PostgreSQL chunk store

**Files:**
- Modify: `backend/rag/indexing/chunk_store_pg.py`
- Modify: `backend/rag/indexing/consistency.py`
- Test: `backend/tests/rag/test_index_consistency.py`

**Interfaces:**
- `PostgresChunkStore.list_doc_ids() -> set[str]` 返回当前表中去重后的 doc_id。
- `IndexConsistencyChecker._check_chunk_store()` 只依赖 `get_chunk_store().list_doc_ids()`，不再读取 `CHUNK_STORE_PATH` 或导入 sqlite3。

- [ ] **Step 1: Write the failing tests**

```python
def test_chunk_store_check_uses_postgres_interface(monkeypatch):
    fake_store = FakeChunkStore(doc_ids={"doc-active", "doc-orphan"})
    monkeypatch.setattr(chunk_store, "get_chunk_store", lambda: fake_store)
    checker = make_checker(active_ids={"doc-active"})
    report = ConsistencyReport()
    checker._check_chunk_store(report)
    assert {i.doc_id for i in report.issues} == {"doc-orphan"}


def test_postgres_chunk_store_exposes_distinct_doc_ids():
    store = make_fake_pg_store(rows=[("doc-a",), ("doc-a",), ("doc-b",)])
    assert store.list_doc_ids() == {"doc-a", "doc-b"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_index_consistency.py -q --no-cov`

Expected: FAIL because `PostgresChunkStore` lacks `list_doc_ids()` and consistency checker仍打开旧 SQLite 路径。

- [ ] **Step 3: Write minimal implementation**

在 PG store 增加带索引友好 SQL 的 `SELECT DISTINCT doc_id` 方法；一致性检查器使用该接口并将 issue 文案、类型注释中的 Chroma/SQLite 残留改成“向量库/PG”。无论读取失败还是 PG 暂不可用，都按现有策略记录 warning，不执行可能破坏数据的修复。

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:/Python/python.exe -m pytest backend/tests/rag/test_index_consistency.py backend/tests/test_upload_resilience.py -q --no-cov`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/rag/indexing/chunk_store_pg.py backend/rag/indexing/consistency.py backend/tests/rag/test_index_consistency.py
git commit -m "fix: audit postgres chunk store consistently"
```

### Task 5: 集成验证与上线备注

**Files:**
- Modify: `docs/RAG_DESIGN.md`
- Create: `docs/RAG-20k-上线检查清单.md`

- [ ] **Step 1: Run focused verification**

Run: `D:/Python/python.exe -m pytest backend/tests/rag backend/tests/test_retrieval_request_cache.py backend/tests/test_indexer_bm25_sync.py -q --no-cov`

Expected: 相关 RAG 测试无失败；外部服务依赖失败必须明确区分为环境阻断。

- [ ] **Step 2: Run syntax verification**

Run: `D:/Python/python.exe -m compileall -q backend/rag backend/config`

Expected: exit code 0。

- [ ] **Step 3: Document the deployment knobs**

在设计文档中更新 PGVector/BM25 实际架构；在上线清单中写明建议初始值：`RAG_DOC_CANDIDATE_K=50`、`BM25_CANDIDATE_K=100`、`VECTOR_HNSW_EF_SEARCH=80`，并要求用真实问答集验收 Recall@5、MRR、Top-1、P95 延迟和索引内存。备注清楚：没有真实 2 万份文档和标注问答集时，不能承诺具体检索成功率。

- [ ] **Step 4: Review scope before completion**

Run: `git diff --stat; git status --short`

Expected: 仅出现本计划涉及的 RAG 文件和新增计划/清单，已有无关改动保持原样。

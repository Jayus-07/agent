# 迁移实施计划：SQLite → PostgreSQL · Chroma → pgvector

> 日期：2026-09-17　|　状态：待评审　|　前置结论：不上 Milvus（依据见下）
>
> 实测规模（2026-09-17 扫描，非估算）：
> - SQLite：`data/` + `backend/data/` 共 **18 个 .db**，合计 ~15MB，总行数 < 1.5 万（最大 analytics.db 9281 行）
> - Chroma：**7 个实例目录 / ~10 个 collection，向量总数 ≈ 1,611 条**（chroma 1296 / cs_router 132 / doc_db 98 / 其余 <100）
> - Embedding 双轨：DashScope `text-embedding-v3`（1024 维）+ 本地 `BAAI/bge-small-zh-v1.5`（512 维）
> - **docker-compose 已在运行 `pgvector/pgvector:pg16`**（agent_memory 库，承载 LangGraph checkpoint + 记忆库）
> - 开发环境数据可丢弃（用户拍板）→ 迁移以"可重跑脚本"为主，不建复杂回滚

---

## 0. 为什么不上 Milvus（一票否决依据）

| Milvus 触发条件 | 当前实测 | 结论 |
|---|---|---|
| 向量 > 500 万 | 1,611 条 | ❌ 差 3 个数量级 |
| QPS > 200 / P99 < 50ms | 内部平台，未见压测（待补） | ❌ |
| 多租户物理隔离 / 分布式扩展 / GPU 索引 | 单机 Docker，团队无专职运维 | ❌ |

Milvus standalone 也要拉 etcd + MinIO，运维面净增两个组件，换不来任何收益。
pgvector 与已在跑的 PG 16 同实例，事务 / 备份 / 权限 / 只读账号（agent_readonly）全部复用。

---

## 1. 目标终态

```
现状：18 个 SQLite + 7 个 Chroma 实例目录（分散、无统一备份、无权限控制）
目标：1 个 PG 实例（已有）
      ├─ schema rag       ：文档注册 / chunk / 关键词规则
      ├─ schema business  ：选品 / 竞对 / 市场调研 / 库存 / demo
      ├─ schema ops       ：工作流运行 / 日报 / 评测历史 / 反馈 / 操作日志
      └─ rag_vectors 表   ：pgvector，替代全部 Chroma collection
```

---

## 2. 里程碑一：SQLite → PostgreSQL（预计 1 天）

### 2.1 库归并映射表

| 源库（data/） | 目标 schema | 备注 |
|---|---|---|
| doc_registry.db（55 行）| rag | **先迁**，chunk→doc 父子关系的权威 |
| chunk_store.db | rag | 与 chunk_db 目录元数据合并核对 |
| keyword_rules.db（361 行）| rag | |
| analytics.db（9281 行）| business | 最大库 |
| selection.db / selection_decision.db / competitor.db / demo_sales.db / market_research.db / inventory_alerts.db | business | |
| workflow_runs.db（894 行）| ops | |
| daily_reports.db（1101 行）| ops | |
| eval_history.db / feedback.db / doc_operation_log.db | ops | |
| backend/data/ 下 10 个同名库 | **不迁** | 疑为旧副本（合计 6.5MB），迁移前与用户确认后归档删除 |
| _backup / _quarantine / *.bak | 不迁 | 归档到迁移备份包 |

### 2.2 类型映射与 SQL 方言注意点

| SQLite | PostgreSQL | 说明 |
|---|---|---|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGINT GENERATED ALWAYS AS IDENTITY` | 自增语义等价；开发库数据可丢，无需保序 |
| TEXT 存布尔（'0'/'1'）| `BOOLEAN` | 导入脚本里转换 |
| TEXT 存 ISO 日期 | `TIMESTAMPTZ` | 统一时区 |
| JSON 文本列 | `JSONB` | 支持索引与过滤 |
| `PRAGMA` / `ATTACH` | 无对应 | 应用层若用到需改写 |
| 引号语义（"" 当标识符）| 全部改双引号或小写不加引号 | 建表脚本统一小写 snake_case |

### 2.3 步骤

1. **建 schema 与账号**：`CREATE SCHEMA rag/business/ops;`，沿用 agent_readonly 只读账号并授予各 schema SELECT（docker/init-dbs.sh 增量修改，保持幂等）。
2. **写导出脚本** `scripts/migrate_sqlite_to_pg.py`（一次性工具，进 git）：
   - 输入：库文件 → 目标 schema 的映射表（硬编码在脚本顶部，可评审）
   - 逐表 `SELECT *` → 分批 `COPY`/execute_values 入 PG
   - 类型转换集中在一个 `convert_row()` 函数
   - 幂等：`TRUNCATE ... RESTART IDENTITY CASCADE` 后重导
3. **行数对账**：脚本最后自动打印 源行数 vs 目标行数 vs 差值，差值非零即 exit 1。
4. **应用切换**：`backend/config` 中各 DB 路径改为 PG 连接串（按 schema 分发），留一个迭代期的 `DATA_BACKEND=sqlite|pg` 开关。
5. **回归**：跑现有 pytest 冒烟子集（注意并发会话纪律：全量 pytest 约 20 分钟，避开其他会话跑测窗口）。

### 2.4 验收标准

- [ ] 对账脚本全部库差值 = 0
- [ ] `DATA_BACKEND=pg` 下核心接口（RAG 检索 / 工作流 / 日报）冒烟通过
- [ ] agent_readonly 账号对业务表写入被 PG 层拒绝（只读防线生效）

### 2.5 回滚方案

开发数据可丢 → 回滚 = 环境变量切回 `DATA_BACKEND=sqlite` + revert 配置改动。源 SQLite 文件在 M2 验收前不删除，只移入 `data/_migrated_20260917/` 归档目录。

---

## 3. 里程碑二：Chroma → pgvector（预计 1~1.5 天）

### 3.1 隐式契约盘点（迁移前必须逐项确认）

| 契约 | 现状 | 迁移动作 |
|---|---|---|
| Embedding 模型 | text-embedding-v3（1024 维，主力）+ bge-small-zh-v1.5（512 维，local）| 迁移期间**锁定 provider，禁止切换**（.env 既有规则） |
| 向量维度 | 1024 / 512 两种 | 分表或加 `dim` 区分列 → 采用**分表**（见 3.2） |
| 距离度量 | Chroma 默认 cosine（需脚本读 collection metadata 确认）| pgvector 用 `vector_cosine_ops`，查询 `ORDER BY embedding <=> $1` |
| ID 规则 | Chroma 自动 UUID | 改**确定性 ID** `{collection}:{doc_id}:{chunk_idx}`，重跑幂等 |
| metadata 过滤 | Chroma `where` 语法（`$eq/$in/$contains`）| 改 SQL WHERE + JSONB `@>`；`$contains` 全文语义改 `ILIKE` 或 tsvector |
| 父子关系 | chunk → doc_registry.doc_id | 已有权威表，外键补上 |
| collection 结构 | 7 实例 10 collection | 单表 + `collection` 列；**僵尸先清理**：chunk_db(0 条)、chroma_market(2 条)、long_term_memory(31 条) 疑似废弃，迁移前与用户确认 |

### 3.2 表设计

```sql
CREATE TABLE rag.vectors_v3 (
    id           TEXT PRIMARY KEY,                -- {collection}:{doc_id}:{chunk_idx}
    collection   TEXT NOT NULL,
    doc_id       TEXT,
    content      TEXT,
    metadata     JSONB NOT NULL DEFAULT '{}',
    embedding    vector(1024) NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON rag.vectors_v3
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX ON rag.vectors_v3 (collection, doc_id);
CREATE INDEX ON rag.vectors_v3 USING gin (metadata);
-- 若 bge-small-zh-v1.5 本地轨仍启用，再建 rag.vectors_bge (vector(512))，同构
-- 混合检索过滤示例：WHERE collection=$1 AND metadata @> '{"lang":"zh"}' ORDER BY embedding <=> $2 LIMIT 5
```

1,611 条规模 HNSW 参数不必调优，默认即可；< 5 万条甚至全表扫描都在 10ms 内。

### 3.3 步骤

1. **清理确认**：与用户确认 3 个疑似僵尸 collection 处置（删/归档/迁）。
2. **导出** `scripts/export_chroma.py`：`collection.get(include=['embeddings','metadatas','documents'])` → JSONL 落盘 `data/_migration/`（既是迁移中间产物也是校验基准）。
3. **导入** `scripts/import_pgvector.py`：读 JSONL → 确定性 ID 重写 → 入 `rag.vectors_v3` → 建 HNSW 索引。
4. **数据校验**：
   - 条数对账（逐 collection）
   - 抽样 50 条：同 query 分别打 Chroma 与 pgvector，recall@5 对比 ≥ 0.95
   - 过滤查询：每个 collection 至少 1 条带 metadata 过滤的用例结果一致
5. **API 层切换**：RAG 检索/写入改走 pgvector（rag-service 同容器内改），`VECTOR_BACKEND=chroma|pgvector` 开关。
6. **回归评测**：跑 `data/eval_runs` 现有评测框架，指标不降。
7. **清理**：验收通过后，7 个 Chroma 目录 tar 打包移入 `data/_migrated_20260917/`。

### 3.4 验收标准

- [ ] 逐 collection 条数对账 = 0 差值
- [ ] 抽样 recall@5 ≥ 0.95，无维度/度量错配报错
- [ ] `VECTOR_BACKEND=pgvector` 下评测指标 ≥ Chroma 基线
- [ ] API Key、DB 连接串等敏感值全部走 .env，无新增明文

### 3.5 回滚方案

环境变量切回 `VECTOR_BACKEND=chroma` + revert。tar 归档保留到下个迭代结束。

---

## 4. 检查清单

### 迁移前
- [ ] 用户确认：backend/data/ 旧副本与 3 个僵尸 Chroma collection 的处置
- [ ] 锁定 EMBEDDING_PROVIDER / EMBEDDING_MODEL（迁移窗口内禁切）
- [ ] 确认无其他会话正在跑全量 pytest / 重建索引（协作纪律）
- [ ] git 提交当前工作区或确认改动范围，迁移改动与业务改动分开提交

### 迁移中
- [ ] 一切通过幂等脚本执行，禁止手工 psql 改数据
- [ ] 每步落对账数字，失败即停
- [ ] 不删除任何源文件，只归档

### 迁移后
- [ ] 评测回归通过
- [ ] init-dbs.sh / docker-compose 与新 schema 同步（新人一键可复现）
- [ ] `docs/` 补一页"数据存储现状"（替代本计划的临时性）
- [ ] 归档包验证可解压后，再等一个迭代后清理

## 5. 明确放到二期 / 不做

1. Milvus / Qdrant 等独立向量库 —— 触发条件见 §0，当前零命中
2. 向量分片、副本、多租户隔离
3. 增量 embedding 同步管道 —— 全量重建成本可忽略（1.6k 条）
4. pgvector 分区表 —— 数据到百万级再考虑
5. 历史库表结构重构 —— 只平移不改表设计，避免迁移+重构双重风险

## 6. 待用户确认 / 补充

1. 真实 QPS 与延迟要求（唯一可能改变选型的输入）
2. backend/data/ 10 个旧库是否可直接归档不迁
3. chunk_db / chroma_market / long_term_memory 三个 collection 的去留
4. bge-small-zh-v1.5 本地嵌入轨是否还在使用（决定是否建第二张向量表）

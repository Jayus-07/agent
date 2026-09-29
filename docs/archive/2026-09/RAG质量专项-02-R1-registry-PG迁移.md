# RAG 质量专项 · R1 registry SQLite→PostgreSQL 验收报告（2026-09-17）

> 对应任务：附录二 **R1**（＝C19，用户已拍板）。
> 基线 HEAD：`4114c82`（开工前 `git status` 干净、无并发 pytest）。
> R0 审计依据：`docs/RAG质量专项-01-审计报告.md` §1.3（调用面）/ §三 R-P1-4。

---

## 一、交付物（4 件）

### ① PG 建表脚本（走现有 PG 体系）
`backend/sql/migrations/010_doc_registry_pg.sql`
- 目标库：`agent_memory`（Agent 自身元数据库，与 chat/memory 表同层；**不进** agent_business）。
- 30 列与 SQLite 版 schema 逐列对齐 + `expire_at` 直接内置（SQLite 侧由 `ensure_expire_at_column` 惰性补列）。
- 时间列保持 **TEXT**，写入 `to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')`——与 SQLite `datetime('now')` 文本格式及排序语义一致，迁移零类型转换。
- 应用侧同步幂等建表（`PostgresDocumentRegistry._init_db`，语句与脚本一致），容器 init-dbs 挂载路径与手动 psql 两条路都通。
- **R4 依赖说明（表结构变更）**：R4 版本治理 11 字段（version_id/effective_from/supersedes_version_id/source_priority/quality_status 等）将基于本 PG 表做**增量迁移脚本**（本文件不预建，任务书 §12.6）；届时 SQLite 后端同样需要补列迁移，R4 迁移脚本需双后端兼容或明确只保 PG。

### ② 连接层替换（对外接口与调用方零改动）
- 新增 `backend/rag/indexing/doc_registry_pg.py::PostgresDocumentRegistry`——`DocumentRegistry` 的**子类**，逐方法实现全部 26 个公开方法（同签名/同返回结构）。
- `doc_registry.py::DocumentRegistry.__new__` 按 `DOC_REGISTRY_BACKEND` 分发：`postgres` → PG 子类实例；其余/未设置 → 原 SQLite 实现。`isinstance` 兼容、测试 patch 路径不变，**7 处调用方（server/_rag_shared/hybrid/pipeline/consistency/doc_id_resolver/cleanup 脚本）零改动**。
- 方言映射：`?`→`%s`；`INSERT OR REPLACE`→`ON CONFLICT (file_path) DO UPDATE`；`LIKE`→`ILIKE`（对齐 SQLite ASCII 大小写不敏感）；`PRAGMA`→`information_schema`。
- PG 每次操作独立短连接（commit/rollback/close），防连接泄漏打满 max_connections。

### ③ 历史数据一次性迁移脚本
`backend/scripts/migrate_doc_registry_to_pg.py`
- 全量批量 upsert（`execute_values`，含 `created_at` 全列拷贝保留历史时间戳），幂等可重跑；`--truncate` 支持覆盖式重迁。
- 对账三层：行数（总数 + 分状态）+ `file_hash`（内容 SHA256）抽样逐行比对（含 chunk_count/doc_id/status）。
- **实跑结果（2026-09-17 03:32，源 `data/doc_registry.db` → `agent_memory.doc_registry`）**：

```
迁移完成: 28 行
[reconcile] 行数: sqlite=28 pg=28 ✅
[reconcile] 分状态: sqlite={'active': 24, 'pending_review': 4}  pg 完全一致 ✅
[reconcile] file_hash 抽样对账: 20/20 一致 ✅
对账结果: ✅ PASS（exit 0）
```

### ④ 回滚开关
- `DOC_REGISTRY_BACKEND`（`backend/config/database.py`）：默认 **`sqlite`**（零行为变化）；`postgres` 切换。切回即回滚，无需改代码/迁数据（SQLite 文件保持不动，双写期数据以后写入方为准——见 §四.3）。
- 配套：`DOC_REGISTRY_PGDATABASE`（默认 agent_memory，**不跟随** `PGDATABASE`——本地 .env 把它指到 demo）、`DOC_REGISTRY_PG_TABLE`（测试隔离用）。
- 启用方式：`.env` 加 `DOC_REGISTRY_BACKEND=postgres` 即可（psycopg2 镜像内已有，`business_report`/`sql.executor` 在用，零新依赖）。

## 二、旁路调用方接管（审计增量发现）

| 位置 | 原状 | 处置 |
|---|---|---|
| `observability/metrics.py::update_metadata_coverage` | 硬编码相对路径 `data/doc_registry.db` 裸 sqlite 读（**绕过 registry，且带 CWD 依赖 bug**） | 改走 `DocumentRegistry(DOC_REGISTRY_PATH).list_active()`，判定逻辑逐条等价 |
| `scripts/reset_rag_index.py` | 无条件清 sqlite 文件 | PG 模式下改清 `agent_memory.doc_registry` 表；sqlite 模式行为不变 |
| `scripts/cleanup_tmpnl_residuals.py` | 走 `DocumentRegistry()` 构造 | 无需改动（自动跟随开关分发） |

## 三、验收结果（3/3 通过）

### 1. 并发批量入库无锁冲突 ✅
`backend/tests/rag/test_doc_registry_pg.py::TestConcurrentWrites`（spawn 多进程，真实 PG）：
4 进程并发 ×（25 独占行 + 10 轮**同行 hot-row 争写 upsert**）→ 全部成功、计数精确（101 行）、无锁等待/死锁异常。SQLite 单写者模型下 hot-row 正是 `database is locked` 的典型触发点，PG 行级锁无冲突。

### 2. 离线评测不退化 ✅（PG 模式实跑，非 sqlite 对照）
`LLM_MODEL=qwen3.7-plus DOC_REGISTRY_BACKEND=postgres python -m backend.evaluation rag --dataset rag_test_kb.json`（2m32s，exit 0，日志 `logs/r1_eval_pg.log`）：

| 指标 | R2 基线 | R1（PG 模式） | 判定 |
|---|---|---|---|
| Recall@5 | 0.8731 | **0.8731** | ✅ 持平 |
| MRR | 0.8277 | **0.8277** | ✅ 持平 |
| NDCG@10 | 0.8251 | 0.8251 | ✅ 持平 |
| 拒答准确率 | 1.0000 | **1.0000**（negative 15/15） | ✅ |
| 通过率 | 92.2%（95/103，8 失败 0 错误） | 92.2%（95/103，8 失败 0 错误） | ✅ 失败用例集合一致 |
| 分层 | smoke 100%/core 90.3%/hard 89.5% | 逐层一致 | ✅ |

检索链路（hybrid.py 的 pending_review 过滤）与评测启动恢复全程走 PG registry，逐指标与基线**逐位一致**——内容等价性由迁移对账 + 评测双重证实。

### 3. RAG 定向回归无退化 ✅
```
./.venv/Scripts/python.exe -m pytest backend/tests/rag/ backend/tests/config/ \
  backend/tests/test_rag_upload_celery_mode.py backend/tests/test_indexer_recovery_and_reindex.py \
  backend/tests/test_upload_resilience.py -q -p no:randomly --no-cov
结果: 534 passed / 2 skipped / 0 failed（2m18s，日志 logs/r1_regression.log）
```
对照 R2 基线 519 例 = 517/2/0：**原 519 例零退化，新增 17 例 PG 测试全过**（接口行为 12 + 引擎分发 4 + 并发验收 1）。

## 四、已知语义差异（有意为之，均有界）

1. **分页/并列排序 tiebreaker**：PG 对 `updated_at` 同秒并列行无稳定序（SQLite 靠 rowid）→ PG 侧 `ORDER BY ..., file_path` 补确定性 tiebreaker，`get_by_path`/`get_by_doc_id` 同理。
2. **register 保留 created_at**：SQLite `INSERT OR REPLACE` 会重置 created_at，PG `DO UPDATE` 保留原值（更合理，无调用方依赖旧行为）。
3. **双后端并存期数据不互写**：开关只决定读写哪一套，PG↔SQLite 之间不做自动同步。回滚 = `DOC_REGISTRY_BACKEND=sqlite`；回滚后 PG 期间新增的 registry 行需要重跑迁移脚本（幂等 upsert）找回，属已知代价。
4. 时间列保持 TEXT（见 ①），排序语义与 SQLite 一致，R4 引入真正的 `effective_from` 时建议改 timestamptz。

## 五、变更文件清单

| 文件 | 变更 |
|---|---|
| `backend/rag/indexing/doc_registry.py` | 引擎分发 `__new__` + docstring（+12 行，SQLite 实现零改动） |
| `backend/rag/indexing/doc_registry_pg.py` | **新增** PG 连接层（子类，全接口） |
| `backend/config/database.py` | `DOC_REGISTRY_BACKEND` / `DOC_REGISTRY_PG_CONFIG` / `DOC_REGISTRY_PG_TABLE` |
| `backend/sql/migrations/010_doc_registry_pg.sql` | **新增** 建表脚本 |
| `backend/scripts/migrate_doc_registry_to_pg.py` | **新增** 迁移 + 对账 |
| `backend/scripts/reset_rag_index.py` | doc_registry 清理按开关分发 |
| `backend/observability/metrics.py` | 旁路裸 sqlite 读改走 registry |
| `backend/tests/rag/test_doc_registry_pg.py` | **新增** 17 例（pg marker，PG 不可达自动 skip） |
| `docs/未完成功能进度汇总-2026-09-16.md` | 附录二回执 append + R4 依赖说明 |

## 六、遗留与移交

- 生产容器（app/worker）切换 `DOC_REGISTRY_BACKEND=postgres` 时**无需 rebuild**（psycopg2 镜像已有），restart 生效； rag-service 同理。
- R-P1-4 的「无版本字段」半边归 R4（本会话只做存储引擎，字段治理不动——与附录二 R1/R4 分工一致）。
- §12.2「重复投递不产生重复 chunk」等幂等核查项仍归 R5，与本次改动无耦合。

# RAG 上传入库模型血缘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每次文件上传/重索引建立可追溯的处理运行，记录 OCR、元数据抽取、问题生成、表格描述、语义切分和 Embedding 实际使用的模型/引擎及版本，并在管理端按处理链展示，同时不破坏高并发和 Token 缓存路径。

**Architecture:** 使用 `rag_processing_runs` 记录一次文件处理，使用 `rag_processing_steps` 记录每个逻辑阶段和每次重试/降级尝试；`doc_registry` 只保存最后一次成功运行的摘要指针。通过轻量 `ContextVar` 将 `run_id/step_id/role/stage` 传入 LLM、OCR 和 Embedding 用量记录，Trace 继续负责调试但不再承担唯一血缘职责。模型角色从 `main` 拆出 `metadata_extract`、`question_gen`、`table_describe`，Rerank 保持查询链独立展示。

**Tech Stack:** FastAPI、Celery `rag_index`、PostgreSQL migrations、psycopg、Redis 缓存、现有 Trace/`llm_usage`、Next.js 14、React、TypeScript、pytest、Vitest。

**Spec:** `docs/superpowers/specs/2026-09-19-rag-metadata-pipeline-governance-design.md` §14

> **实施状态（2026-09-20）**：核心链路已接入并完成容器重建；PG 迁移已应用，
> app、worker、metadata-shadow-worker 均已健康。以下勾选以代码和测试证据为准；
> 真实四格式供应商验收已完成；未完成项集中在生产等价 2 倍峰值压测和管理端
> 组件化收敛，不影响当前主链路试运行。

### 已落地的关键接线

- `ProcessingRunContext` + `ContextVar`：每个文件一个 `run_id`，每个阶段一个 `step_id`。
- PG `rag_processing_runs/steps`、`doc_registry` 摘要指针和 `llm_usage` 归因字段已应用。
- OCR、metadata R0/R1/R2、summary、question_gen、table_describe、embedding、vector_write 全链路留痕；未调用阶段不填模型。
- 角色解析已拆出 `metadata_extract/question_gen/table_describe`，缓存键包含模型/配置/Prompt/Schema 版本。
- 异步血缘写入按 run 内有序、run 间并行，不阻塞主索引；向量库回退嵌入也保持阶段绑定。
- 上传 SSE、文档 API 和管理端文档列表/血缘弹窗已接入；Rerank 仍属于查询时链路。
- 入库专用角色的直接模型实例已补接统一 Token 记录；`metadata_extract` 的
  `llm_tokens` 会从供应商响应进入 `DecisionEnvelope` 和处理阶段，供应商未返回
  用量时以 `llm_usage_status=unavailable` 区分“调用成功但用量未知”和“未调用”。

### R1 分类器模型文件配置（开发环境）

R1 不是远程 LLM 角色，而是本地 `joblib` 分类器文件；因此不在模型供应商页配置。
容器通过共享卷 `/app/data` 读取，配置项如下：

```text
METADATA_CLASSIFIER_ENABLED=true
METADATA_CLASSIFIER_MODEL_PATH=/app/data/models/metadata_lr/lr_model.joblib
```

当前开发种子集只能生成 dry-run 文件，先用它验证接线：

```powershell
docker compose exec -T worker python -m backend.eval.metadata_baseline.train_lr `
  --golden backend/eval/metadata_baseline/golden_seed.jsonl `
  --out /app/data/models/metadata_lr --dry-run --calibrate
```

Compose 开发默认路径为
`/app/data/models/metadata_lr/lr_model_dryrun.joblib`；启用当前开发实例可执行：

```powershell
$env:METADATA_CLASSIFIER_ENABLED = "true"
docker compose up -d --force-recreate app worker
```

正式上线必须将路径切换到黄金集正式训练产物 `lr_model.joblib`，并通过黄金集门禁。
`model_card.promotable=false` 的 dry-run 文件只用于开发验证，不作为生产质量证明。

## Global Constraints

- 每个文件每次上传或重索引只能创建一个 `run_id`；同一批文件使用 `batch_id` 关联，但不能共用 `run_id`。
- `doc_registry` 只保存列表查询摘要；阶段详情必须从 `rag_processing_runs` 和 `rag_processing_steps` 获取，禁止前端逐 Chunk 查询。
- 未调用模型的阶段必须记录 `status=skipped` 和原因，不能填写当前配置模型冒充实际调用。
- 云端模型记录响应中的实际模型名；本地模型记录引擎/包版本和模型文件指纹；不存在模型版本时写 `null`，不推测版本。
- 一次运行开始时固定不含密钥的有效模型角色、Prompt、规则、Taxonomy 和 Schema 快照；配置中心暂不可用时记录 `env|code-default` 来源和快照 hash。
- 血缘表不保存原文、完整 Prompt 或模型响应，只保存 hash、计量、版本和必要的错误摘要。
- 现有 `METADATA_CASCADE_ENABLED`、R1 分类器和 shadow 开关语义不变；开发环境可继续 100% 级联，生产仍按现有灰度门禁。
- 局部 pytest 必须使用 `--no-cov`；后端修改后至少运行对应测试、`py_compile` 和注册/分层一致性测试。
- 先查看 `git diff`，只修改本计划列出的文件；不要覆盖现有工作区用户改动。

---

### Task 1: 建立跨阶段处理上下文和血缘领域契约

**Files:**
- Create: `backend/shared/processing_context.py`
- Create: `backend/rag/indexing/processing_lineage.py`
- Test: `backend/tests/rag/test_processing_lineage.py`
- Test: `backend/tests/rag/test_processing_context.py`

**Interfaces:**
- `processing_context.py` 提供纯 stdlib 的 `ProcessingBinding`、`bind_processing(binding)` 上下文管理器和 `get_processing_binding()`，不得导入 LangChain、数据库或 RAG pipeline。
- `processing_lineage.py` 提供 `StageStatus = Literal["running", "success", "skipped", "cached", "fallback", "failed"]`、`RunStatus`、`ModelIdentity`、`ProcessingRunSnapshot`、`ProcessingStepSnapshot`。
- `ProcessingRunContext.create(doc_id, file_hash, operation, *, task_id=None, batch_id=None, trace_id=None)` 生成唯一 `run_id`，并固定 `pipeline_version`、`git_sha`、`config_snapshot_hash` 和无密钥配置摘要。
- `ProcessingRunContext.begin_stage(stage, *, role=None, engine_type, ordinal, model=None, skip_reason=None) -> str` 返回 `step_id`；`finish_stage(step_id, *, status, input_count=0, output_count=0, cache_status="miss", usage=None, error_message=None, fallback_reason=None)` 完成阶段。
- `ProcessingRunContext.finish(status, *, error_message=None) -> ProcessingRunSnapshot` 只允许终态调用一次；重复完成必须抛出明确异常。

- [ ] **Step 1: 编写上下文隔离测试**

验证两个并发 `contextvars.copy_context()` 任务分别读取自己的 `run_id/step_id/role/stage`，一个任务清理后不能污染另一个任务，未绑定时返回 `None`。

- [ ] **Step 2: 编写领域契约失败测试**

覆盖未知阶段、非法状态跳转、重复完成、负 Token、`skipped` 却填写实际模型等强断言；确认 `skipped` 允许 `model_name=None`，`success` 的模型是否必填由 `engine_type` 决定。

- [ ] **Step 3: 实现纯内存运行上下文**

上下文只负责生成和校验快照，不在本任务连接数据库；`ModelIdentity` 至少包含 `role/engine_type/provider/model_name/model_revision/config_source/config_revision/artifact_fingerprint`。

- [ ] **Step 4: 运行测试**

Run: `cd backend; python -m pytest tests/rag/test_processing_context.py tests/rag/test_processing_lineage.py -q --no-cov`

Expected: PASS，且测试不启动 Redis、PostgreSQL 或模型。

---

### Task 2: 增加 PostgreSQL 运行/阶段存储和文档摘要指针

**Files:**
- Create: `backend/sql/migrations/027_rag_processing_lineage.sql`
- Create: `backend/rag/indexing/processing_lineage_pg.py`
- Modify: `backend/rag/indexing/doc_registry_pg.py`
- Modify: `backend/observability/llm_usage_store_pg.py`
- Create: `backend/tests/rag/test_processing_lineage_pg.py`
- Test: `backend/tests/rag/test_doc_registry_version_fields.py`

**Interfaces:**
- `PostgresProcessingLineageRepository.create_run(snapshot: ProcessingRunSnapshot) -> str`
- `PostgresProcessingLineageRepository.upsert_stage(snapshot: ProcessingStepSnapshot) -> None`
- `PostgresProcessingLineageRepository.finish_run(run_id: str, status: RunStatus, *, finished_at, error_message=None, model_summary=None) -> None`
- `PostgresProcessingLineageRepository.get_latest_for_doc(doc_id: str) -> dict | None`
- `PostgresProcessingLineageRepository.list_runs(doc_id: str, *, page: int, page_size: int) -> dict`
- `PostgresProcessingLineageRepository.get_run_detail(doc_id: str, run_id: str) -> dict | None`
- `PostgresDocumentRegistry` 增加 `last_processing_run_id`、`pipeline_version`、`metadata_route`、`ocr_used`、`ocr_model`、`metadata_model`、`processing_status`、`processing_finished_at` 的读写和搜索结果字段。

- [ ] **Step 1: 编写迁移契约测试**

检查迁移文本包含两张表、`run_id/stage/attempt_no` 唯一约束、按 `doc_id+finished_at` 和 `run_id+ordinal` 的索引、外键/级联策略、JSONB 字段以及 `doc_registry` 摘要列；不得出现原文或 API Key 字段。

- [ ] **Step 2: 编写 Repository 行为测试**

用 fake cursor 固定参数顺序，断言 `create_run`、`upsert_stage`、`finish_run`、`get_latest_for_doc`、`get_run_detail` 使用参数化 SQL；同一 `(run_id, stage, attempt_no)` 重放只更新不插入重复阶段。

- [ ] **Step 3: 实现迁移**

创建 `rag_processing_runs` 和 `rag_processing_steps`；金额/Token 使用整数或数值字段，时间使用带时区时间；`model_summary`、`config_snapshot`、阶段扩展字段使用 JSONB；`doc_registry.last_processing_run_id` 使用可空外键或逻辑关联，兼容历史行。

- [ ] **Step 4: 实现仓储和 Registry 摘要更新**

仓储初始化失败时沿用现有 PostgreSQL fallback 约定，但不能吞掉主索引异常；只在成功入库后更新文档当前指针，失败运行保留历史记录但不覆盖最后成功摘要。

- [ ] **Step 5: 运行测试**

Run: `cd backend; python -m pytest tests/rag/test_processing_lineage_pg.py tests/rag/test_doc_registry_version_fields.py -q --no-cov`

Expected: PASS；随后在开发数据库应用迁移并验证表、索引和列存在。

---

### Task 3: 引入独立入库模型角色并让调用方显式选择角色

**Files:**
- Modify: `backend/config/model_roles.py`
- Modify: `backend/infra/llm/proxy.py`
- Modify: `backend/rag/preprocessing/llm_enrichment.py`
- Modify: `backend/rag/preprocessing/metadata_llm.py`
- Modify: `backend/rag/preprocessing/question_gen.py`
- Modify: `backend/rag/preprocessing/table_describe.py`
- Test: `backend/tests/rag/test_metadata_llm.py`
- Test: `backend/tests/rag/test_simulated_questions_pipeline.py`
- Test: `backend/tests/rag/test_table_describe.py`
- Create: `backend/tests/infra/test_llm_role_resolution.py`

**Interfaces:**
- 在 `MODEL_ROLES` 中新增 `metadata_extract`、`question_gen`、`table_describe`；三者默认可继承 `main`，但保留 `source=inherit` 语义，不把未配置值伪装成独立绑定。
- `backend.infra.llm.proxy.get_llm_for_role(role: str) -> BaseChatModel`：按 DB → env → code-default 解析指定角色，使用现有模型缓存、限流、重试、Token 记录。
- `invoke_metadata_llm(prompt, llm_obj=None, *, role="metadata_extract")`：`llm_obj` 显式传入时保持测试兼容；未传入时调用 `get_llm_for_role(role)`。
- `question_gen._invoke_llm(prompt, llm_obj=None)` 固定传 `role="question_gen"`；`table_describe._invoke_llm` 固定传 `role="table_describe"`；统一抽取固定使用 `metadata_extract`。

- [ ] **Step 1: 编写角色解析失败测试**

验证 DB 覆盖、env、code-default、inherit 四种来源，角色不存在时报 `KeyError`，并断言 `get_llm_for_role("question_gen")` 不会隐式读取 `main` 的模型名作为角色名。

- [ ] **Step 2: 编写调用方角色测试**

分别 monkeypatch `get_llm_for_role`，调用元数据、问题生成、表格描述入口，断言收到的 role 分别是 `metadata_extract/question_gen/table_describe`；已有 fake LLM 无 `model` 属性时仍能正常测试。

- [ ] **Step 3: 实现角色解析和显式调用**

复用现有 `_get_override_llm` 和 provider/限流包装，不新增第二套模型客户端；每次调用把 `role` 放入当前处理上下文，使后续用量记录能知道用途。

- [ ] **Step 4: 修正版本化缓存键**

`question_gen._cache_key` 的输入改为 `role + effective_model + prompt_version + schema_fingerprint + doc_type + chunk_text`；表格描述增加同样的模型/Prompt/Schema 版本信息。旧键自然失效，不删除旧 Redis 数据。

- [ ] **Step 5: 运行测试**

Run: `cd backend; python -m pytest tests/rag/test_metadata_llm.py tests/rag/test_simulated_questions_pipeline.py tests/rag/test_table_describe.py tests/infra/test_llm_role_resolution.py -q --no-cov`

Expected: PASS，且不发生真实外部模型请求。

---

### Task 4: 将运行上下文接入 LLM、OCR、Embedding 和用量记录

**Files:**
- Modify: `backend/infra/llm/proxy.py`
- Modify: `backend/infra/token_tracker.py`
- Modify: `backend/observability/llm_usage_store.py`
- Modify: `backend/observability/llm_usage_store_pg.py`
- Modify: `backend/rag/embedding_singleton.py`
- Modify: `backend/rag/preprocessing/parser/ocr.py`
- Modify: `backend/rag/preprocessing/parser/pdf_parser.py`
- Test: `backend/tests/rag/test_pdf_ocr.py`
- Test: `backend/tests/rag/test_embedding_singleton.py`
- Create: `backend/tests/infra/test_llm_usage_provenance.py`

**Interfaces:**
- 用量记录统一从 `get_processing_binding()` 读取 `run_id/step_id/role/stage`，字段为空时保持普通问答调用兼容。
- OCR 暴露 `get_ocr_model_identity() -> ModelIdentity`；RapidOCR 返回包/模型文件指纹，DashScope 返回 `provider/model_name`。
- Embedding 记录 `role="embedding"`，语义切分调用使用独立 `stage="semantic_chunk"`，最终向量化使用 `stage="embedding"`。
- `ocr_image` 的云端缓存命中返回可供父级阶段消费的 `cache_hit` 信息，不能重复写 Token；RapidOCR 不写 Token。

- [ ] **Step 1: 编写用量关联测试**

在绑定 `run_id=run-a, step_id=step-b, role=metadata_extract` 的上下文中模拟一次 LLM 响应和一次 Embedding，断言写入 usage 的 payload 含相同关联字段；脱离上下文调用仍能写入原有字段。

- [x] **Step 2: 编写 OCR 分支测试**

覆盖文本层充足（OCR skipped）、RapidOCR 成功、DashScope 成功、云端缓存命中、OCR 页失败；断言模型身份、页数、缓存状态和 Token 不重复。补充了需要 OCR 但功能关闭、部分页失败和实际模型契约测试。

- [ ] **Step 3: 实现 LLM/Embedding 用量上下文透传**

在现有 `_record_tokens` 和 `_TrackedEmbedding` 记录点读取上下文，保持已有模型/provider/token/cost 字段；新增字段只追加，不改变旧字段含义。

- [x] **Step 4: 实现 OCR 模型身份和阶段信息**

由 PDF 解析器围住一次完整 OCR 页循环创建一个逻辑 `ocr` stage，按页调用共用同一 `step_id`；缓存命中、部分页成功和失败写入汇总，不为每页创建 PostgreSQL 阶段行。`ocr_required/ocr_attempted` 区分文本层充足、功能关闭和实际 OCR；Registry 的 `ocr_used` 对缓存/降级/失败尝试不再误记为 false。

- [x] **Step 5: 运行测试**

Run: `cd backend; python -m pytest tests/rag/test_pdf_ocr.py tests/rag/test_embedding_singleton.py tests/infra/test_llm_usage_provenance.py -q --no-cov`

Expected: PASS；OCR 测试默认不得产生真实网络调用。OCR/注册模型/四格式血缘回归当前通过。

---

### Task 5: 接入索引主链路并保证成功指针、失败重试和幂等

**Files:**
- Modify: `backend/rag/indexing/indexer.py`
- Modify: `backend/rag/indexing/stages/metadata_stage.py`
- Modify: `backend/rag/preprocessing/chunking.py`
- Modify: `backend/rag/preprocessing/question_gen.py`
- Modify: `backend/rag/preprocessing/table_describe.py`
- Modify: `backend/app/api/routes/rag_upload.py`
- Modify: `backend/app/api/routes/rag_documents.py`
- Modify: `backend/app/api/routes/_rag_shared.py`
- Create: `backend/tests/rag/test_indexer_processing_lineage.py`
- Create: `backend/tests/rag/test_metadata_stage_processing_lineage.py`
- Create: `backend/tests/rag/test_processing_lineage_idempotency.py`

**Interfaces:**
- `_index_file` 在已确定 `doc_id/file_hash` 后创建 `ProcessingRunContext`，解析、OCR、级联、富化、Embedding、写库均使用同一上下文；异常路径必须 `finish("failed")` 后再抛出。
- `MetadataStage` 写入 `metadata_route`、`rules_version`、`taxonomy_version`、`prompt_version` 和 `metadata_extract` 阶段模型；R0/R1 以 `skipped` 或 `success` 记录，不调用隐式 LLM。
- `EmbeddingStage` 接受可选 `processing_context`，不改变已有 `EmbeddingArtifact` 返回契约。
- 上传完成 SSE 的 `done/duplicate/error` 事件增加 `run_id` 和轻量 `model_summary`；操作日志 detail 增加 `run_id`，不嵌入完整阶段数组。

- [ ] **Step 1: 编写主链路失败测试**

模拟 OCR、metadata LLM、question_gen、table_describe、embedding 分别成功/跳过/失败，断言任何异常只结束当前 run，后续重试产生新 run，旧的最后成功指针不被失败运行覆盖。

- [ ] **Step 2: 编写不同文件类型矩阵测试**

使用现有 parser fixtures 覆盖文本 PDF、扫描 PDF、DOCX、XLSX/CSV：断言阶段集合符合文件类型，`ocr` 仅在扫描 PDF 触发，表格文件才出现 `table_describe`。

- [ ] **Step 3: 接入索引器阶段生命周期**

先写 run，再按阶段开始/结束；向量库和文档注册表写入成功后才更新 `doc_registry.last_processing_run_id`；重复任务使用既有幂等键，并在数据库唯一约束下安全重放。

- [ ] **Step 4: 接入级联和可选富化**

把 R0/R1/R2 路由、模拟问题、表格描述、语义切分分别绑定到明确 stage；记录 `feature_disabled/not_table/route_r0` 等跳过原因，禁止用当前环境模型填充跳过阶段。

- [ ] **Step 5: 接入上传 SSE 和操作日志**

后台 Celery 和同步 fallback 都返回相同的 `run_id` 字段；旧前端忽略新增字段仍能完成上传，新增前端使用 `run_id` 请求详情。

- [ ] **Step 6: 运行索引测试**

Run: `cd backend; python -m pytest tests/rag/test_indexer_processing_lineage.py tests/rag/test_metadata_stage_processing_lineage.py tests/rag/test_processing_lineage_idempotency.py tests/rag/test_indexer_trace.py -q --no-cov`

Expected: PASS，且现有索引器 Trace/进度契约不回归。

---

### Task 6: 提供文档处理运行和阶段详情 API

**Files:**
- Modify: `backend/app/api/routes/rag_documents.py`
- Modify: `backend/app/api/routes/rag.py`
- Modify: `frontend-admin/src/api/knowledge.ts`
- Create: `backend/tests/api/test_rag_processing_lineage_api.py`
- Create: `frontend-admin/src/api/knowledge-processing-lineage.test.ts`

**Interfaces:**
- `GET /api/rag/documents/{doc_id}/processing-runs?page=1&page_size=20` 返回运行摘要、状态、耗时、模型摘要、路由和 `run_id`。
- `GET /api/rag/documents/{doc_id}/processing-runs/{run_id}` 返回运行信息和按 `ordinal, attempt_no` 排序的阶段数组。
- 文档列表 `/api/rag/documents` 增加 `last_processing_run_id`、`processing_status`、`metadata_route`、`ocr_used`、`ocr_model`、`metadata_model`、`model_count`、`processing_finished_at`。
- TypeScript 增加 `ProcessingRunSummary`、`ProcessingStep`、`ProcessingRunDetail`，并实现 `knowledgeService.getProcessingRuns`、`getProcessingRunDetail`。

- [ ] **Step 1: 编写 API 响应契约测试**

断言成功、文档不存在、run 不属于 doc、空历史四种响应；detail 不返回原文、Prompt 或 response，只返回版本、模型、计量和错误摘要。

- [ ] **Step 2: 实现后端路由**

复用现有 `require_rag_user`；详情直接调用 Repository，不查询 Trace 作为数据源；参数使用 `page/page_size` 上限校验，返回字段固定为 snake_case。

- [ ] **Step 3: 扩展列表摘要**

批量获取当前页文档的最新成功运行，使用一次批量 SQL 或已有 Registry 查询，禁止循环调用 detail API。

- [ ] **Step 4: 实现前端 API 类型和请求方法**

对 `status/provider/model_name` 使用可选字段兼容历史文档；接口失败返回空列表/错误对象，不阻塞文档列表。

- [ ] **Step 5: 运行 API/类型测试**

Run: `cd backend; python -m pytest tests/api/test_rag_processing_lineage_api.py -q --no-cov`

Run: `cd frontend-admin; npm test -- --runInBand src/api/knowledge-processing-lineage.test.ts`

Expected: 两侧 PASS。

---

### Task 7: 在管理端展示入库模型血缘和运行历史

**Files:**
- Create: `frontend-admin/src/components/knowledge/ProcessingLineagePanel.tsx`
- Create: `frontend-admin/src/components/knowledge/ProcessingLineagePanel.test.tsx`
- Modify: `frontend-admin/src/app/knowledge/documents/page.tsx`
- Modify: `frontend-admin/src/app/knowledge/operations/page.tsx`
- Modify: `frontend-admin/src/app/knowledge/operations/traces/[id]/page.tsx`
- Modify: `frontend-admin/src/api/knowledge.ts`

**Interfaces:**
- `ProcessingLineagePanel` props：`{ docId: string; initialRunId?: string; compact?: boolean }`；内部先加载最近成功 run，点击历史项再加载 detail。
- 时间线阶段顺序固定为 `parser → ocr → semantic_chunk → metadata_extract → question_gen → table_describe → embedding → vector_write`，后端返回的未知阶段显示原始名称并排在末尾。
- 状态显示：`success=成功`、`skipped=未执行`、`cached=缓存命中`、`fallback=已降级`、`failed=失败`；跳过必须显示 reason。

- [ ] **Step 1: 编写组件渲染测试**

验证 RapidOCR、云端 OCR、R0、R2、缓存命中、失败降级、历史运行切换和空历史；断言 Rerank 不出现在入库时间线中。

- [ ] **Step 2: 实现阶段卡片和版本信息**

每个阶段展示实际 provider/model、模型 revision、配置来源、Prompt/规则版本、Token、耗时、缓存状态和错误摘要；无模型的 parser/rule 阶段显示引擎版本而不是 `-` 模型。

- [ ] **Step 3: 接入文档列表摘要**

增加 OCR、元数据路由、Embedding 模型和模型数量列/徽标；保留原有文档筛选、删除、重索引和 Trace 跳转。

- [ ] **Step 4: 接入操作中心和 Trace**

操作记录显示 `run_id` 和“查看处理链”；Trace 页面在文档索引 Trace 旁显示面板，但查询 Trace 的 Rerank 仍归入“查询模型”，不与入库模型混淆。

- [ ] **Step 5: 运行前端测试和类型检查**

Run: `cd frontend-admin; npm test -- --runInBand src/components/knowledge/ProcessingLineagePanel.test.tsx src/api/knowledge-processing-lineage.test.ts`

Run: `cd frontend-admin; npx tsc --noEmit`

Expected: PASS。

---

### Task 8: 完成高并发、Token 缓存和端到端验收

**Files:**
- Modify: `backend/rag/indexing/embed_cache.py`
- Modify: `backend/rag/preprocessing/question_gen.py`
- Modify: `backend/rag/preprocessing/table_describe.py`
- Modify: `backend/rag/preprocessing/parser/ocr.py`
- Modify: `backend/tests/rag/test_embed_cache.py`
- Create: `backend/tests/rag/test_processing_lineage_e2e.py`
- Create: `backend/scripts/verify_processing_lineage.py`
- Modify: `docs/superpowers/specs/2026-09-19-rag-metadata-pipeline-governance-design.md`

**Interfaces:**
- 所有缓存键必须包含 `content_hash + role + provider + model_name + model_revision + prompt/rules/taxonomy/schema fingerprint`；缓存命中返回阶段 `cache_status=hit`，不增加模型调用。
- `verify_processing_lineage.py` 接受 `--doc-id`、`--run-id`、`--expect-stage` 和 `--json`，输出阶段完整性、模型身份、版本字段、重复写入和摘要一致性结果，失败退出码非零。

- [x] **Step 1: 编写缓存版本回归测试**

同一内容同一模型命中缓存；只改变模型、Prompt、规则或 Schema 指纹时必须 miss；模型调用次数通过 fake LLM 计数断言为零/一，不依赖日志文本。
已由 `test_embed_cache.py` 及模型血缘相关回归覆盖并通过。

- [x] **Step 2: 实现所有缓存键版本化**

补齐 question_gen、table_describe、OCR 云缓存和 Embedding 缓存的模型/版本字段；旧键不迁移，避免旧结果冒充新模型结果。

- [x] **Step 3: 编写端到端四格式测试**

构造文本 PDF、扫描 PDF、DOCX、XLSX/CSV fixture，使用 fake OCR/LLM/Embedding 和 fake PostgreSQL repository，断言每种格式的阶段集合、跳过原因、真实模型和最终 `last_processing_run_id`。已覆盖 5 个场景并通过。

- [x] **Step 4: 增加高并发写入验证**

并发运行至少 100 个假的索引任务，断言每个文件只有一个成功 current pointer，阶段唯一约束无重复，失败任务不覆盖成功摘要，血缘写入不会触发无限重试。已通过 100 个并发运行隔离测试；PG 仓储补充为惰性、有界、线程安全连接池。

- [x] **Step 5: 运行完整验证**

Run: `cd backend; python -m py_compile backend/shared/processing_context.py backend/rag/indexing/processing_lineage.py backend/rag/indexing/processing_lineage_pg.py`

Run: `cd backend; python -m pytest tests/rag/test_processing_lineage_e2e.py tests/rag/test_embed_cache.py tests/rag/test_processing_lineage.py tests/rag/test_processing_lineage_pg.py -q --no-cov`

Run: `cd backend; python -m pytest tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py tests/skills/test_base_output_contract.py -q --no-cov`

Run: `cd backend; python scripts/verify_processing_lineage.py --json`

Expected: 所有测试通过；验证脚本输出无重复 run/step、无缺失实际模型身份、无错误的 skipped 模型字段。当前目标回归已通过，容器健康检查和迁移校验已通过。

- [x] **Step 6: 记录开发 Compose 上线门禁压测证据**

已补 `backend/scripts/run_metadata_load_v1.py`，默认使用缓存命中工作负载：真实写入 PostgreSQL 血缘、真实并发请求血缘详情 API，不调用外部 OCR/Embedding/LLM，避免开发压测产生云模型费用。报告由 `backend/eval/metadata_baseline/load_report.py` 统一生成，最终门禁仍由 `validate_release` fail-closed 校验。

当前开发 Compose 证据：

- [metadata-load-v1-20260920-64.json](../../../docs/evidence/metadata/metadata-load-v1-20260920-64.json)：峰值 `8/4=2.0x`，64/64 成功，队列最终排空，血缘 API P95 `313ms`，处理端到端 P95 `2406ms`，PG 连接池等待 P95 `0ms`、最大 `47ms`，LLM/Embedding/OCR 调用 `0`，缓存命中率 `100%`，重复写入 `0`，API 错误 `0`。
- 同一 64 任务将血缘异步写线程从 4 临时提高到 8 的对照报告显示 P95 `3640ms`、连接池等待最大 `141ms`，因此不把 8 线程作为默认上线参数；当前默认值保持 4。

这条证据证明的是“血缘记录/API/缓存命中”高并发门禁，不等价于真实外部模型冷启动或模型限流压测；生产上线前仍需在隔离配额下补一轮真实 OCR/Embedding/统一抽取数据，并补齐影子开启和回滚演练证据。不满足门禁时只回滚记录功能开关，不删除历史血缘数据。

已补 [metadata-rollback-v1-20260920.json](../../../docs/evidence/metadata/metadata-rollback-v1-20260920.json)：只切换共享路由指针，旧指纹存在、幂等重放成功、重复写入 0、演练耗时 0 秒，并恢复演练前指针；该报告已通过 `_rollback_report_gate`。

已补 [metadata-shadow-v1-20260920.json](../../../docs/evidence/metadata/metadata-shadow-v1-20260920.json)：开发 Compose 中真实上传 3 份文件，主链路 3/3 完成；FAQ 样本命中 R0，`metadata_extract` 为 `skipped/route_r0`，元数据 LLM 调用和 Token 均为 0；影子独立队列 3/3 成功，其中 FAQ 为 `L0/agreement=true`。本轮同时修复了级联路径漏提交影子任务，以及同步 Celery→临时事件循环桥接导致 `create_task` 可能未提交的问题，现改为直接投递专用影子线程池。

- [ ] **Step 7: 提交分阶段变更**

按“契约/迁移 → 模型角色/用量 → 索引接入 → API → 前端 → 压测验收”拆分提交；每次提交只包含本计划对应文件，提交前再次运行 `git diff --check` 和相关测试。

## 本轮执行记录

### 已完成

- Task 1～4：上下文、领域契约、PG 仓储、角色解析、Token 归因和 OCR/Embedding 透传已完成。
- Task 5：索引器已接入 `load → parser → ocr → semantic_chunk → metadata → metadata_extract → question_gen → table_describe → embedding → vector_write`；失败会收口为 failed，成功才回填 Registry 当前指针。
- Task 6：运行列表/详情 API、用量明细和文档摘要字段已完成；详情查询已改为短连接并发安全。
- Task 7：管理端文档列表增加模型数量/路由摘要、OCR 状态/模型，并提供运行历史和阶段模型版本弹窗。
- Task 8 的缓存版本化、高并发假任务验证、验证脚本和容器部署已完成。
- 本轮继续：新增四格式真实 `IncrementalIndexer` 假边界端到端回归；`PostgresProcessingLineageRepository` 改为共享进程内 `ThreadedConnectionPool`，避免每个阶段新建连接。
- 本轮 OCR：补齐 `ocr_required/ocr_attempted`、成功/失败页/缓存页统计和 `ModelIdentity` 契约；扫描件 OCR 关闭时记录 `feature_disabled`，云端 OCR 无 DB 绑定时保留开发环境 Key 兼容，DB 绑定仍优先且不回退。
- 本轮真实供应商验收：使用数据库已配置的 OCR `qwen3.5-ocr` 和 Embedding `qwen3.7-text-embedding`，通过上传入口完成扫描 PDF、文本 PDF、DOCX、XLSX 四种文件入库；详情 API 均为 `success`，扫描 PDF 的 OCR stage 为 `cached`，文本 PDF 为 `text_layer_sufficient`，DOCX/XLSX 为 `not_pdf`，四种格式的 Embedding stage 均记录真实模型且命中缓存。
- 本轮运行时修复：Celery prefork 子进程启动时刷新数据库模型注册表；保留专项 Embedding 的 `/compatible-mode/v1` 地址；配置快照改为读取实际 OCR 身份；文档摘要和管理端列表改为展示实际 Embedding 模型。新增 Worker 导入隔离、专项地址、配置快照和展示字段回归测试。
- 本轮最终校验：相关模型血缘/压测回归 `102 passed`，注册/分层/基础输出一致性 `44 passed`，`py_compile`、`git diff --check` 和容器内 `verify_processing_lineage.py --json` 通过；重建后的 `app/worker/metadata-shadow-worker` 均 healthy，`GET /health` 返回 200，真实 FAQ run 的 10 个阶段和 R0 零元数据 LLM 调用校验通过。管理端此前 `31` 个测试文件 `311 passed`、`npx tsc --noEmit` 也已通过。
- Step 6 开发压测：新增 `metadata-load-v1` 指标聚合、真实 PG 仓储连接池等待/重复阶段键统计和可重复压测脚本；8 并发（当前 4 并发峰值的 2 倍）下 64 个缓存命中任务全部成功，血缘详情 API 64/64 成功，门禁报告通过。

### 仍需上线前补齐

- 影子开发烟测和回滚演练证据已补齐；仍需使用生产等价数据执行真实 OCR/Embedding/统一抽取链路的 `metadata-load-v1` 2 倍峰值压测，并补齐外部模型 429/限流证据。当前开发缓存命中证据不能替代这一项。
- 管理端血缘弹窗目前内嵌在文档页，后续可抽成独立 `ProcessingLineagePanel` 并接入操作中心/Trace 详情；当前不影响文档页查看实际模型。
- 代码尚未拆分提交；当前工作区包含其他模型配置重构改动，提交前需按计划文件范围人工复核后再拆分。

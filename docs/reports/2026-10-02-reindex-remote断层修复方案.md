# reindex remote 断层修复方案（企业做法）

> 状态：待拍板 ｜ 日期：2026-10-02 ｜ 前置：04c9e4f（审核/删除 remote 收口，本方案是同源问题的第二期）

## 1. 问题定性

`RAG_MODE=remote` 部署下，`POST /api/rag/documents/{doc_id}/reindex`（`rag_documents.py:494`）直接构造 `IncrementalIndexer(..., pipeline.vectordb, pipeline.doc_db, pipeline.bm25_store)`，而 app 持有的是 `RAGServiceProxy`——索引管理面被 `__getattr__` 守卫拒绝（`rag/client.py:264`），按钮点击即报错。

与已修复的审核/删除同源，但不能照抄同一修法。**删除是秒级动作，同步转发可接受；reindex 是分钟级长任务**（parse→chunk→embedding provider 调用→写入），同步 HTTP 有四个硬伤：APISIX/浏览器读超时掐断、无进度回报、无失败重试、连接断开任务变盲。企业做法不是把同步调用超时调大，而是**任务化**。

## 2. 方案选型

| 选项 | 做法 | 结论 |
|---|---|---|
| A. 照抄删除：同步转发 rag-service | `/admin/documents/{id}/reindex` + client 代理 | ❌ 长任务同步 HTTP 四硬伤（上文），网关 300s 超时内大文档跑不完 |
| B. rag-service 内置 job 表 + 后台线程 | 自建任务状态/重试/恢复/进度 | ❌ 在 serving 进程重造一套任务运行时；重活压在服务进程影响检索延迟；进程崩溃任务即失 |
| **C. 复用 rag_index 异步任务运行时（推荐）** | reindex 入 Celery `rag_index` 队列，执行体落 rag-index-worker | ✅ 状态机/重试/恢复/进度/观测五件套全部复用上传链路的已实证设施 |

**选 C 的决定性事实**：`docker-compose.yml` 中 `rag-index-worker` 本身就是 `RAG_MODE: local`（第 482 行）——它持有本地 pipeline，**上传索引今天就在这个进程跑**。文件可读（上传走 staging 路径已实证）、PG 向量库共享、跨进程 BM25 热刷新（C 阶段机制）已随上传 overwrite 场景验证。reindex 与上传写的是同一套 `IncrementalIndexer`/同一批存储，执行场所是现成的，不需要 rag-service 参与执行。

## 3. 详细设计

### 3.1 提交口（app 侧，`rag_documents.py` reindex 端点改造）

语义从「同步执行」变「幂等提交」：校验通过 → 建任务 → 立即返回 `{"ok": True, "task_id": ..., "async": True}`。

- **保留全部现有校验**：归属 `authz.can_manage_row`、F2 多 active 行拒绝、文件存在性——这段逻辑原样保留在提交口。
- **活动任务 join（并发第一道闸）**：同 doc_id 已存在 PENDING/RUNNING 的 reindex 任务 → 返回既有 task_id（后来者搭车看进度），不报错不入队。判重权威在 PG tasks 表；并发提交窗口用唯一部分索引（`(biz_type, biz_id) WHERE status IN ('PENDING','RUNNING')`）或 PG advisory lock 兜底。
- **双保险（并发第二道闸）**：worker 执行前 `pg_advisory_xact_lock(hashtext('rag_reindex:' || doc_id))`——两个管理员同时点同一条，后者快速失败为「已有重索引进行中」（fail-fast 优于排队等待，管理端 UX 口径）。
- **降级路径**：broker 不可达 → 同步降级执行（对齐上传 `_do_index_sync` 的既有语义），响应带 `"degraded": true` 标记。

### 3.2 执行体（worker 侧）

- 新 Celery 任务 `tasks.reindex_document`，落 `backend/tasks/index_tasks.py`（与 execute_index 同队列同族）。
- **易漏点**：`queue_router.py:87` 对 `tasks.execute_index` 是显式任务名映射，新任务名必须同样登记 `task_routes`，否则默认路由投错队列（queue_router 注释里点名的根源模式）。
- 执行逻辑抽到 `backend/rag/indexing/reindex_service.py`（对齐 review_service 模式）：现 route 里的 `IncrementalIndexer` 构造（registry 回读 kb_id/department、`processing_task_id="reindex:{doc_id}"`）+ `reindex_file` + 终态 operation_log 收口，remote 提交口与本地模式共用这一个模块。
- **进度**：复用上传的 Redis 进度镜像（`_write_progress_redis` 同通道）+ processing lineage（`stage_elapsed` 各阶段耗时已天然落 PG，`/runs/{run_id}` 详情端点已存在）。
- **operation_log 写入方迁移**：现状 route 同步写 success/failed；任务化后提交口不写终态，由 worker 终态 settle 时写（对齐 index_tasks 的 `_settle_index_result` 收口模式）。

### 3.3 失败语义（全部复用，零新造）

- 可重试错误（embedding 超时/限流）→ 既有 `classify_task_error` 分类 + `rag_index` retry policy 退避重试。整节点幂等由两层既有安全网保证：registry duplicate/upsert 吸收重写 + `reindex_file` 的「先写后删/F4 精确清理」保证重试中途失败不丢旧向量。
- 不可重试/预算耗尽 → registry 状态 settle + TaskState FAILED + operation_log failed + Redis 终态事件。旧索引原样在服。
- 超时：rag_index 队列软/硬双层超时 + lease heartbeat 续租，均既有。

### 3.4 跨进程可见性（唯一需要实机验证项）

worker 写完新向量后，rag-service 检索侧拾取依赖既有热刷新机制（上传 overwrite 同路径，已实证）。验收必须显式覆盖：reindex 完成后 rag-service 侧新内容可检索、**旧内容不再命中**（删除传播与上传 overwrite 等价，用 RAG 上传验收的「双向对照终验」方法）。

### 3.5 双模式口径

- remote（生产）：任务化。
- local（app 裸跑开发态）：**同样走任务化**（broker 在 compose 栈里恒在；真无 broker 走 3.1 同步降级）。统一行为避免长期维护两套语义——企业口径：管理动作只有一种生命周期。

### 3.6 前端（frontend-admin）

- `documents/page.tsx`「重新解析」按钮：点击 → 提交拿 task_id → 按钮进入「重索引中」态 → 复用上传进度 SSE 通道（`GET /upload/{id}/stream` 泛化为任务进度流，或降级轮询 Redis 镜像 GET）→ 终态 toast + 列表刷新。
- 现有 `reindexing: Set<string>` 本地状态改为以 task 生命周期为准（页面刷新不丢状态，从任务列表/进度接口恢复）。

## 4. 明确不做（本期边界）

- **批量/全局重索引**（嵌入模型切换批量刷新）：基于本期任务化能力做批次编排（batch_id + 配额限速 + 断点续跑），独立排期。
- **tool_approval 审批门**：不引入。那是 Agent 调 Tool 层的门；管理端 reindex 已有 RBAC 人工授权（`require_rag_editor` + `can_manage_row`），与删除同口径。
- **rag-service 改造**：零改动（执行不走它）。

## 5. 测试与验收

| 层 | 用例 |
|---|---|
| 提交口 | 归属拒绝 / F2 多行拒绝 / 文件缺失 / 活动任务 join / broker 不可达降级 |
| worker | 成功（chunk_count/hash 回读一致）/ 可重试失败重试后成功 / 不可重试终态 FAILED 且旧索引在服 / 双提交同 doc 互斥 |
| 进度 | 事件序列（提交→各阶段→终态）+ SSE/轮询消费 |
| remote 流 | 对齐 `test_pending_review_remote_flow.py` 模式（mock broker 边界） |
| 波及面 | rag 波及面 57 用例 + `test_registry_consistency` 等（局部跑加 `--no-cov`） |
| 实机 | compose rebuild 后真点按钮：进度可见、完成后 rag-service 新内容命中且旧内容不再命中、operation_log 终态正确 |

CI：改动落在 backend/rag|tasks → `rag_smoke.yml` 8 条 smoke 自动触发，符合既有门禁。

## 6. 发布与回滚

- 开关：`RAG_REINDEX_ASYNC_ENABLED`（默认关）。开启前 local 同步旧路径在 remote 下本来就是坏的，flag 关闭的回滚语义 = 恢复「明确报错」，不是恢复「能用」——如实登记。
- 提交纪律：后端 + 前端同一原子提交（同仓同发布，接口语义从同步变异步属破坏性变更，消费方必须同提交更新）；涉及任务名登记不触碰 Tool 契约，无需重生成两个 lock。
- 实机验证前必查容器内代码（rebuild 后 exec grep 核对，防「up 假成功」）。

## 7. 工作量

后端约 0.5–1 天（任务 + 提交口 + 互斥 + 测试），前端 0.5 天（状态机 + 进度），实机验收 0.5 天。单会话可闭环。

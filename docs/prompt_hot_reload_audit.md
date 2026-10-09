# Prompt Runtime 热更新审计报告

> 审计日期：2026-09-30
> 范围：Phase 0 基线审计；后续已按本报告接入客服独立 Key、Runtime epoch、热更新监听和请求级 pin。
> 基线取样：本机 `agent_memory`（PostgreSQL 5433）与当前容器进程；实现后状态见文末。

## 结论

现有 Prompt 系统已具备 DB 版本、`production`/`staging` 指针、进程内快照、请求起始版本记录及 Trace 记录能力。基线时**快照只在进程启动（和 seed）时全量刷新**；实现后已增加 DB epoch、Redis pub/sub、订阅器和请求入口的新鲜度检查。

基线时，管理端发布后 app 会立即使用新版本，已启动的 Celery worker 子进程和 rag-service 则继续使用其启动时的旧快照，直至重启；这正是本次热更新接线所补齐的跨进程缺口。

另外，基线的 `AgentState.prompt_versions` 与 Trace tag 只是在请求/任务开始时记录快照。当前已补上版本→模板历史和节点上下文绑定，执行中的后续 `render_sync()` 会按 pin 读取旧模板。

## 1. Prompt Storage Audit Report

### 1.1 权威数据模型

存储库为 `agent_memory`，当前表位于默认 schema：

| 表 | 作用 | 关键字段 |
|---|---|---|
| `prompts` | Prompt 元数据和生产运行时指针 | `key`（唯一）、`active_version`、`is_code_controlled` |
| `prompt_versions` | 不可变式版本内容与工作流状态 | `prompt_id`、`version`、`template`、`status`、`change_kind` |
| `prompt_aliases` | 命名版本指针 | `prompt_id`、`alias`（`production`/`staging`）、`version` |
| `prompt_audit_log` | 操作审计 | `prompt_key`、`action`、`from_version`、`to_version` |

建表迁移为 [018_prompts_pg.sql](../backend/sql/migrations/018_prompts_pg.sql)，版本语义与 alias 为 [057_prompt_alias.sql](../backend/sql/migrations/057_prompt_alias.sql)。ORM 定义在 [prompt.py](../backend/memory/models/prompt.py)。

`prompts.active_version` 是生产读取路径的唯一事实源；`prompt_aliases.production` 在发布时与它同步，`staging` 当前不参与运行时读取。

### 1.2 当前数据库快照

实测查询时间：2026-09-30。

| 指标 | 值 |
|---|---:|
| Prompt Key 数量（基线） | 39 |
| 当前 Prompt Key 数量 | 43 |
| `prompt_versions` 数量（基线） | 39 |
| 当前 `prompt_versions` 数量 | 43 |
| `prompt_aliases` 数量（基线） | 40 |
| 当前 `prompt_aliases` 数量 | 44 |
| 有生产 active 版本的 Prompt | 43 |
| 当前 active 版本 | 全部为 `v1`，状态均为 `published` |
| `production` alias | 39 个，全部指向 `v1` |
| `staging` alias | 仅 `business_report.polish`，指向 `v1` |
| 审计记录 | 基线 `seed` 39 条、`set_alias` 4 条；后续客服 Key seed 4 条 |

当前 39 个生产 Key：

`business_report.polish`、`capability.inventory_analyzer`、`competitor.extractor`、`customer_service.answer`、`customer_service.system`、`evaluation.judge.user`、`market_research.analyzer`、`memory.long_term.fact_extraction`、`memory.session.auto_compact`、`memory.session.summary`、`memory.trigger`、`planner.critique`、`planner.system`、`rag.contextualize`、`rag.document`、`rag.evidence_gate.self_correction`、`rag.guardrails.judge`、`rag.guardrails.rewrite`、`rag.indexing.doc_type`、`rag.multi_query`、`rag.preprocessing.arbitration`、`rag.preprocessing.chunk_batch`、`rag.preprocessing.chunk_single`、`rag.preprocessing.doc_ollama`、`rag.preprocessing.doc_proxy`、`rag.preprocessing.llm_enrichment`、`rag.preprocessing.metadata_extract`、`rag.preprocessing.summary`、`rag.qa`、`reporter.summary`、`reporter.system`、`router.llm`、`selection_decision.differentiation`、`selection_decision.review_pain`、`selection.panel.vote`、`selection.recommender.reason`、`skill.business_analysis`、`sql.generator`、`sql.router`。

客服域新增的硬编码 Prompt 已迁移到独立 Key：`customer_service.query_intent`、`customer_service.complaint_assess`、`customer_service.supervisor`、`customer_service.redirect_main`；代码入口统一经 `backend/customer_service/prompting.py` 渲染。

### 1.3 发布与回滚入口

管理 API 位于 [prompts.py](../backend/app/api/routes/prompts.py)：

| 操作 | HTTP 入口 | 服务入口 | 现有行为 |
|---|---|---|---|
| 创建草稿 | `POST /prompts/{key}/drafts` | `PromptService.create_draft()` | 创建新版本，状态为 `draft` |
| 发布 | `POST /prompts/{key}/publish` | `PromptService.publish()` | 校验状态/模板，更新 `active_version`、`production` alias、版本状态和审计；只更新本进程快照 |
| 回滚 | `POST /prompts/{key}/rollback` | `PromptService.rollback()` | 调用 `publish(..., skip_workflow=True)`；只更新本进程快照 |
| 切 production | `POST /prompts/{key}/aliases/production` | `PromptService.set_alias()` | 复用 `publish()`，因此等同发布 |
| 切 staging | `POST /prompts/{key}/aliases/staging` | `PromptService.set_alias()` | 仅更新 alias，不影响运行时读取 |

发布实现位于 [service.py](../backend/prompts/service.py#L249)。目前在 DB 提交后仅调用本进程的 `_fire_hooks(key)`；仓库没有生产环境的 `register_reload_hook()` 调用方。

## 2. Prompt Read Path

### 2.1 统一服务与优先级

[PromptService](../backend/prompts/service.py) 的读取规则如下：

```text
render_sync / get_template_sync
  进程内 _snapshot → YAML defaults

get_active（异步）
  进程内 _snapshot → Redis 缓存（TTL 300s）→ PostgreSQL → YAML defaults
```

`refresh_snapshot()` 从 `prompts.active_version` 读取每个 Key 的版本内容，写入 `_snapshot`，并仅增加进程内 `_epoch`。一旦快照已命中，异步读取路径不会访问 Redis 或 DB；同步读取路径从不访问 Redis 或 DB。

### 2.2 刷新生命周期

全仓生产刷新点只有以下四类：

| 进程/场景 | 位置 | 触发时机 |
|---|---|---|
| FastAPI app | [server.py](../backend/app/server.py#L189) | app startup |
| Celery prefork 子进程 | [celery_app.py](../backend/tasks/celery_app.py#L226) | `worker_process_init` |
| rag-service | [rag_server.py](../backend/services/rag_server.py#L139) | service startup |
| YAML seed | [service.py](../backend/prompts/service.py#L404) | seed 完成后 |

当前运行中有 app、7 个 Celery worker/dispatcher、beat 和 rag-service；它们的快照相互独立。现已在 app、Celery prefork 子进程和 rag-service 启动 `PromptHotReload` listener，并在请求/任务入口执行 epoch 对比。

### 2.3 Prompt 消费方

所有业务消费最终使用 `prompt_service.render_sync()`、`get_template_sync()` 或少量管理端异步 `render()`；没有业务消费方直接查询 Prompt 表。主要链路如下：

```text
Planner / Critique / Reporter / Router
LangGraph node → PromptService.render_sync() → 本进程 snapshot → LLM

RAG query / multi-query / evidence gate
RAG chain → PromptService.get_template_sync() 或 render_sync() → snapshot → LLM

RAG indexing / metadata preprocessing（可由 rag-service 或 worker 执行）
preprocessing stage → PromptService.render_sync() → 各自进程 snapshot → LLM

SQL、Memory、Selection、Customer Service、Business Report 等
业务模块 → PromptService 同步读取 → 各自进程 snapshot → LLM
```

已扫描的读取 API 包括 `get_active`、`render`、`render_sync`、`get_template_sync`、`current_versions`；仓库中没有独立的 `get_prompt`/`render_prompt` 业务读取器，`get_prompt` 和 `render_prompt` 是管理 API 处理函数名。

## 3. Trace Version Flow

### 3.1 请求与任务开始时的版本 pin

现有代码已经实现请求级 pin，但 pin 的来源是**当前进程快照**：

```text
GraphRunner.stream()
  → prompt_service.current_versions()
  → trace.tags["prompt_versions"]
  → initial_state["prompt_versions"]
  → LangGraph / checkpoint

TaskExecutor.start_trace()
  → prompt_service.current_versions()
  → trace.tags["prompt_versions"]
```

对应位置为 [runner.py](../backend/orchestration/graph/runner.py#L386) 和 [task_executor.py](../backend/orchestration/checkpoint/task_executor.py#L256)。当前已通过 `PromptService.pin_snapshot()`、`bind_prompt_versions()` 和 Trace middleware 节点边界绑定实现版本绑定；长请求中途发布不会改变已 pin 的模板。

### 3.2 每次实际渲染的版本记录

`render()` / `render_sync()` 读取到版本后会：

1. 写入 ContextVar `_prompt_usage_var`；
2. 调用 `record_prompt_version(key, version, source)`；
3. 在 trace 收尾时由 `trace_collector.finish()` 收集，写入 `trace.metadata["prompt_versions"]`。

相关实现：[service.py](../backend/prompts/service.py#L149)、[prompt_trace.py](../backend/observability/prompt_trace.py)、[tracer.py](../backend/observability/tracer.py#L535)。

当前 Trace 可保存 `key`、`version`、`source`，并在 `tags.prompt_runtime` 保存 `epoch`、`versions`、`snapshot_time`、`reload_source`；实际渲染记录的 source 会标记为 `pinned`。

## 4. Phase 1+ 的事实约束

1. DB 的 `active_version` 与 `production` alias 已是发布权威；Redis 只能作为传播信号，不能取代 DB。
2. 热更新必须覆盖 `publish()` 的所有复用入口：`rollback()` 和 `set_alias(..., "production")`；`staging` 不得触发运行时刷新。
3. `current_versions()` 调用之前是请求/任务确保快照新鲜的正确接线位置；随后还必须冻结可按 key 查找的版本模板，并让所有渲染经该请求上下文读取，才能实现真正的 pin。
4. Redis 或刷新失败必须保留现有快照并继续业务，即 fail-open；不得使渲染路径抛出新的可用性错误。
5. Trace 在新鲜度检查后记录 runtime epoch、snapshot 时间和 reload 来源，并按请求 pin 的版本记录实际渲染。

## 5. 发现的缺口

| 需求 | 当前状态 | 缺口 |
|---|---|---|
| DB runtime epoch | 已实现 | `065_prompt_runtime_hot_reload.sql`，初始 `epoch=1` |
| 发布后跨进程通知 | 已实现 | `bump_prompt_epoch()`：DB + Redis INCR/PUBLISH |
| 丢消息后的最终一致性 | 已实现 | 请求/任务入口 epoch 拉取比对，fail-open |
| app/worker/rag 监听 | 已实现 | 三类进程启动 daemon listener |
| 请求级固定版本 | 已实现 | version→template 历史 + 节点上下文绑定 |
| Trace prompt key/version | 已实现 | `prompt_runtime` 运行态字段 + pinned 实际渲染来源 |
| 管理端运行状态 | 已实现 | Redis heartbeat + `/prompts/runtime-status`（兼容 `/admin/prompts/runtime-status`） |

## 审计结论

实施按用户给定顺序完成了 Phase 1~7 的代码接线，不需要替换 Prompt 系统或引入配置中心。

## 6. 实施后验证状态（2026-09-30）

- migration `065_prompt_runtime_hot_reload.sql` 已执行，`prompt_runtime_state.global_epoch=1`，事件表为空。
- 客服四个独立 Key 已 seed 到 `agent_memory`，当前均为生产 `v1`；`backend/prompts.lock.json` 已按 DB 重新生成。
- `backend/tests/prompts`：`130 passed`；热更新专项测试 7 个、pin 专项测试 2 个均通过。
- Redis 故障路径按 fail-open 处理；恢复后由请求入口拉取检查追平。生产发布/回滚验收仍需在启用新代码的容器滚动部署后执行。

## 7. 浏览器发布与回滚验收（2026-09-30）

管理端实际操作 `customer_service.supervisor`：

1. 浏览器保存新版草稿 v2，经 `draft → testing → evaluation → passed` 后点击「发布」；DB `active_version=2`，运行 epoch `1→2`。
2. 发布后约 7 秒内，app、全部 Celery worker、rag-service 的运行态均收敛到 v2；管理端运行态显示 epoch 2 且所有进程 `healthy`。
3. 浏览器点击「回滚」到 v1，epoch `2→3`，全部进程收敛到 v1；再回滚到 v2，epoch `3→4`，全部进程恢复 v2。
4. Playground 真实 LLM 结果：v2 对「投诉且要求升级」返回 `handoff`；回滚 v1 同一场景返回 `complaint`，证明运行时确实切换了版本，而非仅更新界面。
5. 运行态事件表记录 epoch 2、3、4 均为 `prompt.changed / published / retry_count=0`。

本轮专项验证：

```text
backend/tests/prompts/test_hot_reload.py
backend/tests/prompts/test_prompt_pin.py
backend/tests/api/test_prompts_api.py::TestPlayground::test_playground_invokes_runtime_llm_proxy
11 passed
backend/tests/observability/test_prompt_version_trace.py + prompt pin
8 passed
frontend-admin: tsc --noEmit PASS
```

因此 Phase 8 的发布、热更新、回滚和管理端运行态验收已完成；长任务 Trace 仍以自动化 pin/trace 测试作为回归门，RAG 的 BM25/向量集合不一致是独立的既有数据问题，不属于 Prompt 热更新阻断项。

# Phase 2 Step 3 验收报告：Queue Topology + QueueRouter

> 基线：Step 2（fded947）→ 实施提交：**f35b850**（2026-09-23）
> 前置：Step 1 Worker Crash Auto Recovery PASS ｜ Step 2 Timeout/Retry/Error Taxonomy PASS

## Step 3：PASS

### 1. 修改摘要

| 文件 | 修改内容 |
| --- | --- |
| `backend/tasks/queue_router.py` | **新增**。QueueRoute/QueueRoutingError/workload registry/resolve 三入口/log_route 观测/celery_task_routes() 派生/beat_queue() |
| `backend/tasks/task_manager.py` | `enqueue_task` 接 QueueRouter（删 `queue="agent"` 硬编码）；新增 `dispatch_task` 统一派发入口 + `_WORKFLOW_DISPATCHERS` 注册表；`resume_task` 删 graph_name if-else、加 `dispatch_type`；`sweep_stale_executions` 改 `dispatch_task(recovery)` |
| `backend/tasks/index_task_runtime.py` | `redispatch_index_task` 队列决策改经 QueueRouter（不再直读 CELERY_RAG_INDEX_QUEUE），加 dispatch_type |
| `backend/tasks/agent_tasks.py` | `_retry_after_state_recheck` 显式 `self.retry(queue=route.physical_queue)` + log_route(dispatch_type=retry) |
| `backend/tasks/index_tasks.py` | index 壳 retry 同上（rag_index 亲和 + 跟随新 binding） |
| `backend/tasks/celery_app.py` | task_routes 改 `celery_task_routes()` 派生；beat 七项全部显式 `options.queue=beat_queue(...)`；task_default_queue=CELERY_AGENT_QUEUE |
| `backend/services/task_service.py` | `mark_queued` queue 必传（删默认 "agent"） |
| `backend/app/api/routes/tasks.py` | QueueRoutingError → 落 FAILED(queue_routing_error) + HTTP 400（不留假 RUNNING）；HTTPException 穿透 503 兜底 |
| `backend/app/api/routes/admin_tasks.py` | admin retry 传 `dispatch_type="admin_retry"`（不指定 queue） |
| `backend/app/api/routes/rag_upload.py` | `_dispatch_index_to_celery` 接 QueueRouter + log_route |
| `backend/rag/preprocessing/metadata_shadow.py` | `_enqueue_shadow_task` 接 `resolve_for_celery_task`（队列不变） |
| `backend/config/tasks.py` | 新增 `CELERY_AGENT_QUEUE`、`QUEUE_ROUTING_UNKNOWN_FALLBACK` |
| `backend/tests/test_task_queue_router.py` | **新增** 17 例（Case A-L + registry 派生一致性） |
| `backend/tests/test_task_{resume,cancel,phase2_recovery}.py` | enqueue mock 签名兼容修补（`(record)` → `(record, *args, **kwargs)`，断言语义不变） |

### 2. Commit

`f35b850`

### 3. Queue Topology（实际）

```text
workflow=main（用户 agent 任务）
→ logical=interactive_agent → physical=agent → agent-worker(-Q agent,rag_index)

workflow=rag_index（上传索引）
→ logical=rag_index → physical=rag_index（CELERY_RAG_INDEX_QUEUE）→ agent-worker(-Q agent,rag_index)

tasks.execute_metadata_shadow
→ logical=metadata_shadow → physical=rag_metadata_shadow（CELERY_METADATA_SHADOW_QUEUE）
→ metadata-shadow-worker(-Q rag_metadata_shadow)【独立 worker，未动】

beat: cs.handoff_timeout_scan / cs.confirmation_expiry_scan /
      cs.event_outbox_compensation / tasks.zombie_reconcile /
      tasks.stale_execution_recovery / model.health_scan / model.health_check_one
→ logical=maintenance → physical=agent（**Step 5 待拆** maintenance worker）

beat: cs.qa_daily_report
→ logical=report → physical=agent（**Step 5 待拆** report worker）
```

### 4. QueueRouter 设计

- **唯一事实源**：`backend/tasks/queue_router.py` 模块级三张 registry（`_LOGICAL_QUEUES` workload→物理、`_WORKFLOW_ROUTES` graph_name→workload、`_CELERY_TASK_ROUTES`+`_BEAT_TASK_ROUTES` task name→workload）。`celery_app.conf.task_routes` 与 beat `options.queue` 均由派生函数生成（G2，无第二份手写映射）。
- **registry 复用**：workflow 口径直接用 `tasks.graph_name`（`TaskRecord.workflow` 别名，Phase1 单一事实源，现存值仅 main/rag_index，不另立名单）；物理队列名复用既有 `CELERY_RAG_INDEX_QUEUE`/`CELERY_METADATA_SHADOW_QUEUE` env，补 `CELERY_AGENT_QUEUE` 对齐。
- **unknown workflow**：fail-closed 抛 `QueueRoutingError` 拒绝入队（API 层落 FAILED(queue_routing_error) + 400，不留假 RUNNING）。逃生门 `QUEUE_ROUTING_UNKNOWN_FALLBACK`（默认空=关）显式设置时降级 `routing_reason=legacy_fallback` + 必打 warning。
- **logical/physical 区分**：QueueRoute.workload_class=logical、physical_queue=物理。report/maintenance 物理映射 agent（Step 5 待拆），不创建无人消费的队列；未动 worker 拓扑/concurrency/prefetch。
- **不做的事**（边界遵守）：无并发/准入/优先级/租户限流（Step 4）、无 prefetch/fairness 调整、未改 `shared/idempotency.py`。

### 5. Initial / Retry / Resume / Recovery 路由

| Dispatch Type | Workflow | Expected Queue | Actual Queue | Result |
| --- | --- | --- | --- | --- |
| initial | main | agent | agent（route 日志+tasks.queue） | ✅ |
| initial | rag_index | rag_index | rag_index（单测+派生表断言） | ✅ |
| initial | metadata_shadow | rag_metadata_shadow | rag_metadata_shadow（实机） | ✅ |
| resume | rag_index | rag_index | rag_index（实机 T2） | ✅ |
| resume | main | agent | agent（单测 Case C 同型） | ✅ |
| retry | rag_index | rag_index | rag_index（实机 worker 日志 Retry in 13s 同队列重投） | ✅ |
| retry | main | agent | agent（实机+单测 Case F fake.retry kwargs） | ✅ |
| recovery | rag_index | rag_index | rag_index（实机 T3，recovery_count=1） | ✅ |
| recovery | main | agent | agent（实机 Case L） | ✅ |
| admin_retry | 任意 | 同 resume 链 | 经 QueueRouter（dispatch_type 标注） | ✅ |
| beat | maintenance/report | agent | agent（显式 options.queue） | ✅ |

### 6. Beat Routing（全量，不漏项）

| task | logical queue | physical queue |
| --- | --- | --- |
| cs.handoff_timeout_scan | maintenance | agent（Step 5 待拆） |
| cs.confirmation_expiry_scan | maintenance | agent（Step 5 待拆） |
| cs.event_outbox_compensation | maintenance | agent（Step 5 待拆） |
| cs.qa_daily_report | report | agent（Step 5 待拆） |
| tasks.zombie_reconcile | maintenance | agent（Step 5 待拆） |
| tasks.stale_execution_recovery | maintenance | agent（Step 5 待拆） |
| model.health_scan | maintenance | agent（Step 5 待拆） |
| model.health_check_one（手动触发注册项） | maintenance | agent（Step 5 待拆） |

### 7. Case A-L

| Case | Expected | Actual | PASS/FAIL |
| --- | --- | --- | --- |
| A initial agent | logical=interactive_agent physical=agent | 一致（实机 T1） | ✅ |
| B initial rag_index | rag_index | 一致 | ✅ |
| C rag_index resume | 仍投 rag_index | 实机 T2 resume → rag_index | ✅ |
| D rag_index retry | retry queue=rag_index | 实机 worker 日志（6482793f retry 同队列）+ 单测 | ✅ |
| E rag_index recovery | recovery queue=rag_index + checkpoint 恢复 | 实机 T3 sweep → rag_index，recovery_count=1 | ✅ |
| F agent retry | 仍 agent | 单测 fake.retry kwargs queue=agent | ✅ |
| G beat maintenance | 显式 route 不依赖 default | 8 项全部 options.queue + beat_binding | ✅ |
| H metadata shadow | 仍 rag_metadata_shadow 独立 worker | 实机 T5 + shadow worker 消费 | ✅ |
| I unknown workflow | 拒绝 + QueueRoutingError + 不留假 RUNNING | 单测 fail-closed + API 400 落 FAILED | ✅ |
| J 配置映射变化 | 用新 binding + 记 previous/resolved | 单测 monkeypatch registry + 实机 binding changed warning | ✅ |
| K 重复 resume | 仅一次 enqueue | 既有并发用例 test_resume_concurrent_two_clients_single_enqueue 保持通过 | ✅ |
| L retry/recovery 不串队列 | 均经同一 QueueRouter | 两者 route 日志均带 dispatch_type 且队列正确 | ✅ |

### 8. 硬编码清理（全仓 grep）

`queue="agent"` / `queue="rag_index"` / `queue="rag_metadata_shadow"` 扫描（排除 queue_router.py、config/tasks.py、celery_app.py 声明处）：**0 残留**。

合法残留说明：
- `config/tasks.py`：CELERY_AGENT_QUEUE/CELERY_RAG_INDEX_QUEUE/CELERY_METADATA_SHADOW_QUEUE 环境默认值（配置源本体）。
- `tasks/queue_router.py`：registry 引用上述 config 常量（唯一消费点）。
- `celery_app.py`：`task_default_queue=CELERY_AGENT_QUEUE`（Celery 框架必填项，未登记 task 的兜底）。
- `models/task.py` TaskRecord.queue 默认 "agent"：记录层读回默认，与 DB 列 `DEFAULT 'agent'`（schema.sql）自洽，非路由决策；实际值由 mark_queued(必传) 覆写。
- `docker-compose.yml`：worker `-Q agent,rag_index` 消费声明（拓扑声明，非投递决策）。

### 9. 测试结果

- Step 3 新增：`tests/test_task_queue_router.py` **17/17 passed**
- 契约三门：registry/layer/ADR0001 **36 passed**
- Phase 1 / Step 1 / Step 2 回归（resume/cancel/pause/phase2_recovery/taxonomy/state_machine/zombie/checkpoint/index_runtime/orchestration/execution_lock/rag_upload_celery_mode）：**与干净基线（b5c59dd worktree、零本 Step 改动）对照，失败清单逐条完全一致**（18+11=29 failed / 109 passed vs 基线同数）——29 个失败全数归属其他会话 `b5c59dd`（executor lease 接口收口 + 新增身份授权校验）未同步旧测试 stub 的波及面，**本提交零新增失败**。该 29 例修复责任在 b5c59dd 会话（其新增 test_task_executor_lease_interface.py 4 例自绿）。

### 10. 实机证据（docker 实测，app/worker/beat/shadow 已 rebuild）

| 项 | task_id | workflow | tasks.queue | actual Celery queue | dispatch_type | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| agent initial | ad271ee1…055d40ef | main | agent | agent（route 日志+worker 消费拓扑） | initial | QueueRouter route 行 + PENDING→CANCELLED 收尾 |
| rag_index resume | fd7011f0…eea81a5 | rag_index | agent→rag_index | rag_index | resume | redispatched to rag_index (7eec4334) + binding changed warning |
| rag_index recovery | 711d6f14…991b3f | rag_index | rag_index | rag_index | recovery | dispatch=recovery + recovery_count=1 + redispatched (6482793f) |
| retry（实机偶遇） | 同上 6482793f | rag_index | rag_index | rag_index | retry | worker 日志 `Task tasks.execute_index[6482793f] retry: Retry in 13s` |
| metadata shadow | step3-fake-shadow-job | metadata_shadow | —（无 tasks 行） | rag_metadata_shadow | initial | shadow worker 消费（fake job uuid 校验报错属预期噪音） |
| beat | — | maintenance | — | agent | beat | worker 3min 内消费 confirmation_expiry_scan×6/outbox×24/handoff×6/stale_recovery×12 |

- broker 实测：agent/rag_index/rag_metadata_shadow 三队列 LLEN 全 0（无积压、无死信）。
- 测试数据已清理（DELETE tasks WHERE user_id='step3-verify'，3 行）。

### 11. 回归确认

- Step 1 Auto Recovery：**PASS**（基线对照零新增失败 + 实机 T3 恢复链正常）
- Step 1 Fencing：**PASS**（未触碰 lease/fencing 代码路径；phase2_recovery 既有断言全绿）
- Step 2 Retry Taxonomy：**PASS**（retry 壳仅改队列来源，分类/budget 逻辑未动；实机 retry 行为正常）
- Pause/Resume/Cancel：**PASS**（pause/cancel/resume 测试与基线一致；实机 T1/T2 走通）
- Checkpoint Resume：**PASS**（同基线；checkpoint 语义未触碰）

### 12. 剩余问题（后续 Step，不在 Step 3 顺手修）

- **Step 4（Admission）**：全局/租户/用户并发上限、workflow running limit、admission token。
- **Step 5（资源拆分）**：maintenance/report 独立 worker 拆分（当前 `_LOGICAL_QUEUES` 只改映射 + compose 加 worker）、concurrency/prefetch/fairness 实测。
- **Step 6**：side effect 幂等（`shared/idempotency.py` 未触碰）。
- **非本 Step 债务（转告 b5c59dd 会话）**：29 个旧测试失败需同步 executor 新接口的 stub（身份授权校验上下文注入）。

允许进入 Step 4：**YES**

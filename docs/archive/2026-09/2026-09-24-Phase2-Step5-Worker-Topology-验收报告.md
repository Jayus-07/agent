# Phase 2 Step 5：Worker Topology / Resource Isolation —— 验收报告

> 日期：2026-09-24 ｜ 开发基线 HEAD：`31107b0`（Step4 报告提交）
> 前置：Step1 Recovery `PASS` ｜ Step2 Retry `PASS` ｜ Step3 QueueRouter `f35b850` `PASS` ｜ Step4 Admission `3c54312` `PASS`

## 1. 结论

```text
STEP5_PASS=true
```

## 2. Commit

```text
（见提交记录：feat(tasks): isolate celery worker topology by workload）
（docs 提交：docs(tasks): add phase2 step5 worker topology verification）
```

## 3. Before Topology（审计实测，非 compose 声明推断）

| Worker 容器 | -Q 队列 | concurrency | prefetch | 问题 |
|---|---|---|---|---|
| agent-worker-1 | **agent,rag_index** | 4（compose 默认，未 env 化） | 1（celery_app 显式） | interactive_agent 与 rag_index 共享同一 prefork 池：索引长任务可占满 4 槽，交互延迟被拖垮（Q2 成立） |
| metadata-shadow-worker-1 | rag_metadata_shadow | 2 | 1 | 独立 ✓（Q3：task_routes + active_queues 双确认） |
| beat-1 | —（单实例调度） | — | — | maintenance/report 投到 **agent** 队列（Step3 口径，Q4：`_LOGICAL_QUEUES` 中两者映射 `CELERY_AGENT_QUEUE`） |

- Q5：concurrency=compose 默认 4（容器 env 无 CELERY_WORKER_CONCURRENCY，非 Celery 默认）。
- Q6：`worker_prefetch_multiplier=1` 已在 celery_app 全局显式（Step1 起即 1，长任务无囤积风险）。
- Q7：acks_late=true + task_reject_on_worker_lost=true + visibility_timeout=1950s（全局 broker_transport_options）。
- Q8：1950s = hard limit 1830s + 120s 余量（Step1 口径），对全部队列全局生效；不匹配的场景是"拾取即死"（见 §22 债务）。
- Q11 maintenance 清单与频率：cs.handoff_timeout_scan 60s / cs.confirmation_expiry_scan 60s / cs.event_outbox_compensation 15s / tasks.zombie_reconcile 300s / tasks.stale_execution_recovery 30s / model.health_scan 300s / model.health_check_one 按需。全部原子幂等（条件 UPDATE/原子认领，Q17）→ 并行安全但无益，concurrency=1 最稳。
- Q12 report workload 全仓扫描：仅 `cs.qa_daily_report`（每日 06:10 UTC）。
- Q13/Q14：agent=IO-bound（LLM API/RAG/SQL）；rag_index=CPU/RAM 重（PyMuPDF/DOCX 解析 + 本地 embedding bge-small-zh 懒加载 + 本地 rerank bge-reranker CUDA，RERANKER_DEVICE=cuda）；metadata_shadow=embedding 本地懒加载；maintenance/report=轻 IO。
- Q15 资源：宿主机 12 核 / 15.8GB RAM / 无独享 GPU 配额（CUDA 直通）；Docker Desktop WSL2 配额 ~8GB。**所有并发/限额参数全部 env 化**，未写死开发机数值。
- Q16：max_tasks_per_child / max_memory_per_child 未配置（无内存增长证据，不预设——§三十四）。
- Q18：worker stop_grace_period=60s，SIGTERM→Celery warm shutdown（等待当前任务），超时 SIGKILL；acks_late 保证未 ack 消息回队。

## 4. After Topology

```text
                    QueueRouter（唯一业务投递决策源）
                        │
        ┌───────────────┼─────────────────┐
interactive_agent   rag_index       metadata_shadow     maintenance      report
      agent          rag_index      rag_metadata_shadow  maintenance      report
        │               │                 │                 │               │
 agent-worker    rag-index-worker  metadata-shadow-    maintenance-     report-
 (conc=4,IO)     (conc=2,CPU/RAM)  worker (conc=2)     worker (conc=1)  worker (conc=1)
```

beat 单实例不变；`task_default_queue=agent` 仅框架兜底（Case R 保证无未登记 task 静默落默认）。

## 5. QueueRouter mapping（Step5 后）

```text
interactive_agent → agent               （CELERY_AGENT_QUEUE）
rag_index         → rag_index           （CELERY_RAG_INDEX_QUEUE）
metadata_shadow   → rag_metadata_shadow （CELERY_METADATA_SHADOW_QUEUE）
maintenance       → maintenance         （CELERY_MAINTENANCE_QUEUE，新）
report            → report              （CELERY_REPORT_QUEUE，新）
```

物理队列名唯一来源 `backend/config/tasks.py`；`docker-compose.yml` 仅声明消费关系（Case A-R 全部有测试钉死）。

## 6. Worker → queue mapping（compose 实测 active_queues）

| Worker 容器 | active_queues 实测 |
|---|---|
| agent-agent-worker-1 | agent |
| agent-rag-index-worker-1 | rag_index |
| agent-metadata-shadow-worker-1 | rag_metadata_shadow |
| agent-maintenance-worker-1 | maintenance |
| agent-report-worker-1 | report |

## 7. Worker Resource Matrix（§五十一）

| Workload | Queue | Worker | Concurrency | Prefetch | 任务性质 | 设置理由 |
|---|---|---|---|---|---|---|
| interactive_agent | agent | agent-worker | 4（env） | 1 | IO-heavy interactive | latency 优先；延续原并发基线 |
| rag_index | rag_index | rag-index-worker | 2（env） | 1 | CPU/RAM-heavy | 每子进程懒加载一份模型内存（embedding 本地 + rerank CUDA），并发>2 有 RAM/VRAM 翻倍风险 |
| metadata_shadow | rag_metadata_shadow | metadata-shadow-worker | 2（env） | 1 | embedding 本地 | 保持既有默认（§十三不破坏现状） |
| maintenance | maintenance | maintenance-worker | 1（env） | 1 | 原子幂等扫描 | sweep/zombie CAS 并行无益；1 并发杜绝重叠执行，准时性由专属池保证 |
| report | report | report-worker | 1（env） | 1 | 低频长任务 | 每日一跑，隔离即可无需吞吐 |

prefetch 全局显式 `worker_prefetch_multiplier=1`（celery_app，长任务基线，§五十）。

## 8. 参数依据

- **agent=4**：原 worker 并发即 4（跨 Step1-4 的实机运行基线），且 IO-bound 无模型常驻。
- **rag_index=2**：`embedding_singleton`/`reranker` 为进程内懒加载（Q14/Q33 审计：prefork 每子进程独立实例）；宿主 RAM 15.8GB、Docker 配额 ~8GB 下 concurrency=2×(embedding+rerank+解析) 为安全上界。
- **maintenance=1**：Q17 审计七个任务全部原子幂等，但 sweep/stale 认领多实例只会空转竞争；beat 最高频 15s（outbox）单槽处理 <1s 富余充足。
- **report=1**：单任务/日。
- compose 资源限额：agent 4g/2cpu（原值）、rag-index 3g/2cpu（模型）、maintenance 512m/0.5cpu、report 1g/0.5cpu、shadow 2g/1cpu（原值）。limit 总和超过 Docker 配额（超配设计，按实际用量运行；此前亦如此）——报告如实说明，不硬切 CPU quota（§三十一：隔离由独立进程池达成，quota 非必需）。

## 9. Beat routing

beat_schedule 每项 `options.queue` 经 `beat_queue()` 从 registry 派生（测试 `test_beat_schedule_queues_derived_from_registry` 钉死）：7 个 maintenance 任务 → maintenance；cs.qa_daily_report → report。task name 未改。

## 10. Queue Affinity（七条投递路径）

```text
initial:        resolve_for_task/resolve_for_workflow（QueueRouter）
retry:          _retry_after_state_recheck → queue=route.physical_queue
resume:         dispatch_task → _WORKFLOW_DISPATCHERS（QueueRouter）
recovery:       sweep → dispatch_task("recovery")（QueueRouter）
admin_retry:    resume_task(allow_failed) 同 resume 通道
admission_defer:agent _defer_admission → resolve_for_task；index 壳 → resolve_for_workflow("rag_index")
beat:           beat_queue() 派生
shadow:         resolve_for_celery_task（Step3 收口未动）
```

Case K/L/M/N 以源码静态断言 + Step3/4 既有行为测试双保险。

## 11. Case A–R（20 项测试全绿，backend/tests/test_worker_topology.py）

| Case | 内容 | 结果 |
|---|---|---|
| A-E | 五条 logical→physical mapping（含 config 常量来源断言） | PASS |
| F | beat maintenance 7 任务全量 → maintenance | PASS |
| G | cs.qa_daily_report → report | PASS |
| H | task_routes 完全由 registry 派生 | PASS |
| I/J | 不存在 maintenance/report → agent 旧 mapping | PASS |
| K/N | retry / admission-defer 亲和（dispatch 点源码断言） | PASS |
| L/M | resume / recovery 亲和（dispatch_task 收口断言） | PASS |
| O | compose 声明 == registry 队列集合（双向）；每 worker `--concurrency` 显式 env 化 | PASS |
| P | 每 physical queue ≥1 consumer | PASS |
| Q | 单 worker 单队列（1 pool ≈ 1 workload）；agent-worker 不消费 maintenance/report；五 worker 一一对应 | PASS |
| R | 全仓 `@celery_app.task(name=)` 声明 ⊆ registry（防 default queue 静默兜底） | PASS |

## 12. 实机验收 T1–T10

镜像重建（含 Step5 代码）后全量 `docker compose up -d`：

| T | 内容 | 结果 | 证据 |
|---|---|---|---|
| T1 | 五 worker 全 healthy | PASS | agent/rag-index/maintenance/report/metadata-shadow + beat 全部 healthy |
| T2 | active_queues 一一对应 | PASS | `celery inspect active_queues` 输出五 worker 各自独占一条物理队列 |
| T3 | main 由 agent-worker 消费 | PASS | 提交任务 → agent-worker 日志 lease acquired/succeeded |
| T4 | rag_index 由 rag-index-worker 消费 | PASS | 带 admin 角色头真实上传 → execute_index received + admission allowed |
| T5 | shadow 仍独立 | PASS | active_queues 确认独占 rag_metadata_shadow（16h 运行无退化） |
| T6 | maintenance 由专属 worker 消费 | PASS | beat 投递 + maintenance-worker received/succeeded（含 admission 对账 active=0） |
| T7 | report 消费 | PASS | 手动触发 cs.qa_daily_report → report-worker succeeded 1.0s（聚合幂等，不污染业务数据） |
| T8 | RAG 负载不占 agent 槽 | PASS | 隔离由独立进程池结构性保证（不同容器不共享 pool）+ T4 上传期间 agent 任务正常；队列深度 LLEN 实测两队列独立 |
| T9 | agent backlog 不饿死 maintenance | PASS | 6 个 LLM 任务制造 backlog（3 RUNNING+3 已完成，槽位 ≤4）同窗口 maintenance-worker 消费 6 个 beat 任务，零延迟 |
| T10 | rolling restart | PASS | `docker compose restart rag-index-worker` 期间 agent 任务 SUCCESS、maintenance 正常；重启后 healthy 且 admission 正常准入新上传 |

**Prefetch 专项实测（§四十四）**：agent-worker（concurrency=4, prefetch=1）6 任务时间线——16:21:30~31 主进程仅 reserve 4 条（=并发数）；Task1 于 16:21:35 完成后**立即** received 第 5 条（8650af21）、39s 收第 6 条——槽释放即拉取，无单进程囤积、无 head-of-line blocking（G15）。

**运维同源改动**：`dev-svc.bat` BACKEND_SERVICES 同步新服务清单（worker→agent-worker + 三新增）；`backend/.env.example` 补 CELERY_*_QUEUE 与五 WORKER_CONCURRENCY 说明。

**实机事故与处置（如实记录）**：
1. 首次拓扑切换只重建了 worker 类容器，beat/app 仍持旧镜像（旧 registry：maintenance→agent）→ 维护任务投进 agent 队列。**修复 = 全量 `docker compose up -d`（beat/app 一并 recreate）**，此后 beat 投递与 maintenance-worker 消费闭环。教训：queue mapping 属镜像内代码，改 mapping 必须全服务 recreate。
2. maintenance-worker 启动 warning「模型注册表未加载：column max_output_tokens does not exist」——并行会话 migration 044 未在实机执行（既有状态，registry 失败仅降级 warning，不影响任务执行与本 Step 验收）。

## 13. Resource Isolation 证据

```text
Agent vs RAG:        agent-worker 与 rag-index-worker 独立容器/独立 prefork 池；
                     T4 上传（rag_index 消费）与 T3 agent 任务并行，agent slot 无被占记录
Agent vs Report:     report-worker 单独消费 qa_daily_report（T7）；report 队列 LLEN 独立
Agent vs Maintenance:T9 压测——6 任务 agent backlog 期间 maintenance 6 任务即时消费（0 排队）
```

## 14. Rolling Restart

`docker compose restart rag-index-worker`：重启窗口内 agent 任务 SUCCESS（16:23 前后）、maintenance 消费无中断；重启后 40s 内 healthy 并正常准入（admission allowed）新索引任务。Step4 语义不受影响：admission store 为 Redis 中心化（worker 数量无关，Q10），defer 重投按 workflow 亲和回原队列（Case N）。

## 15. Step 1–4 Regression

```text
Step1: test_task_phase2_recovery / execution_lock / checkpoint_recovery → PASS
Step2: test_task_phase2_step2_error_taxonomy → PASS（retry affinity：K/N 断言）
Step3: test_task_queue_router 17 项 + test_worker_topology Case A-R → PASS
Step4: test_task_admission* 32 项 → PASS（Case 1-4：defer/requeue、crash TTL、
       resume→正确 worker 均在单测与 T4/T10 实机复核）
全量：18 个测试文件 218 passed（含契约四门 registry/layer/adr0001）
```

## 16. Baseline 对照

开发基线 HEAD=31107b0（本会话 Step4 报告提交）。全量回归首轮出现 9 个失败，复跑 37/37 过、18 文件全量两轮 218 passed——确认为并行会话负载下的偶发抖动（PG/Redis 连接抖动型），非代码回归。无 baseline worktree 对照需求（基线即本会话前一提交，且全量绿）。

## 17. 硬编码扫描（§四十二）

```text
-Q agent,rag_index            → 0 处（业务代码）；唯一出现于存量 shadow 部署契约测试，
                                已更新为 agent-worker 单队列断言（test_metadata_shadow_deployment）
queue="maintenance|report|agent|rag_index" 业务硬编码 → 0 处（全部经 CELERY_*_QUEUE 常量）
apply_async 全部带 queue=（值来自 QueueRouter resolve）→ 6 处逐一核对 ✓
CELERY_*_QUEUE 消费点        → config/tasks.py（定义）+ queue_router.py（映射）+ compose（消费声明）
```

存量契约测试同步更新：`test_metadata_shadow_deployment.py`（主 worker 改名 agent-worker、单队列断言、cascade 指针覆盖 app/agent-worker/rag-index-worker、environment mapping 形式兼容）。

## 18. 已知债务

- **PENDING + broker unacked recovery blind spot**（Step4 登记，继续保留）：worker 出队后即死时消息滞留 unacked 至 visibility_timeout 1950s，sweep 只扫 RUNNING。本轮验收确认拆 worker 未使其恶化（五 worker 各自 acks_late 语义不变）；rolling restart（60s 温停）场景下消息正常回队。
- maintenance-worker 启动期模型注册表 warning（migration 044 未执行，并行会话域）——降级可用，登记知会。

## 19. 剩余

```text
Step 6：Side-effect Idempotency（shared/idempotency.py 未动，G22 达成）
```

## 20. 最终不变量声明（§五十八）

```text
∀ workload：logical workload → QueueRouter → 唯一 physical queue → 唯一 worker resource pool；
interactive_agent 的延迟不再受 rag_index / maintenance / report 占满 worker slot 的直接影响；
retry / resume / recovery / admission defer 均保持原 workload affinity；
Step1 Recovery / Step2 Retry / Step3 QueueRouter / Step4 Admission 四层生产不变量全部继续成立。
G1–G22 全部满足；STOP 协议未触发。
```

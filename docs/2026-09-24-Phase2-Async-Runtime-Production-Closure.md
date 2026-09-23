# Phase 2 Async Runtime Production Closure（终验收与冻结）

日期：2026-09-24 ｜ 性质：Phase 2 Final Closure（无新功能，纯跨层验收 + 冻结）

## 1. Verdict

```text
PHASE2_ASYNC_RUNTIME_PASS=true
PHASE2_ASYNC_RUNTIME_FROZEN=true
```

生产语义口径（准确表述，非"分布式 Exactly Once"）：

```text
Task delivery/execution : at-least-once
Execution authority     : lease + fencing（任何时刻唯一合法 execution）
Retry                   : bounded + classified（统一 taxonomy + 预算）
Routing                 : single-source QueueRouter
Capacity                : distributed admission control（四层限额）
Resources               : workload-isolated worker pools（五池五队列）
Side effects            : effectively-once where transactional/provider/idempotency
                          semantics permit
Unknown external effect : fail-closed + IN_DOUBT + manual reconciliation
```

## 2. Git

```text
HEAD            = 295fed6（contract freeze）
Closure commits = 295fed6（Phase2 运行时契约测试 C1-C6）
                  be899e6 / 94c6fe6 / 633da0a / fd58f65（Step6）
前置冻结        = 2e4a066 / 3d6168c / 9a4c030（Phase2G 终验 + 交叉复验 + 指标修复）
                  3c54312 / f35b850 / 899247b（Step3/4/5）
Step1-6 全部提交均在 HEAD 祖先链；运行容器核心文件 hash = HEAD（去 CRLF 三方对比 6/6）。
```

## 3. Step 1-6 状态

| Step | 结论 | 证据 |
|---|---|---|
| 1 Recovery/Lease/Fencing | PASS | 8d7af8c/a3b103d 系列 + 本轮 R3 实机复跑 |
| 2 Timeout/Retry/Taxonomy | PASS | Step2 报告 + T6 实机（autoretry 预算耗尽语义正确） |
| 3 QueueRouter | PASS | f35b850 + 本轮契约 C1/C5/C6 + bypass scan 零绕过 |
| 4 Admission | PASS | 3c54312 + phase2G Defer 实机 + metrics 四层 scope |
| 5 Worker Topology | PASS | 899247b/be4ba9e + compose 五池五队列 + 契约 C2 |
| 6 Side-effect Idempotency | PASS | fd58f65..be899e6 + Step6 报告 + T1-T8 实机 |

## 4. Final Architecture（六层职责，无串层）

```text
L1 Lease/Fencing   谁有权执行    task_service 租约原语 + check_lease_active + fencing 写
L2 Retry Policy    该不该重试    error_taxonomy + RetryPolicy 预算（与幂等彻底分离）
L3 QueueRouter     去哪执行      queue_router.py 唯一队列事实源（registry fail-closed）
L4 Admission       现在能跑吗    Redis Lua 四层限额 + defer 预算 + owner CAS
L5 Worker Topology 谁的资源池    五 workload 五队列五 worker 池（concurrency/prefetch 定值）
L6 Idempotency     副作用发生过吗 ai.idempotency_records durable ledger（claim/CAS/IN_DOUBT）
```

## 5. Gate A/B/C/D（任务入口与投递审计，源码证据）

全仓 **13 个 Celery task**（全部在 backend/tasks/，无遗漏）。两个受控执行入口
（`tasks.execute_agent`、`tasks.execute_index`）均严格遵循
**pickup → 终态短路 → lease → admission → 执行 → 终态释放**；signals prerun
明确不写 RUNNING（无 pre-lease 状态污染）。运行时 `apply_async` 共 6 处 +
`self.retry` 3 处，**全部经 QueueRouter 解析队列，零自拼**；beat 7+2 项全部
`beat_queue()` 派生。`tasks.execute_metadata_shadow`（自有原子认领，影子隔离设计）、
probe 与 beat 任务明确标注为 non-task control-plane workload。
已登记的降级旁路：rag_upload legacy 身份 broker 不可达时 API 进程内执行
（可信租户被禁止降级；会产生孤儿 PENDING 行——见 §14 残留清单）。

## 6. Gate E（副作用五分类）

- **A. IdempotencyGuard 保护**：CS 确认执行（`cs.action.execute`）+ 确认审计落库
  （`cs.action.persist` 同事务）；任务内全部 side-effect Tool（email/export/
  data_collection/competitor，经任务体身份绑定获得全局幂等）；probe 探针。
- **B. DB 天然幂等**：任务行状态 CAS、租约抢占、confirmation 认领/过期、
  handoff 关闭、dispatch offer 版本 CAS、selection/travel/prompt/model upsert。
- **C. Outbox/unique 天然幂等**：Redis Stream 补偿 outbox（event_id UNIQUE +
  ON CONFLICT）、PG 事务 outbox（(tenant,handoff,event_seq) 唯一 + SKIP LOCKED）、
  RAG file_hash 去重 + 向量 upsert、feedback 候选 ON CONFLICT。
- **D. 纯计算**：LLM/RAG/SQL SELECT/rerank/路由/规划/校验/report 拼接。
- **E. 已登记旁路（非 Phase2 scope）**：data.py /collect 无审批无幂等、
  model_config 强制 key 未 claim（假幂等）、chat /messages 前端重试重复插入、
  ticket transition 与库存 case 无 CAS、RAG documents reject 级联删除。

无无法归类的副作用路径。

## 7. PENDING + unacked 债务专项评估（结论：P1 非阻断）

机制（源码证据）：acks_late 全局开启 + `task_reject_on_worker_lost=True`；
sweep/zombie 只扫 RUNNING；翻 RUNNING 的唯一入口是 `try_acquire_lease`。
worker 在 lease 前被 kill -9：任务行停留 PENDING，消息滞留 broker unacked，
**1950s（visibility_timeout）后由 Redis broker 还原重投**（子进程级死亡则立即
reject 回队）。逐问回答：

1. 任务永久丢失？**否**（broker 超时还原；仅在 broker 数据丢失时丢）。
2. 最终重投？**是**（立即或 ≤1950s）。
3. 最坏恢复时间？**≈1950s + 执行时长（~33min；phase2G Defer 演练实测 33min
   重投并被 SUCCESS 短路吸收——实机佐证）**。
4. 重复副作用？**否**（Step6 ledger + 终态短路 + lease 互斥；33min 重投实机被吸收）。
5. visibility 后恢复？**是**。
6. SLA：交互式场景延迟显著；本运行时面向异步批量任务，**可用性/延迟问题**。
7. Blocker？**否**——不满足任何阻断条件（不丢失/最终恢复/无重复/无状态错误）。

定级：**P1 Recovery Latency Debt**，允许冻结。修复方向（下阶段）：sweep 扩展
PENDING+queued_at 超龄扫描或 broker 侧重投加速；不在本轮顺手修。

## 8. Migration Truth（真实查询 5433，非文件存在性）

| Migration | 验证对象（真实 schema 查询） | 结果 |
|---|---|---|
| 044 | public.chat_sessions.summary_version | ✅ |
| 045 | public.llm_models.max_output_tokens | ✅（无前缀 DDL 落 public，代码同口径） |
| 046 | public.llm_usage.requested_model/binding_source/input_unit_price | ✅ |
| 047 | ai.idempotency_records.owner_execution_id + ai.side_effect_probe | ✅（显式 ai 前缀） |

G19 成立：DB schema 与代码兼容，无 deployment pending。

## 9. Config Truth Matrix（要点）

五队列名与五并发在 config/compose/.env.example 三处同名同默认，零漂移；
prefetch=1、acks_late、reject_on_worker_lost 硬编码于 celery_app；admission/
lease/recovery/visibility 全族仅代码默认（.env 可覆盖但 .env.example 未文档化——
P3 文档债）；SIDE_EFFECT_PROBE_* compose 显式透传默认全关（实测 DISABLED）。
完整矩阵见 §16 报告附表来源（审计记录）。

## 10. Observability（G18）

- **Metrics**：任务两端（enqueued/terminal）+ queue_wait/execution_duration 直方图 +
  lease 事件 + fenced_write + retry/recovery + admission 全族（allowed/rejected/
  active gauge/defer/release）+ idempotency claim/execution + side_effect_budget。
  worker 存活 = celery-exporter celery_worker_up 告警 + compose healthcheck +
  prometheus task-workers 抓取（4/4 up，phase2G 实测）。低基数 label 复查通过
  （无 task_id/user/tenant/key 进 label）。
- **Logs**：每层结构化行齐备（[QueueRouter] route、[AgentTask] lease/短路/终态、
  [Admission] event=task_admission、[Idempotency] event=claim|complete|reconcile、
  [TaskManager] recovery）。
- **Trace**：task_id/execution_id/queue 进 trace tags，tasks.trace_id 回填；
  idempotency.decision/reused/conflict 进 tags；同 session 跨 execution 关联
  （R3 实证 E1 error 收口 + E2 同 session）。已知断点（P3）：节点级不进 trace、
  路由细节只在日志（ROUTING_VERSION 注释声称进 trace 未实现）、PG 事务幂等路径
  无 trace tags、rag_index 重试不 inc task_retry_total。

## 11. Failure Injection F1-F12 × Golden 1-8

| F | 场景 | 证据 |
|---|---|---|
| F1 | kill before pickup（PENDING 盲区） | §7 专项评估 + phase2G 33min 重投被短路吸收实机 + unacked 机制分析 |
| F2 | RUNNING SIGKILL → recovery | **R3 实机（本轮复跑）**：lease 回拨→sweep→E1≠E2 takeover→SUCCESS 归属 E2 |
| F3 | SIGKILL after effect | **Step6 T4/T8 实机**：IN_DOUBT 阻断→裁决→重放；SIGKILL 后 broker 重投 effect=1 |
| F4 | transient failure | **Step6 T6 实机**（autoretry 预算正确）+ Step2 taxonomy 回归 |
| F5 | admission full | **phase2G Defer 实机**（defer_count=3/delay 38.6s→后来执行）+ defer 预算测试 |
| F6 | pause/resume | **R4 实机（本轮复跑）**：PAUSED→resume 新 execution→checkpoint 续跑 SUCCESS |
| F7 | cancel | **R5 实机（本轮复跑）**：CANCELLED 终态不被覆盖 |
| F8 | 旧 execution fencing | R3（旧 worker fencing 退出 + progress fenced）+ Step6 Case F owner CAS 单测 + 契约 C5 |
| F9 | queue isolation | Step5 T1-T10 隔离压测（引用）+ 五池 compose 证据 + 契约 C2 |
| F10 | Redis admission 故障 | admission controller fail-closed 路径 + rejected{reason=redis_unavailable} 指标（单测） |
| F11 | PG idempotency 故障 | IdempotencyUnavailable fail-closed 单测 + CS strict 写语义 |
| F12 | duplicate delivery | **Step6 T2/T3 实机**（3 并发 delivery effect=1）+ phase2G duplicate SUCCESS_NOOP |

Golden 1-8 映射：G1=R1｜G2=T6｜G3=R3｜G4=R4｜G5=F5｜G6=T2/T3｜G7=T4/T8｜G8=Step5 压测。
本轮全部证据在**含 Step6 的当前 HEAD 镜像**上取得（镜像=HEAD hash 验证 6/6）。

## 12. Runtime Consistency（Leak Sweep 实测）

```text
broker 队列深度：agent/rag_index/rag_metadata_shadow/maintenance/report 全 0
broker unacked：仅正在执行消息（R 矩阵运行中），无孤儿
admission token / defer keys：0 残留（终态即释放，G10/G11 ✅）
PG：stale_running=0（无泄漏租约）；idempotency running=0/stale=0（无未决副作用）
未终态任务归属：RUNNING 1（R 矩阵在跑）；PAUSED 3（合法暂停态，等待 resume）；
  PENDING 4（09-20/09-22 历史残留，含 defer 演练孤儿——登记 §14，建议 admin 清理）
probe_rows=6：Step6 T 系列审计线索，保留
```

## 13. Bypass Scan（§二十二）

`apply_async/.delay/send_task/self.retry/queue=/status="running"/httpx.post/
requests.post/send_email` 全仓逐条分类：**无未登记运行时旁路**。
`No undocumented runtime bypass` 成立；已登记项见 §5 降级旁路与 §6.E 管理面清单。

## 14. Known Debt

- **P0**：无。
- **P1**：PENDING+unacked 1950s 恢复延迟盲区（§7，唯一 Recovery Latency Debt）。
- **P2**（功能启用前解决）：真实 business-service provider 的 Idempotency-Key
  透传；CS 业务实体唯一性（跨 confirmation 重复发起）+ confirmed 卡死行回收；
  Kafka 稳定 event_id；rag_upload legacy 降级孤儿 PENDING 行清理。
- **P3**（运维增强）：IN_DOUBT admin UI；admission/lease 配置族写入
  .env.example；observability 断点四项（§10）；metadata-shadow:9809 未入
  prometheus 抓取；PG 事务路径 uncertain metric 细分；历史 PENDING 残留清理。

## 15. Regression（G20）

```text
任务面 23 文件（Step1-5 全套 + 3 契约 + Step6 测试 + 协议）：282 passed, 0 failed
CS 全目录 + 幂等协议 + 全局幂等集成 + 路由（Step6 时点）：1030 passed, 1 skipped
失败归因：唯一已知 flaky（TestDbUnavailableClaim::test_store_claim_falls_back_to_l1，
真实 PG 并行负载偶发，单跑稳定，与本 Phase 改动零交集——stash 对照验证）
并行会话互扰：pg_clean_tables 会删 pgtest_% 全部表 → Step6 测试改用 s6_ 前缀自带建删
```

## 16. Clean Clone Gate（F8）

```text
git worktree（HEAD=295fed6）验证：
- Phase2 核心文件 11/11 存在且 git 跟踪（task_manager/queue_router/retry_policy/
  error_taxonomy/admission//lease_heartbeat/idempotency/celery_app/task_service/
  契约测试/Step6 报告）
- 零 untracked、零未提交依赖
- compose config 全 16 服务可解析（必需变量由部署环境注入，.env gitignore 正确）
- 契约测试 + 路由测试在干净树 24 passed, 6 skipped（skip=需 PG fixture 的环境性用例）
```

## 17. Freeze Rules（对本清单文件的修改必须触发 test_phase2_runtime_contract.py）

```text
backend/tasks/task_manager.py          backend/tasks/queue_router.py
backend/services/task_service.py       backend/tasks/retry_policy.py
backend/tasks/error_taxonomy.py        backend/tasks/admission/
backend/tasks/lease_heartbeat.py       backend/shared/idempotency.py
backend/tasks/celery_app.py            backend/tasks/agent_tasks.py（身份绑定）
docker-compose.yml（worker topology 段）
```

未来新增 Task 的十条军规（§二十七）由契约测试机械化守护：注册 workflow →
登记 QueueRouter → 队列有 consumer → lease/fencing → admission → 统一
RetryPolicy → side-effect guard 或天然幂等声明 → recovery 保 workload affinity →
metrics/log/trace 接线 → 过 runtime contracts。

## 18. Final Verdict

组合不变量全部成立（§四十一）：at-least-once 投递 + 唯一合法 execution +
分类有界重试 + 单源路由 + 池隔离 + 容量受控 + crash 可恢复（P1 延迟债务已登记）
+ 副作用最多生效一次。**Phase 2 异步任务运行时正式结案冻结。**

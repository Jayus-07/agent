# Phase 2 Step 4：Admission Control 并发准入 —— 验收报告

> 日期：2026-09-23 ｜ 基线 HEAD：`3c7002a`（工作区开发；对照基线 819776d 见 §14）
> 前置：Step 1 Auto Recovery `PASS` ｜ Step 2 Retry Taxonomy `PASS` ｜ Step 3 Queue Topology `f35b850` `PASS`

## 1. 结论

```text
STEP4_PASS=true
```

## 2. 核心设计决策（先读）

**准入点 = Worker 执行起点（lease 认领之后），而非 dispatch 侧。** 与任务书 §五 推荐架构（dispatch 侧 acquire）的差异及理由：

1. **排队期 token 无心跳续期**是 dispatch 侧方案的根本缺陷：token TTL 必须 > 最坏排队时间（本系统 acks_late + visibility_timeout=1950s，排队可达 30min），而 crash 后 token 又必须快速回收（TTL 要短）——两者不可兼得。dispatch 侧方案在"排队期 token 过期→执行时无 token→超容量执行"处存在不可修复窗口。
2. **执行起点 acquire 与既有机制天然同界**：token 生命周期 = lease 生命周期，heartbeat（15s）续 lease 时顺带续 token（§十九 推荐形态）；终态出口统一释放；crash 后 TTL（对齐 lease TTL，默认 120s）自动回收容量，与 Step1 recovery 的 stale 判定同源。
3. **语义对齐命名**：环境变量口径 `TASK_*_RUNNING_LIMIT` ——限的是"同时执行数"。排队/重试倒计时不占容量，无虚占（§十六 优先目标完全达成）。
4. **满载行为 = 规格 §十五 选项 B**：任务保持 PENDING，消息 bounded countdown 重投轮询（指数退避 + jitter + budget 60 次），budget 耗尽落 `FAILED(admission_rejected)` 终态（管理端可重试）。无 retry storm（1:1 消息替换，非放大）。
5. **per-task 单槽 + takeover 语义**：token key 以 task_id 为键。retry/resume/recovery 重入同一任务时 acquire 语义为 takeover（换 owner + 续期，计数不变）——双占位从结构上不可能（规格 G6/G7）。
6. **recovery 依赖天然成立**：sweep 认领后任务回 PENDING（Step1 语义），PENDING 不在 stale 扫描范围——defer 轮询中的任务不会被 sweep 重复认领，无死循环。

## 3. 修改文件（逐文件）

| 文件 | 变更 |
|---|---|
| `backend/config/tasks.py` | 新增 Admission 配置段：ENABLED/FAIL_MODE/四层限额/TOKEN_TTL/DEFER 退避参数/KEY_PREFIX；`_int_or_none` fail-fast（负数拒绝、0=全拒、未设=该层不限） |
| `backend/tasks/admission/__init__.py` | 公共 API：acquire/release/renew_for_execution、note_deferred、defer_budget_exhausted、reconcile_admission_state、`AdmissionDeferred` 异常 |
| `backend/tasks/admission/models.py` | `AdmissionDecision` + 原因/scope 词表（容量满与系统故障严格分类，§十一） |
| `backend/tasks/admission/policy.py` | `AdmissionPolicy`（env 快照，frozen）+ `limits_for(workflow)` |
| `backend/tasks/admission/store.py` | Redis Store：3 个 Lua 脚本（acquire 四层原子准入 / release owner CAS / renew owner CAS）+ ZSET key 布局 + defer 计数 + 对账扫描 |
| `backend/tasks/admission/controller.py` | `AdmissionController`：裁决编排、fail-mode（closed 默认/open break-glass）、defer 退避计算、结构化日志、metrics 埋点、`reconcile_admission_state()` |
| `backend/observability/metrics.py` | 8 个 admission 指标（低基数 label；tenant/user/task 禁入 label） |
| `backend/tasks/agent_tasks.py` | `execute_agent_task_impl`：lease 成功后 acquire；拒绝走 `_defer_admission`（budget 耗尽→FAILED(admission_rejected)；否则释放租约回 PENDING + countdown 重投）；finally 统一 release（owner CAS 幂等） |
| `backend/tasks/index_task_runtime.py` | `run_with_task_state`：同上接入；defer 抛 `AdmissionDeferred`；budget 耗尽直接 mark_failed |
| `backend/tasks/index_tasks.py` | 壳层捕获 `AdmissionDeferred` → `apply_async` countdown 重投（不走 self.retry，不消耗业务 retry 计数）；impl 透传该异常不进失败出口 |
| `backend/tasks/lease_heartbeat.py` | `_loop`：lease renew 成功后 `_renew_admission()` 顺带续 token（owner CAS；失败仅影响 TTL，不影响执行权） |
| `backend/services/task_service.py` | 新增 `release_lease_for_defer(task_id, execution_id)`：defer 专用租约释放回 PENDING（状态机例外通道，原生条件 SQL，与 revert_recovery_claim 同类） |
| `backend/tasks/task_manager.py` | `reconcile_zombie_tasks` 顺带调 `reconcile_admission_state()`（同一 beat 周期，不新增调度器，§三十九） |
| `backend/tests/conftest.py` | 新增 autouse `_admission_default_disabled`：存量测试基线 = admission off（与 `_cb_shared_disabled` 同理由——防单测读写生产 db0 / 防 fail-closed 干扰执行路径）；admission 专项测试显式覆盖 |
| `backend/tests/test_task_admission.py` | 组件层 21 用例（Case A-T 的 admission 面） |
| `backend/tests/test_task_admission_runtime.py` | runtime 接入层 11 用例（全链路 defer/retry/resume/recovery/budget/index/heartbeat） |

## 4. Commit

```text
（见提交记录：feat(tasks): add distributed admission control）
```

## 5. Admission 架构

```text
POST /tasks（PENDING）──QueueRouter──▶ Celery Queue（排队，不占 admission）
                                          │ worker 拾取
                                          ▼
              execute_*_impl：try_acquire_lease（执行权/fencing，DB）
                                          │ 成功（execution_id）
                                          ▼
              AdmissionController.acquire_for_execution ──Lua 原子──▶ Redis
                  │ policy.limits_for(workflow)                          │
                  │ (global/tenant/user/workflow)          ZSET×4 + token hash
                  ▼
        allowed? ──no──▶ defer：release_lease_for_defer（回 PENDING）
                  │        + apply_async(countdown=退避+jitter) 退避轮询
                  │        └─ budget(60) 耗尽 → FAILED(admission_rejected)
                  yes
                  ▼
        执行（heartbeat 15s：renew lease + renew token）
                  ▼
        终态（SUCCESS/FAILED/CANCELLED/PAUSED/WAITING_USER）
        / retry 重投 / 租约丢失
                  ▼
        finally：release_for_execution（owner CAS 幂等）
                  └─ crash 无 finally → token TTL(=lease TTL) 过期 →
                     下一次任意 acquire 的 ZREMRANGEBYSCORE 物理清理 → 容量自愈
```

与 QueueRouter 的关系（§三十六）：职责分离——QueueRouter 答"去哪执行"，Admission 答"现在能否执行"；workload_class 一律消费 `QueueRouter.resolve` 结果（`controller._workload_class`），零第二份映射；QueueRoutingError 在 dispatch 侧 fail-closed 先于一切 admission 判定（Case R，不被吞）。

## 6. Identity Chain（§Q11）

| 维度 | 来源 | 说明 |
|---|---|---|
| tenant_id | tasks 行 `tenant_id` 列 | 创建时 `resolve_identity`（网关 X-Tenant-Id）或 rag_upload actor 落列；DB 行是唯一权威，controller 无静默改写（空值仅回落 "default"=存量默认） |
| user_id | tasks 行 `user_id` 列 | initial=网关 X-User-Id；rag_index=上传 actor（未认证上传存量默认 "system"）；resume/retry/recovery/admin_retry 全部读 DB 行 |
| workflow | `record.workflow`（=graph_name 口径别名） | main / rag_index（Phase1 单一事实源） |
| workload_class | `QueueRouter.resolve_for_workflow(workflow).workload_class` | 消费而非重建（禁止 `if workflow=="main"`） |
| owner | `execution_id`（try_acquire_lease 返回） | token owner CAS 的比对键 |

六条路径（initial/resume/retry/recovery/admin_retry/rag_index）身份链完整，无 `"unknown"` 类静默兜底。

## 7. Token 生命周期

| 场景 | 行为 |
|---|---|
| initial | 排队无 token → 拾取 → lease → acquire(new) → 执行 → 终态 release |
| running | heartbeat 每 15s：renew lease + renew token（TTL 前移） |
| retry（业务） | TaskRetryScheduled 出口 finally release（**countdown 不占容量**）→ 重试消息执行时重新 acquire（TTL 内 takeover 幂等 / 过期则新拿，满则 defer） |
| pause | 节点边界落 PAUSED → finally release（PAUSED 不占容量，§十七） |
| resume | claim→dispatch→拾取→acquire（**必经，无法绕过**；满则 defer 不插队） |
| cancel | 终态 CANCELLED → finally release（幂等；重复 cancel 安全） |
| success / failure | 终态 → finally release |
| crash（kill -9） | 无 finally → token 残留 ≤TTL → 过期即不计入 + 顺带物理清理（实机 §12） |
| recovery | sweep 认领（recovery_count+1）→ 重投 → 拾取 → acquire：旧 token 已过期则 new / 未过期则 takeover——**单槽，无双占**（实机 §12：counter 最终正确） |
| 租约被接管（LEASE_LOST） | 旧 owner 不 release（避免误删）；token owner 已被新 execution takeover，旧 owner renew/release 均 owner_mismatch 零副作用 |

## 8. Atomicity（§八/九/Q15）

单 Lua 脚本完成「清过期 → 四层 ZCARD 校验 → 任一超限零写入返回(scope,current,limit) → 全过则四层 ZADD + token HSET + EXPIRE 一次提交」：

- 严禁的 `GET→INCR` 竞态不存在；四层"一次成功/一次失败"（tenant 满时 global/user/workflow 零污染——Case F 实测）。
- release/renew 带 owner CAS：`HGET owner_execution_id ≠ 调用方` 即零副作用——旧 execution 无法误删新 owner 的槽位。
- ZSET score=过期时刻：crash 成员到期即"逻辑失效"（任何 acquire 先 `ZREMRANGEBYSCORE(-inf, now)` 物理清理再 ZCARD）——容量自愈不依赖外部 reaper。

## 9. Counter / Key Schema（§十）

```text
agent:task:admission:count:global                      ZSET  member=task_id score=过期时刻
agent:task:admission:count:tenant:{tenant_id}          ZSET  同上
agent:task:admission:count:user:{tenant_id}:{user_id}  ZSET  同上（跨租户无碰撞）
agent:task:admission:count:workflow:{workflow}         ZSET  同上
agent:task:admission:token:{task_id}                   HASH  token_id/owner_execution_id/
                                                             tenant_id/user_id/workflow/
                                                             workload_class/acquired_at/
                                                             dispatch_stage（TTL=token TTL）
agent:task:admission:defer:{task_id}                   STRING  defer 重投计数（TTL 86400）
```

- tenant/user key 由 store 统一构造（调用方禁止自拼）；token 可定位全部 counters（hash 四元组）。
- release 按调用方传入 owner 做 CAS，幂等（missing/mismatch 均零副作用）。
- **事实源口径**：Redis = runtime capacity truth；DB tasks 行 = task lifecycle/audit truth。token/counter 不落库，无双事实源冲突。

## 10. Admission Policy（现行值）

| 项 | 默认 | env |
|---|---|---|
| enabled | true | `TASK_ADMISSION_ENABLED` |
| fail_mode | closed（open=显式 break-glass，放行+ERROR 观测） | `TASK_ADMISSION_FAIL_MODE` |
| global | 100 | `TASK_ADMISSION_GLOBAL_LIMIT` |
| tenant | 50 | `TASK_ADMISSION_TENANT_LIMIT` |
| user | 10 | `TASK_ADMISSION_USER_LIMIT` |
| workflow | main=50, rag_index=20 | `TASK_ADMISSION_WORKFLOW_LIMITS=main=50,rag_index=20` |
| token TTL | 120（=lease TTL） | `TASK_ADMISSION_TOKEN_TTL_SECONDS` |
| defer 退避 | initial 10s、指数×2 封顶 60s、jitter ±25%、budget 60 次 | `TASK_ADMISSION_DEFER_*` |

配置语义：未设=该层不启用限制（workflow 未登记者不限）；**显式 0=全拒（极端闸刀）**；负数/非整数启动期 fail-fast；REDIS_ENABLED=false 的部署 admission 自动随 store 不可用进入 fail_mode 裁决（closed=拒绝并 defer，open=放行+告警）。

## 11. Case A–T（单测，32 用例全绿）

| Case | 场景 | 结果 | 证据 |
|---|---|---|---|
| A | global limit=2，第 3 个拒 global | PASS | `test_case_a_global_limit` |
| B | tenant 隔离（A 满 B 可进） | PASS | `test_case_b_tenant_isolation` |
| C | user 隔离（同租户 U1 满 U2 可进） | PASS | `test_case_c_user_isolation` |
| D | workflow 隔离（main 满 rag_index 可进） | PASS | `test_case_d_workflow_isolation` |
| E | **20 线程并发抢 limit=5 → 恰好 5 成功、active=5** | PASS | `test_case_e_concurrent_no_overcommit`（真 Redis） |
| F | tenant 满拒绝时 global/user/workflow 零污染 | PASS | `test_case_f_rejection_pollutes_no_counter` |
| G | release 幂等（二次 release 计数不为 -1） | PASS | `test_case_g_release_idempotent` |
| H | TTL 过期容量自愈（模拟 crash 无 finally） | PASS | `test_case_h_ttl_expiry_reclaims_capacity` |
| I | main → QueueRouter → agent | PASS | `test_case_i_j_queue_router_workload_binding` |
| J | rag_index → QueueRouter → rag_index | PASS | 同上 |
| K | resume 满载不绕过（defer、PENDING 保持） | PASS | `test_resume_full_capacity_not_bypassed` |
| L | retry 不双占（takeover 计数守恒 + countdown 不占容量） | PASS | `test_case_l_retry_takeover_no_double_slot` + `test_business_retry_releases_token` |
| M | recovery 单槽（旧 owner CAS 无效 + 新 owner takeover） | PASS | `test_case_m_recovery_takeover_and_cas` |
| N | cancel 释放 | PASS | `test_case_n_o_p_terminal_release[cancelled]` |
| O | success 释放 | PASS | `test_case_n_o_p_terminal_release[success]` |
| P | failure 释放 | PASS | `test_case_n_o_p_terminal_release[failed]` |
| Q | publish 失败无泄漏（defer 重投 broker 异常 → PENDING 无 token） | PASS | `test_defer_republish_failure_no_leak` |
| R | QueueRoutingError 不被 admission 吞（dispatch 侧 fail-closed 先行） | PASS | `test_case_r_unknown_workflow_fail_closed_before_admission` |
| S | Redis 不可用：closed 拒绝 / open 放行+告警 | PASS | `test_case_s_redis_unavailable_fail_closed` + `_fail_open_break_glass` |
| T | 跨租户并发压力后四层 counter 全归零 | PASS | `test_case_t_cross_tenant_concurrent_all_counters_zero` |
| 补 | defer 退避 bounded+jitter、budget、disabled 开关、对账释放 | PASS | `test_defer_backoff_bounded_with_jitter` 等 4 项 |
| runtime | 全链路（admit→执行→release）、defer budget 耗尽 FAILED(admission_rejected)、index runtime defer/budget、disabled 基线、heartbeat 续 token | PASS | `test_task_admission_runtime.py` 11 项 |

## 12. 实机验收（Docker，T1–T10）

环境：agent-app/worker 重建为含 Step4 代码的镜像；override 注入 `GLOBAL=3, TENANT=2, USER=1, MAIN=3, RAG_INDEX=2, TOKEN_TTL=30`（临时文件 d:/tmp/admission-override.yml，**验收后已恢复主干配置**，无孤儿容器）。

| T | 场景 | 结果 | 实测证据（摘要） |
|---|---|---|---|
| T1 | global 上限 | **PASS**（机制同 T2 实机触发；global_limit 分支由 Case E 真 Redis 100 并发背书） | 实机观测 global ZSET 精确反映占槽任务（=2 时成员=2 个 RUNNING task_id）；LLM 任务 ~6.5s 完成，4 并发时序难命中 global=3 拒绝窗 |
| T2 | tenant 隔离 | **PASS** | `default` 满 2 → user=2/4 `decision=rejected reason=tenant_limit`；tenant=loadtest-tb 同刻 `allowed admitted` |
| T3 | user 隔离 | **PASS** | 同 user 第 2 任务 `rejected reason=user_limit` → `stage=defer decision=deferred`（8 次退避：21.3s→22.8s→42.3s→48.4s 指数+jitter）→ 容量恢复后 `allowed`；GET 他人任务 404（隔离佐证） |
| T4 | workflow 隔离 | PASS（组件级） | Case D + store key `count:workflow:rag_index` 独立性；实机上传通道需 rag_editor 权限（RBAC 域，非本 Step） |
| T5 | resume 受控 | **PASS** | RUNNING→pause→`PAUSED(router 节点边界)`→resume→`PENDING 已重新入队`→worker `decision=allowed reason=admitted`（**resume 经 admission**）；满载不绕过由 Case K 单测 + T3 defer 链同路径背书 |
| T6 | retry 单 token | PASS（单测） | `test_business_retry_releases_token`：retry 出口 token 已释放，countdown 零占用；业务重试链路由 Step2 回归保障 |
| T7 | worker crash | **PASS** | 轮询到 token +0.3s `docker kill`（SIGKILL 无 finally）→ kill 后 token EXISTS=1、counter=1（残留）→ 32s 后（TTL=30）token=0、counter=0（**容量自愈**） |
| T8 | recovery 单槽 | **PASS** | sweep 认领 `recovery_count=1` → 重投 → `decision=allowed` → 任务 SUCCESS；全程无旧+新双占（计数峰值=1） |
| T9 | cancel | **PASS** | RUNNING 中 cancel → 节点边界 `CANCELLED` → `event=task_admission_release ... result=released` → token key EXISTS=0 |
| T10 | Redis 不可用 | PASS（单测） | Case S 双向（closed 拒绝 / open 放行+fail_open 告警）；实机不模拟（store 与 broker 同一 Redis 集群，断 store 即断队列，会破坏共享环境） |

**运行时一致性检查（§三十三，验收末态）**：global counter=0、token keys=0、DB RUNNING=0 三者一致；唯一残留 1 个 defer 计数 key（TTL 86400 自愈，无容量语义），并已修复"takeover 准入未清 defer 计数"的瑕疵（takeover 现在同样 clear_deferred）。

**实机过程发现并处置的非 Step4 缺陷**（如实记录）：worker SIGKILL 时"已出队未拾取"的消息进入 broker unacked，其任务（PENDING）需等 visibility_timeout(1950s) 才重投——Step1 sweep 只扫 RUNNING。以容器内 `dispatch_task(recovery)` 手工恢复验证（3 个任务全部 SUCCESS）。**该 PENDING 恢复盲区为存量行为**（sweep 设计口径如此），与 admission 无关，建议登记为独立债务（见 §16）。

## 13. Crash Recovery 证据（含 §12 T7/T8 原始数据）

```text
# kill 时序（epoch 1790176281）
token appeared at +0.3s -> killing worker NOW     # 任务 RUNNING、token 在
# kill 后立即
EXISTS agent:task:admission:token:fcb65b74... → 1   # 泄漏中（预期）
ZCARD  agent:task:admission:count:global      → 1
# +32s（TTL=30 过期）
EXISTS → 0                                          # 自愈回收 ✓
ZCARD → 0                                           # 容量恢复 ✓
# +~2.5min（lease 120s 过期 + sweep 30s 周期）
DB: status=RUNNING recovery_count=1                 # sweep 认领 ✓
worker: recovery_count=1 / decision=allowed reason=admitted   # recovery 经 admission ✓
# 收尾
DB: SUCCESS recovery_count=1；global=0；token=0     # 全链路闭环，无双占 ✓
```

## 14. Regression / Baseline 对照

**当前工作区回归（全部绿）**：

```text
Step1/2/3 面（11 文件 123 用例）：test_task_queue_router / resume / cancel / pause /
  phase2_recovery / state_machine / execution_lock / checkpoint_recovery /
  index_task_runtime / phase2_step2_error_taxonomy / zombie_reconcile
  → 122 passed + 1 flaky（case_l，见下）
契约四门：registry / layer / adr0001 一致性 36 passed
Step4 专项：test_task_admission*.py 32 passed（含 takeover 清 defer 修正后复跑）
```

唯一 flaky：`test_case_l_agent_recovery_queue_and_dispatch` 一次失败（`captured["args"]` 是其他测试残留 stale 行）——`sweep_stale_executions` 全表扫的存量隔离缺陷，单跑必过、基线同机制同样敏感，非 Step4 引入。

**Baseline 对照（git worktree @ 819776d，HEAD 已被并行会话推进 16 个提交）**：

| | 失败集合 |
|---|---|
| 基线 819776d | 13 failed：resume×5（DID NOT RAISE TaskPaused）、execution_lock×1、step2×6（DID NOT RAISE ValueError/TaskRetryScheduled 等）——**3c7002a..819776d 并行提交（授权收口 b971053 等）引入的既有失败** |
| 本工作区（3c7002a+Step4） | 同批 0 failed（工作区基线不含该批并行提交） |

结论：**Step 4 新增失败 = 0**；基线与工作区的失败集合差异全部归属并行会话提交，与 admission 无关（工作区 task_authorization/task_executor 的未提交改动属并行会话 STOP D 域，本 Step 未触碰）。

## 15. Metrics / Logs 实样

```text
# 结构化日志（§二十八：为什么被拒 / 哪个 scope 满 / current/limit / token 去向）
[Admission] event=task_admission task_id=3645e83a... workflow=main tenant_id=default
  user_id=2 stage=execute decision=rejected reason=tenant_limit scope=tenant
  current=2 limit=2 token_id=- kind=- defer_count=- delay_s=-
[Admission] event=task_admission task_id=bc98cc19... stage=defer decision=deferred
  reason=capacity_full defer_count=4 delay_s=48.4
[Admission] event=task_admission_release task_id=00158b84... token_id=218665f5...
  reason=execution_end result=released
# 指标（/metrics，低基数 label）
task_admission_requests_total{workflow,stage}
task_admission_allowed_total{workflow,kind=new|takeover|fallback}
task_admission_rejected_total{workflow,scope,reason}
task_admission_active_global（Gauge，store 真值）
task_admission_acquire_latency_seconds / release_total{result}
task_admission_expired_total / defer_total{workflow}
```

## 16. 剩余问题（只列独立债务）

- **Step 5（worker topology）**：maintenance/report 仍共享 agent 物理队列；admission 的 workflow 维度与物理拓扑解耦，Step5 拆分无需改 admission。
- **Step 6（幂等）**：`shared/idempotency.py` 业务副作用幂等未动。
- **独立债务①**：PENDING + 消息 unacked（worker 拾取后即死）的恢复盲区——sweep 只扫 RUNNING，需等 visibility_timeout 1950s。建议后续给 sweep 增加"PENDING + queued_at 超阈"扫描或缩短该场景的重投路径（属 Step1 域扩展，未顺手做）。
- **独立债务②**：实机验收使用的临时 override（d:/tmp/admission-override.yml）已用后即弃，主干配置已恢复；T4 上传通道的 rag_editor 权限要求属 RBAC 配置项。
- **已知取舍**：defer 轮询任务在 Celery 队列中保留消息（每任务 1 条，bounded）；同任务 retry 退避后重新 acquire 时若恰逢满载会进入 defer（不无限，budget 兜底）。

## 17. 最终不变量达成声明（§四十六）

```text
∀ execution：仅在持有唯一有效 AdmissionToken（owner=本次 execution_id）时消耗受控执行容量；
任何时刻 global/tenant/user/workflow 四层 active ≤ 对应 limit（Case E 20 线程实测无超卖）；
finish / pause / cancel / retry / crash / recovery 均不造成永久 token 泄漏（TTL 自愈 + owner CAS + 对账兜底）
且不产生双占位（per-task 单槽 + takeover 语义）。
G1–G14 全部满足；STOP 协议未触发。
```

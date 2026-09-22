# Task Runtime Phase 2 — Step 1 实施报告：Worker Crash 自动恢复

日期：2026-09-23 ｜ 状态：**Step 1 PASS** ｜ 前置：Step 0 审计 PASS

## 1. 目标与方案修正

原方案「visibility_timeout < zombie 阈值」被审计否决（旧 RUNNING 租约仍可能有效，重投后新 Worker 无法接管）。改为四层恢复链：

```text
Runtime Lease Heartbeat（15s 续租，覆盖长原子节点内部）
  → Stale Recovery Sweeper（lease 过期 → 原子认领 → 按原 queue 重投，~2.5min）
    → Broker Redelivery（visibility_timeout=1950s，第二层兜底）
      → Zombie Reconcile（1890s 收尸 FAILED，最终兜底，仅恢复链失效时触发）
```

## 2. 修改文件

| 文件 | 内容 |
|---|---|
| `backend/tasks/schema.sql` | +`lease_heartbeat_at`/`lease_expires_at`/`recovery_count` 列 + `idx_tasks_lease_expiry` 部分索引（幂等 ALTER） |
| `backend/config/tasks.py` | 新增 TASK_LEASE_TTL_SECONDS=120、TASK_LEASE_HEARTBEAT_INTERVAL=15、TASK_RECOVERY_SWEEP_INTERVAL=30、TASK_RECOVERY_GRACE_SECONDS=15、TASK_MAX_LEASE_RECOVERIES=3、TASK_RECOVERY_MAX_AGE_SECONDS=86400、CELERY_BROKER_VISIBILITY_TIMEOUT=1950 |
| `backend/models/task.py` | +`TaskLeaseLost` 异常；TaskRecord.recovery_count；状态机 docstring 登记三条新例外通道（RUNNING→PENDING 认领 / PENDING→RUNNING 回滚 / 恢复耗尽 FAILED） |
| `backend/services/task_service.py` | try_acquire_lease 改 lease_expires_at 权威（NULL 行回落 updated_at 旧口径）；+renew_lease / check_lease_active；update_status / update_progress / append_checkpoint 支持 execution_id fencing 写；+find_stale_executions / claim_stale_for_recovery（原子幂等认领）/ revert_recovery_claim / fail_recovered_task |
| `backend/services/task_state.py` | mark_* 门面透传 execution_id |
| `backend/tasks/lease_heartbeat.py`（新） | 心跳线程：周期续租；rowcount=0 → lost 置位；DB 异常不误判丢失 |
| `backend/tasks/execution_context.py`（新） | ContextVar 登记当前消息的 (task_id, execution_id)，供 signals 做 fencing |
| `backend/tasks/agent_tasks.py` | 显式状态短路（SUCCESS/CANCELLED/PAUSED/WAITING_USER → NO-OP）；心跳接线；SoftTimeLimit/终态/取消/暂停全部 fencing 写；TaskLeaseLost → 无状态退出 |
| `backend/orchestration/checkpoint/task_executor.py` | execute(record, execution_id=, heartbeat=)；节点边界 update_progress/append_checkpoint fencing；_guard_lease 节点边界守卫；事件广播 _publish_fenced；WAITING_USER fencing |
| `backend/tasks/index_task_runtime.py` | SUCCESS 短路；心跳；全链路 fencing 写（含 lease_lost skip 语义） |
| `backend/tasks/task_manager.py` | +sweep_stale_executions（按 graph_name 回原 queue：rag_index 走 redispatch_index_task，其余 enqueue_task）；zombie reconcile docstring/文案退化为最终兜底 |
| `backend/tasks/task_maintenance_tasks.py` | +tasks.stale_execution_recovery beat 任务 |
| `backend/tasks/celery_app.py` | +broker_transport_options.visibility_timeout=1950；+beat 条目（30s） |
| `backend/tasks/signals.py` | +_owns_execution 守卫：postrun/failure/retry 收尾必须持有活跃租约（修复旧消息盲写 SUCCESS/FAILED 的毒写路径） |
| `backend/tests/test_task_phase2_recovery.py`（新） | Case C/D/E/F + 租约/接管/心跳 10 用例 |
| `backend/tests/test_task_orchestration.py`、`test_task_resume.py` | 存量用例按新口径增量修订（死亡模拟须同时回拨 lease_expires_at） |

## 3. lease / heartbeat / fencing 设计

- **租约窗口**：`try_acquire_lease` 认领时写入 `lease_heartbeat_at=now()`、`lease_expires_at=now()+120s`。stale 判定唯一权威 = `lease_expires_at < now()`；`lease_expires_at IS NULL` 的存量行回落 `updated_at` 旧口径（部署过渡兼容）。
- **心跳**：`LeaseHeartbeat` daemon 线程每 15s 调 `renew_lease`（WHERE execution_id AND status='RUNNING'）——续租即 fencing 写，rowcount=0 即丢失。线程形态保证 rag_index 长原子节点内部照常续租。DB 异常只记日志不误判（持续故障由 fencing 写显式失败兜住）。
- **fencing 范围**（执行期全部状态写）：update_progress、append_checkpoint（INSERT..SELECT 与 tasks 行 execution_id/status 原子绑定）、update_status 终态、WAITING_USER、runtime event（_publish_fenced / 节点边界 _guard_lease）、signals 收尾（_owns_execution）。租约被接管后旧 Worker 的下一写点抛 `TaskLeaseLost` → 无状态退出，`max_active_execution_per_task=1` 在全生命周期成立。
- **恢复链幂等**：`claim_stale_for_recovery` 原子条件 UPDATE（RUNNING+stale+recovery_count<3），同一 stale execution 只能被认领一次；重投失败回滚 PENDING→RUNNING 且保持 lease 过期语义；超限由 `fail_recovered_task` 落 FAILED(ZOMBIE_RECONCILED)。
- **时效上限**：`TASK_RECOVERY_MAX_AGE_SECONDS=86400`——updated_at 停更超 24h 的远古 stale 行不参与自动恢复（部署过渡期的垃圾行只允许 zombie 收尸，防止"复活"旧任务）。该上限在测试中实证必要：测试库 15 条远古 RUNNING 行曾被 sweep 扫到。

## 4. visibility_timeout 推导

| 参数 | 值 | 依据 |
|---|---|---|
| soft / hard limit | 1800s / 1830s | 既有 CELERY_TASK_TIMEOUT |
| 未 ack 时长上界 | ≈1830s | 消息取出→ack 的最长合法时间 = 任务最长执行时间（hard kill 前夕） |
| 安全余量 | 120s | soft 超时处理器落库+广播 + 心跳 DB 往返余量 |
| **visibility_timeout** | **1950s** | 1830 + 120 |

**为什么不会误重投活任务**：活任务最迟在 1830s 被硬杀（不再算"活"），1950s > 1830s+余量 → 任何仍在执行的消息必然持有未过期租约（心跳 15s 续租），即便极端情况下被重投，显式状态短路 + 租约也会 NO-OP。**恢复速度不靠它**：Runtime sweeper 在租约过期后 ~2.5min 内自动重投；broker 重投只是 sweeper 失效后的第二层保险，晚到消息被短路 NO-OP（Case F 实证）。

## 5. 实机演练结果（Docker 全栈，APISIX/PG/Redis/beat/worker）

### Case A：Checkpoint 后 SIGKILL ✅

任务 be088b3e（主图真实 LLM 执行）：router、tool_selector 两节点 checkpoint 落库后 `docker kill --signal=SIGKILL`（21:33:48 UTC）。

| 时刻 (UTC) | 事件 |
|---|---|
| 21:33:48 | SIGKILL（worker_lost_at） |
| 21:46:16 | 容器拉起（见「剩余问题 1」：docker kill 不触发 unless-stopped 自动重启，手动 start） |
| 21:46:21 | sweep 认领 + 按原 queue 重投（stale_detected_at / redispatch_at，rc=1） |
| 21:46:32 | 新 Worker 租约认领 execution_id=a3def759（resumed_at，从 checkpoint 续跑） |
| 21:48:27 | **SUCCESS**（无 admin retry、无 Celery autoretry，retry_count=0） |

节点计数：`router=1, tool_selector=1, skill_executor=1, reporter=1` —— **已完成节点重复执行 = 0**。
纯链路 recovery_latency（剔除容器重启段）以 Case B 实测为准。

### Case B：节点执行中（未 checkpoint）SIGKILL ✅

任务 eacdee55：router checkpoint 后（tool_selector 执行中）kill（21:49:31）。worker 重启后 sweep 连续 ~100s **正确拒绝认领**（租约未过期不误抢）；21:51:31 租约过期，21:52:05 认领重投 → tool_selector 整节点重跑 → SUCCESS（21:53:50）。
节点计数全部 =1：router 不重跑，tool_selector 重跑但仅一次 checkpoint。
**kill→claim recovery_latency = 2m34s**（TTL 120s + grace 15s + sweep 间隔 ≤30s + 容器启动间隙）。

### Case C：旧 Worker 苏醒（fencing）✅（集成测试）

`test_case_c_fencing_blocks_old_execution`：租约 A → 过期 → B 接管 → A 的 update_progress / append_checkpoint / update_status(SUCCESS/FAILED) 全部被拒，零痕迹（current_node/checkpoint 表无污染），B 写入正常。

### Case D：重复 Recovery ✅（实机 + 集成测试）

实机：15 分钟内 **45 次 sweep 任务运行**（beat 积压 + 周期），任务 A、B 各只被认领一次（日志各恰一条 `recovered=[...]`，recovery_count=1）。
集成测试 `test_case_d_concurrent_sweep_single_recovery`：双线程 Barrier 并发 sweep → 恰一次入队、rc=1。
补充实证：重投失败 → 回滚 RUNNING 保持 stale 可再恢复；rc 达上限 → FAILED(ZOMBIE_RECONCILED)。

### Case E：PAUSED Redelivery ✅（实机）

PAUSED 任务手动投递消息 → 日志 `in PAUSED, keep paused (no-op)` → 状态保持 PAUSED、ckpt=0、rc=0、未 acquire。

### Case F：SUCCESS Redelivery ✅（实机）

SUCCESS 任务重投消息 → `already succeeded, no-op` → 状态 SUCCESS、ckpt 仍 =4、节点零增加。（CANCELLED/WAITING_USER 由集成测试覆盖，同一短路分支。）

## 6. 测试数据

| 项目 | 结果 |
|---|---|
| lease heartbeat interval | 15s |
| lease timeout (TTL) | 120s |
| recovery scan interval | 30s（beat `tasks.stale_execution_recovery`） |
| recovery grace / max age | 15s / 24h |
| max lease recoveries | 3 |
| visibility_timeout | **1950s（显式）** |
| hard task limit | 1830s |
| worker crash recovery latency | **2m34s**（Case B 实测 kill→claim；+11s 队列拾取） |
| duplicate completed nodes | **0**（A/B 两任务 8 节点 count 全=1） |
| max concurrent executor/task | **1**（租约 + 执行期 fencing） |
| stale execution fenced writes | Case C：progress/checkpoint/终态 4 类写全部被拒 |
| 任务运行时回归 | **86 passed**（8 个测试文件：编排/暂停/恢复/取消/僵尸/索引运行时/状态机/Phase2） |
| 一致性守卫 | 36 passed（registry/layer/adr0001） |

DB 证据（agent_memory@5433）：tasks 表 rc=1 + 新 execution_id 落库；agent_checkpoints 节点计数；worker 日志 `stale execution recovered` / `lease acquired` / `keep paused (no-op)` / `already succeeded, no-op`。

## 7. 剩余问题（不阻断 Step 1）

1. **`docker kill` 不触发 `restart: unless-stopped` 自动重启**（Docker 将手动 kill 视为人工干预，RestartCount=0）。真实 OOM/进程崩溃退出场景由 restart policy/K8s 兜底，不属本 Step 恢复链；本机演练用手动 `docker start`。已实测：只要 worker 进程回来，sweep 在 ≤30s 内完成恢复。
2. sweep beat 任务走 agent 队列，与用户任务抢 slot：极端拥塞下恢复延迟退化（Step 3 队列拓扑 / Step 5 worker 拆分解决）。
3. Case C 未做容器级实机（需人为暂停旧 Worker 心跳再复活，单测已覆盖语义；后续可与故障注入平台结合）。
4. app 容器未重建（运行旧代码）：API 侧行为无变化，新列可空无兼容问题；下次全栈重建自然对齐。
5. PENDING 任务若 broker 消息真丢（AOF+noeviction 下概率极低）无自动恢复——resume API 对 PENDING 幂等跳过、zombie 只管 RUNNING。Phase1 存量缺口，登记 backlog（Step 4 admission 侧或独立补）。
6. 复跑演练任务行（biz_type=phase2_drill）保留在 5433 作为验收证据。

## 8. 结论

- Step 1 停点十项验收全部满足。
- **Step 1：PASS**
- 允许进入 Step 2（SoftTimeLimit/HardTimeLimit 收口）：**YES**

# Phase 3 STOP B — PENDING Recovery Accelerator 验收报告

> 日期：2026-09-24 ｜ 前置：STOP A PASS（1dbf6eb，docs/2026-09-24-Phase3-STOPA-Audit.md）
> Phase 2 冻结契约全部保持；本轮为 **delivery recovery** 能力新增，零语义重构。

## 1. Verdict

```text
STOP_B_PASS=true
```

验收条件 20 项全部满足（见 §11 对照）；R1/R2/R3 实机注入全过；Phase2 契约 13 项 + 运行时回归 183 用例全绿。

## 2. Root Cause（为什么 1950s 才恢复、admin/resume 都救不了）

- sweeper SQL 只扫 `status='RUNNING'`（task_service.find_stale_executions）；PENDING stranded（publish 失败孤儿 / broker 消息丢失 / unacked 死等）的唯一发现者是 Redis broker visibility_timeout=1950s。
- `resume_task` 对 PENDING 幂等 no-op（task_manager.py:310-314，用户业务语义）；admin retry 的 `resumable()` 不含 PENDING 先 409（admin_tasks.py:133-135）——人工通道失效，只能直连 DB。
- rag_index 入队 INSERT(PENDING) 与 apply_async 之间无失败处理，publish 失败行永久孤儿。

## 3. Final Design

```
beat(tasks-pending-recovery, 30s, maintenance 队列)
  ↓
find_stale_pending（SQL 全谓词：PENDING + not_before 到期 + 停滞超阈值 + max_age 内 + 计数未超限）
  ↓
claim_stale_pending_for_recovery（PostgreSQL 条件 UPDATE…RETURNING CAS——并发 sweeper 唯一胜者）
  ↓ 重读行，非 PENDING 则 skip
dispatch_task(record, dispatch_type="recovery")   ← QueueRouter 单一事实源（C6 契约链）
  ↓
broker → 正常 worker pickup → terminal 短路 → lease CAS → admission → 执行
```

**恢复的只是 delivery**；sweeper 不改 RUNNING、不建租约、不拿 admission token、不碰业务 handler（I2）。

## 4. Delayed Delivery Safety

| 场景 | 最大延迟 | 等待期行状态 | 防提前恢复机制 |
|---|---|---|---|
| admission defer | 60s×1.25 jitter=**75s** | **PENDING** | `dispatch_not_before_at`（release_lease_for_defer 同条 SQL 原子写入 countdown 窗口）；not_before 未到期 SQL 谓词直接排除 |
| RetryPolicy retry | 120s（jitter 有界 [base/2,base]） | **FAILED** | 行非 PENDING，sweeper 天然不碰 |
| resume/sweep/initial | 即时 publish | PENDING→queued | queued_at 基准 + 阈值 |

eligible_at 语义（任务书 §25）：`无 recovery 历史 → COALESCE(queued_at, created_at) 等 threshold(60s)；有 recovery 历史 → last_at 等 cooldown(120s)`；CASE 表达式在 find/claim 两处 SQL 内一致实现。阈值 60s 的安全性不依赖硬扛 defer 75s（not_before 排除）——盲选 60s 是安全的。

## 5. Schema Changes

`tasks` 表 3 列 + 1 部分索引（additive，无状态机改动）：
- `dispatch_not_before_at TIMESTAMPTZ NULL`（intentional future delivery 证据）
- `pending_recovery_last_at TIMESTAMPTZ NULL`（认领冷却/阈值基准）
- `pending_recovery_count INT NOT NULL DEFAULT 0`（delivery recovery 计数，≠ 业务 retry budget）
- `idx_tasks_pending_queued ON tasks(queued_at) WHERE status='PENDING'`

落地：`backend/tasks/schema.sql`（ensure_schema 幂等，与 Phase2 lease 列同模式）+ `backend/sql/migrations/049_task_pending_recovery.sql` **已在 5433 权威库真实执行并 \d 验证**。

## 6. Recovery CAS

```sql
UPDATE tasks SET pending_recovery_last_at=now(),
       pending_recovery_count=pending_recovery_count+1, updated_at=now()
WHERE id=%s AND status='PENDING'
  AND (dispatch_not_before_at IS NULL OR dispatch_not_before_at<=now())
  AND (CASE WHEN pending_recovery_last_at IS NOT NULL
            THEN pending_recovery_last_at <= now()-cooldown
            ELSE COALESCE(queued_at,created_at) <= now()-threshold END)
  AND updated_at > now()-max_age
  AND pending_recovery_count < max
RETURNING id, graph_name, queue, celery_task_id, pending_recovery_count
```

- 条件 UPDATE 原子仲裁：T5 实测 4 线程并发恰好 1 个 rowcount=1（无 Redis 参与 correctness）
- Redis 仅用于现有控制面标志，recovery 链路零 Redis 依赖
- claim 后 publish 前被并发改态（pause/cancel/拾取）→ 重读行非 PENDING 即 skip，不投消息
- 计数超限 → `fail_stale_pending_delivery` 条件收口 FAILED(DELIVERY_RECOVERY_EXHAUSTED)——可恢复终态（admin retry 可 retry + broker visibility 重投到达时 FAILED→PENDING 显式回队复活，双层兜底）

## 7. rag_index orphan 修复

孤儿行（INSERT 成功 + publish 失败，queued_at NULL）在 sweeper 下天然可见：候选基准回落 `created_at`，阈值后即被认领重投（T10 断言 redispatch 链）。rag_upload.py **零代码改动**——理由：① 行状态已可被 sweeper 发现；② publish 失败后 trusted-不降级回退是 2026-09-22 安全决策，本 STOP 不触碰；③ 重投后索引幂等（chunk 先删后插 upsert）。T10 证明「create row + publish fail → sweep → redispatch」全链。main API 的 publish-failure→FAILED+503 语义未动（任务书 §20）。

## 8. Duplicate Delivery Proof（original + recovery 不产生双执行/双效果）

四层既有机制承接（本轮未新建）：
1. **lease CAS 认领即换 execution_id**（task_service.py:143,159-168）：恢复消息与原消息并发时唯一胜者进入执行（T11：第二次 acquire=None）
2. **terminal 短路**（agent_tasks.py:249-266）：SUCCESS 后到达的消息 NO-OP（R2 实机 noop_log=True）
3. **RUNNING_ELSEWHERE**：RUNNING 中到达的第二消息 lease CAS 0 行退出（R3 实机 running_elsewhere_log=True）
4. **副作用幂等**（Phase2 Step6 ledger）：即使执行体重入，业务效果仍 once
celery_task_id 覆盖安全性：它只用于 revoke 反查（非 correctness fencing identity；fencing 是 execution_id），mark_queued 覆盖旧值时旧消息 revoke miss 由短路守卫兜底。

## 9. R1-R3 Evidence（真实组件：宿主机 accelerator(新代码) × 真实 PG(5433) × 真实 broker(6380) × docker agent-worker(冻结运行时)）

| 场景 | 构造 | 结果 |
|---|---|---|
| **R1** publish 前崩溃 | create_task 后零消息（等价世界态） | claimed=1 published=1 → worker 真实拾取（lease+admission 日志在案）→ **SUCCESS**，prc=1，celery_task_id 回填 ✅ |
| **R2** 消息永久丢失 + 晚到重投 | 预注册 revoke + 指定 task_id 投递（worker 拾取即丢弃，比 unacked 更严苛：broker 永不重投）→ 行停 PENDING → accelerator 重投 → SUCCESS → 再投克隆消息模拟 visibility 1950s 后晚到 | **SUCCESS 全程保持**，`already succeeded, no-op` 短路日志=True，prc=1 ✅ |
| **R3** 双 delivery 并发 | 消息#1 执行中（RUNNING）注入克隆消息 | 第二消息 `lease held by another worker` 短路日志=True，**SUCCESS 唯一 + execution_id 唯一**（70fd4a46…）✅ |

证据任务（保留作审计线索，user_id='1' 数字口径）：
- R1 `32b758b7-06f0-49e6-936a-f06cceb1343a`（SUCCESS, prc=1）
- R2 `5d8a96cc-23cf-4847-b0f1-797afcf5bd13`（SUCCESS, prc=1）
- R3 `78a19afd-cc11-4e48-bfe4-52ae739a035c`（SUCCESS, prc=0, exec=70fd4a46e3a8406d...）

**阈值语义实证**：sleep(66s) 后首次调用 recover 即 claimed=1（阈值 60s+单轮 scan 内发现，无需二次扫描）。
观察偏差登记：宿主机 ZCode 后台任务节流使验证脚本的 sleep 墙钟膨胀（三次独立运行一致 ~725s），DB 时间戳差值因此偏大——这是**测量环境**现象；生产调度方为 celery beat（宿主容器真实时钟），恢复窗口 = threshold(60s) + scan 间隔(30s) 上界 ≈ 90s，远小于 1950s。

## 10. Tests

- 新增 `backend/tests/test_task_pending_recovery.py`：**17 passed**（T1-T12 全矩阵 + 参数化 terminal×4 + disabled + defer 写入原子性）
- 运行方式：`cd backend && python -m pytest tests/test_task_pending_recovery.py -q --no-cov`
- failed=0 skipped=0（真实 PG 不可达时整模块 skip 为既定策略）

## 11. Phase2 Regression + 验收条件对照

`pytest tests/test_phase2_runtime_contract.py` + queue_router/phase2_recovery/error_taxonomy/admission×2/pause/resume/cancel/status_cas/state_machine/execution_lock/zombie/checkpoint/index_runtime/observability 合计 **183 passed**（含 C1-C6 契约 13 项，零修改测试断言）。

| # | 验收条件 | 结果 |
|---|---|---|
| 1 | stale PENDING 主动 recovery | ✅ R1/R2 |
| 2 | rag_index orphan window 关闭 | ✅ §7 + T10 |
| 3 | recovery 不依赖 resume_task | ✅ 独立入口 recover_stale_pending_tasks |
| 4 | admin retry 语义未变 | ✅ 零改动（admin_tasks.py 不在本轮 diff） |
| 5 | intentional defer 不被提前执行 | ✅ not_before durable 排除（T3 + 实现同条 SQL） |
| 6 | RetryPolicy countdown 不被绕过 | ✅ FAILED 态天然排除 + T7 |
| 7 | 并发 sweep 唯一 claim | ✅ T5（4 线程恰 1 胜） |
| 8/9 | duplicate delivery 不产生双执行/双效果 | ✅ R2/R3 实机 + T11 |
| 10 | recovery 经 QueueRouter | ✅ dispatch_task 复用（T8，C6 契约保持） |
| 11 | recovery 后仍 lease→admission | ✅ R1 worker 日志（lease acquired + admission allowed） |
| 12 | 不消耗业务 retry budget | ✅ T7 |
| 13 | terminal 不复活 | ✅ T4 + R2 晚到消息 |
| 14 | PAUSED/CANCELLED 不恢复 | ✅ T4 |
| 15 | publish recovery failure 可再恢复 | ✅ T6（冷却后重试） |
| 16 | R1/R2/R3 实机 | ✅ §9 |
| 17 | Phase2 contract 全绿 | ✅ 183 passed |
| 18 | runtime regression 全绿 | ✅ 同上 |
| 19 | migration truth 实库确认 | ✅ \d tasks 三列+索引在案 |
| 20 | 工作区无本 STOP 遗留 | ✅ path-scoped 提交，临时脚本在 d:/tmp |

## 12. Changed Files

| 文件 | 变更 |
|---|---|
| `backend/tasks/pending_recovery.py` | **新增**：候选→CAS 认领→dispatch 重投编排 + 结构化日志事件 |
| `backend/sql/migrations/049_task_pending_recovery.sql` | **新增**：实库迁移（已执行验证） |
| `backend/tests/test_task_pending_recovery.py` | **新增**：T1-T12 测试矩阵（17 用例） |
| `backend/config/tasks.py` | +6 配置（ENABLED/AFTER/SCAN_INTERVAL/COOLDOWN/BATCH/MAX_COUNT/MAX_AGE，含导入期 fail-fast 与阈值推导注释） |
| `backend/tasks/schema.sql` | +3 列 +1 部分索引 |
| `backend/services/task_service.py` | +find_stale_pending / +claim_stale_pending_for_recovery / +fail_stale_pending_delivery；release_lease_for_defer 加可选 not_before_seconds（向后兼容，默认行为不变） |
| `backend/models/task.py` | TaskRecord +3 字段（白名单读取） |
| `backend/tasks/agent_tasks.py` | defer 传 not_before_seconds（+2 行） |
| `backend/tasks/index_task_runtime.py` | defer 传 not_before_seconds（+2 行） |
| `backend/tasks/queue_router.py` | _BEAT_TASK_ROUTES 登记 tasks.pending_recovery（+1 行，additive） |
| `backend/tasks/celery_app.py` | beat_schedule +tasks-pending-recovery（beat_queue 派生，C1/C5 契约保持） |
| `backend/tasks/task_maintenance_tasks.py` | +pending_recovery 壳任务 |

Frozen 核心触碰说明（任务书 §34）：task_service.py 仅 additive（新函数 + 可选参数，既有 CAS/白名单零改动）；queue_router/celery_app 仅 registry/beat 条目 additive（C1/C2/C5/C6 契约测试验证保持）；agent_tasks/index_task_runtime 仅 defer 调用点传参。

## 13. Known Debt（STOP B 未解决，归属后续 STOP）

1. **上线首轮历史孤儿重投**：开发库存在大量历史 stale PENDING 行（本轮实测 5432 测试库 1275+ 行、5433 权威库 2 行 drill 残留）。新镜像上线后 beat 将开始恢复它们（max_count=5/max_age=24h 限流，执行消耗有界但可见）。**建议灰度期观察 + 可用 TASK_PENDING_RECOVERY_ENABLED=false 一键停用**。本轮实机窗口已将 5433 残留行钝化（not_before+1h，可逆）。
2. **宿主机双 PG 坑复现**：验证脚本曾误连 5432 同名库（PGPORT env），已按记忆口径修正为显式 5433；`.env` PGPORT=5432 的宿主机默认仍是长期陷阱。
3. admin retry 对 PENDING 的 409 保持原状（任务书 §22，正式 Admin Recovery Plane 归 STOP G）。
4. 测量环境限制：单进程 beat 内联调用的恢复延迟上界（threshold+scan≈90s）为推导+阈值实证；7×24 持续 beat 调度的延迟分布观测归 STOP H 指标（task_pending_recovery_latency）。
5. Provider 幂等 / 业务实体幂等 / confirmation reconciliation / Kafka identity / Admin plane / 6 项 observability 指标：归属 STOP C-H，本轮未触碰（任务书 §36 禁止项均未违反）。

## 14. Git

- commit：见提交记录（path-scoped，仅 §12 清单文件）
- 工作区真相：其余未提交改动归属并行会话（memory STOP C、travel 等），本轮未触碰

## 15. 最终生产语义（不变）

```text
at-least-once delivery（duplicate delivery remains possible）
single execution authority enforced by lease/fencing
side-effect duplication controlled by idempotency
PENDING 恢复：application-level accelerator（≤ threshold+scan ≈ 90s）
           + broker visibility timeout(1950s) 最终兜底（保留，未删除）
禁止声称 exactly-once delivery
```

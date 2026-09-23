# Phase2-G 最终验收与冻结报告 — Async Runtime

日期：2026-09-24 ｜ 阶段：Phase2-G — Final Async Runtime E2E & Production Freeze

## Verdict

```text
PHASE2_ASYNC_RUNTIME_PASS=true
PHASE2_ASYNC_RUNTIME_FROZEN=true
```

前置 gate 复确认：SQL_AGENT_FROZEN=true（tests/sql 268 全绿复跑）｜
PHASE2_LEASE_FENCING_PASS=true（a3b103d/3c54312，battery 复绿）｜
PHASE2_WORKER_OBSERVABILITY_PASS=true（e7f6d70/76fda91/fef948a，四 worker :9809 up）。

## STOP G0 基线

- HEAD=654f168；核心符号 HEAD-only 验证 8/8（QueueRouter/executor fencing/lease 原语/
  admission/worker_metrics/authorization/trace 接线，`git show HEAD:` 逐一确认）。
- 运行时：5 worker 池队列映射正确（agent/rag_index/maintenance/report/rag_metadata_shadow），
  prometheus task-workers 4/4 up，trace 后端 PG trace_store/trace_summary 正常。
- 并行会话 dirty（side-effect 幂等：agent_tasks/celery_app/queue_router/idempotency +670 行
  在飞、context_budget 暂存 12 文件、cs 文档）零混入；镜像重建烧入内容=HEAD+已披露在飞
  dirty，电池 208 + tests/sql 268 先行验证组合树。

## E2E Matrix（真实链路：网关 JWT → broker → worker → graph → DB）

| Case | Level | Auth | Queue | Lease | Admission | Graph | Metrics | Trace | Result |
| ---- | ----- | ---- | ----- | ----- | --------- | ----- | ------- | ----- | ------ |
| R1 Normal | real worker | editor 执行时解析 | agent→agent-worker | acquire+release | allowed | 完整跑通 | terminal SUCCESS=1/queue_wait/duration/lease acquire | trace 落库+tasks.trace_id 回填 | PASS |
| R2 Auth Deny | real worker | viewer 解析成功、SQLSkill 边界拒 | agent | 正常 | 正常 | 敏感节点 0 执行 | task SUCCESS + step permission_denied（归因口径） | trace success 1.1s | PASS |
| R3 Recovery/Takeover | real worker | E2 重新解析 | recovery→QueueRouter 重投 | E1 过期→sweep 认领→E2 换发 | E2 重新 acquire | checkpoint resume | recovery recovered=1/LEASE_LOST=1/progress fenced=1 | E1 error 收口 + E2 同 session 关联 | PASS |
| R4 Pause/Resume | real worker | resume 重新解析（A6 复跑佐证） | agent | 旧 exec fencing（PAUSED 后写拒）| 重新 acquire | checkpoint 续跑 | — | pause/resume 两条 trace 均收口 | PASS |
| R5 Cancel | real worker | — | agent | 终态 fenced | release | 节点边界停止 | — | trace error 收口 | PASS |
| Defer（G3） | real worker（一次性 zero-quota worker） | — | agent | 每轮 acquire+release | rejected→defer（defer_count=3, delay 38.6s） | 不进图 | admission rejected/defer=3 | — | PASS |
| Duplicate | real worker（SUCCESS 后再投递） | — | agent | 不再 acquire | 不进 | 0 执行 | — | — | PASS（SUCCESS_NOOP） |
| Context Leak | integration（O5/O7） | — | — | — | — | — | — | — | PASS（get_execution/current = None） |

注：G3 defer 演练中 `docker rm -f` 探针 worker 触发已知 visibility_timeout（1950s）重投窗口，
延迟消息 ~33min 后自动重投，届时被 SUCCESS 短路吸收（与 R-aintduplicate 同机制）——登记为
已知 broker 行为，非缺陷。

## Recovery Proof

```text
Task:     c4cf9f0b-43a3-441a-9e54-aebc57d8157c（R3）
E1:       f7c14fb7（lease_expires_at 回拨模拟心跳死亡）
Claim:    tasks.stale_execution_recovery（maintenance worker 原子认领）recovery_count=1
E2:       1fd2094e（QueueRouter 按 workflow 重投 agent，agent-worker 认领）
E1 late:  「租约被接管（execution=f7c14fb7），本 executor 退出且不写状态」+ progress fenced=1
Owner:    E2 唯一；Final: SUCCESS，execution_id=1fd2094e
```

## Authorization Proof

```text
viewer（e2e_sqlv/id46）:  sql step permission_denied、guard/executor 0 执行（A1 口径复现）
permission revoked:       A4/A5（执行时撤销 → deny）integration 全绿
tenant revoked:           A3/A4 integration 全绿
resume:                   A6 resume 重新校验全绿；R4 resume 新 ownership 换发
recovery:                 E2 走 execute 入口强制重解析（结构保证 + A 系列回归）
missing auth:             A9/A7 fail-closed 全绿
```

## Metrics Proof（Prometheus 真实查询，非进程内断言）

```text
task_terminal_total{main,SUCCESS}=1（新部署实例）；{main,LEASE_LOST}=1（上一实例，接管证据）
task_recovery_total{recovered} ≥1；task_admission_rejected_total{global_limit}=3；defer_total=3
task_lease_events_total{acquire/conflict/renew_fail}；task_fenced_write_total{accepted/fenced}
task_queue_wait_seconds / task_execution_duration_seconds（histogram count>0）
低基数 label 复查：workflow/status/reason/event/operation/result/dispatch_type，
无 task_id/execution_id/user_id/tenant_id/trace_id/query 进 label
```

## Trace Proof

```text
normal:   trace 落库、tasks.trace_id 回填一致（209d05852171 / degraded——覆盖率聚合语义，非失败）
deny:     trace success（拒绝在 skill 层优雅收口）+ step 归因
recovery: E1=314574670096 error 收口、E2 同 session_id 关联；error span=sql.guard
          （恢复重放中一次 guard 报错被 skill 吸收，任务 SUCCESS——聚合语义 P2）
pause/resume: 6e99fb24b00b(pause) + 37b9de0174b0(resume) 均收口
lease lost: E1 fencing 退出 trace error 收口
dangling: 全部 trace 无 leaked span（leaked=true 计数=0），duration_ms 全部落库
```

## Deployment Consistency

```text
镜像：app/worker 池/beat 全部 2026-09-23T18:19~18:22Z 重建
hash 核验：task_executor.py/task_service.py 容器内 md5 == 工作区 == （未 dirty 文件）HEAD；
          agent_tasks.py 容器 == 工作区（含 side-effect 会话已披露 dirty，≠HEAD，三方 hash 留档）
stale container：无（beat/prometheus 同批 reload）
```

## Git Isolation

```text
commits:            本阶段仅 e2e 脚本 + 本报告（见 git log）
Phase2-G files:     backend/scripts/e2e_async_runtime.py + docs/本报告
other-session dirty: side-effect 幂等会话 / context_budget 暂存 / cs 文档——零混入
accidental inclusion: 无（本轮提交文件均为本会话独占新文件，无共享文件混叠）
```

## Findings

```text
P0: None
P1: None（本轮零业务代码修改，纯验收 + E2E 脚本 + 报告）
P2: ①resume/recovery 类 execution 的 trace 聚合 status 可为 error/degraded（span 级
    sql.guard 错误被 skill 吸收、覆盖率口径）——任务终态以 tasks.status 为准，不改 tracing；
    ②impl 早期日志（lease acquired）先于 trace 绑定 trace_id=null，task_id/execution_id
    内联可查；③ETA 消息 + worker SIGKILL → visibility_timeout 窗口重投（broker 既有语义）；
    ④defer release 不清 worker 名（展示残留，非执行残留）
```

## Deferred

```text
Grafana dashboard（task-workers job 已可配图）
admin retry（管理端能力）
worker :9809 端点认证评估
trace 聚合语义优化（degraded/error 与任务终态解耦展示）
SIGKILL destructive test（已用 lease 回拨安全方式覆盖等价场景）
```

## Final Freeze

```text
PHASE2_ASYNC_RUNTIME_PASS=true
PHASE2_ASYNC_RUNTIME_FROZEN=true
```

口径声明：本运行时为 **at-least-once delivery + single valid execution ownership +
fenced state writes + idempotent/controlled recovery**，不宣称 exactly-once。
此后不再重构 task runtime，除非真实生产 bug / 明确新需求 / 安全漏洞。

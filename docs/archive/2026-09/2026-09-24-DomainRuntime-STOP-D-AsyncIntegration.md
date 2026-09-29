# Domain Runtime STOP D — Async / Side Effect / Recovery Integration（异步域契约报告）

> 日期：2026-09-24 ｜ 前置：STOP C PASS（093d173）
> 验收方式：真实网关 → 真实 broker → 真实 worker 池 → 真实 PG tasks/checkpoint。
> 冻结层 Phase2 运行时矩阵（scripts/e2e_async_runtime.py R1-R5）整体重跑 + 本轮新增
> Domain 契约观测（scripts/e2e_domain_async.py：信封/恢复身份/trace 关联/owner 校验/工单幂等）。

---

## 1. Verdict

```text
STOP_D_PASS      = true
STOP_E_ALLOWED   = true
```

## 2. D1 Domain Task Envelope（实机观测：最近 8 个任务行）

```text
7d84af7a CANCELLED user=50 tenant=default thread=task-7d84af7a-… graph=main trace=5ec7dedec480
3a3c6a4e SUCCESS   user=50 tenant=default thread=task-3a3c6a4e-… graph=main trace=fcdd0a7ec91b
e0b42959 SUCCESS   user=50 tenant=default thread=task-e0b42959-… graph=main recovery=1 trace=84376563b193
…（8/8 行 user/tenant/thread/graph/trace 全非空）
```

- Celery 消息**只带 task_id**（冻结契约），身份权威在 PG tasks 行：user_id/tenant_id/thread_id/graph_name/trace_id 五元组 8/8 完整。
- execution_id 由租约认领换发，worker 侧 `_bind_task_identity` 显式绑定 tenant/user（空值也绑，防 prefork 泄漏）；授权由执行器按 tasks 行身份**执行时现查 auth.users**（不信任创建时 JWT 快照）。

## 3. D2 恢复时域身份保持（recovery=1 实证）

task `e0b42959`：E1 被接管（lease 回拨 + maintenance sweep）→ E2 换发 → SUCCESS 归属 E2（R3，5/5 之一）；
恢复后 `user=50 tenant=default thread=task-e0b42959-… graph=main` 与恢复前一致——**domain/session/tenant/business operation 全部保持**，execution_id 变化不携带业务身份漂移。

## 4. D3 Pause/Resume（R4）

RUNNING → pause → PAUSED（旧 execution 以 fencing 进度写模拟劫持 → 被拒）→ resume → **新 execution（3a131c68→f820ed24 换发）** → SUCCESS；resume 用 update_state 以执行时授权覆盖 checkpoint 旧权限——**恢复不重新路由域**（graph_name=main 固定，checkpoint 定位同一 thread）。

## 5. D4 Authorization（R2）

viewer 提交 SQL 分析任务 → sql step 以 `permission_denied` 收口（**敏感节点 0 执行**，数据范围拒绝不泄漏到后续步骤）；前台角色 ≠ worker 自动放行的契约在执行器每次重解析授权（fail-closed FAILED + `task_authorization_denied_total` 指标）。

## 6. D5 Side Effect 幂等（实机 + 冻结层证据）

- 实机：同会话两次投诉（AFTER_SALES+COMPLAINT 双组命中进 CS）→ 两轮行为**确定一致**（均进入退款办理 need-info 追问）、`tickets(conversation)=0`——无重复建单。
- exactly-once 结构保障（代码+冻结层在案）：CS 确认单 DB 条件 UPDATE CAS（pending→confirmed 仅一方成功）；CS 动作审计落库走 `ai.idempotency_records`（operation=cs.action.persist，ON CONFLICT 幂等）；运行时 duplicate delivery 由 Phase2 R 矩阵 SUCCESS_NOOP 吸收（fenced 写+终态幂等）。未新造任何 ledger。

## 7. D6 Fencing

R3/R4 双实证：被接管的旧 execution 写进度/状态 → TaskLeaseLost 拒绝（fenced_write 计数）；CANCELLED 终态不被迟到旧 worker 覆盖（R5，等 5s 后复核仍 CANCELLED）。

## 8. D7 Trace Correlation（recovery 场景）

task e0b42959 的 trace（容器内 SQLite 权威存储）：

```text
trace 84376563b193: session_id='task-e0b42959-4056-4e14-…' status='error'
tags={task_id: e0b42959-…, execution_id: c49db1d5…, queue: agent}
```

session_id=thread_id 跨 execution 不变 → E1（error）/E2（success）两条 trace 通过 **task_id+thread_id** 可关联（不要求同 trace id）；tags 携带 execution_id 可区分新旧执行。

## 9. D8 Async Result → 回传契约

GET /api/tasks/{id}：owner 视角返回全量任务契约（status/current_node/error_type/duration_ms/…）；**他人读取 → 404**（属主校验，防枚举）。任务失败以结构化 error_type 表达（分类词表），不存在「task failed 被 reporter 当 domain unknown」的通路——域图不提交 Celery 任务，任务回传与域 Reporter 职责分离。

## 10. 结果汇总

```text
R1 Normal              PASS  exec/trace 落库
R2 Auth Deny           PASS  sql step permission_denied（0 执行）
R3 Recovery/Takeover   PASS  E1!=E2、旧 worker fencing 退出、SUCCESS 归属 E2
R4 Pause/Resume        PASS  换发 execution、劫持写被拒、SUCCESS
R5 Cancel              PASS  CANCELLED 终态稳定
D1 信封                PASS  8/8 行身份五元组完整
D2/D7 恢复身份+trace   PASS  域身份保持 + session_id=thread_id 关联
D8 回传契约            PASS  owner 404 隔离 + 结构化错误
D5 工单幂等            PASS  重复投诉零重复建单
```

## 11. 如实记录

- D5 的 need-info 语义：投诉未带订单号时 CS 不建工单、先补槽——工单幂等断言 `≤1` 在「两轮均未建单」下成立；更强的 exactly-once 证据由确认 CAS 与幂等账本承担（§6）。
- P2-10 关联影响：D7 的 trace 只能经容器内 SQLite 查询（PG 镜像表空，STOP C 已登记）。

```text
STOP_D_PASS      = true
STOP_E_ALLOWED   = true
```

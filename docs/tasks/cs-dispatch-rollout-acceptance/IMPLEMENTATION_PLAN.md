# 客服派单部署验收方案

## 目的

代码阶段已完成。本阶段只核对部署数据库、APISIX 负载、故障恢复和分阶段放量；当前结论与待办见同目录 [TASK.md](TASK.md) / [PROGRESS.md](PROGRESS.md)。

## 门禁

### 1. 数据库迁移

- 在隔离或获批准的目标数据库运行 `scripts/cs_dispatch_shared_migrate.py --json` 只读预检。
- 对 028/029 执行前备份 `customer_service` 与 `auth` schema，并复核存量数据风险；028 会规范旧 assignment 状态，异常数据应使迁移失败。
- 仅在低峰/停写窗口运行 `--apply --confirm --operator <标识>`；复核 handoff 新字段、`auth.rbac_audits`、`auth.users` 新字段和服务健康状态。
- 隔离临时库并发验收入口：`scripts/cs_dispatch_pg_acceptance.py`。不得将临时库结果当作目标部署库迁移验收。

### 2. APISIX 负载与业务正确性

- 必须用指向隔离后端的 APISIX 地址；不得对共享开发入口 `localhost:9080` 运行压测。
- 运行 200 并发突发及 20 RPS × 10 分钟持续测试。成功标准：5xx `<0.1%`，创建接口 P95 `<300ms`、P99 `<800ms`，活动 assignment 不超过坐席容量；offer 投递 P95 `≤1s`、P99 `≤2s`。
- 只有明确批准写路径数据及清理责任后才使用 `--include-write`。

### 3. 故障恢复与放量

- 先只读运行 `scripts/cs_dispatch_chaos.py`；受控写演练只允许停启本任务的 `cs-dispatcher`。Redis、PostgreSQL、API 故障演练不得自动停止共享容器。
- 故障期间不得产生重复/超容量绑定；恢复后 outbox 应在 1–2 个 worker tick 内收敛，dispatcher 健康并持续运行。
- 通过预检后按 `shadow → 5% → 20% → 50% → 100%` 观察放量；每档至少 24 小时且累计至少 200 个转人工工单，异常立即切回 `off`。

## 执行手册与证据

- [客服派单变更窗口执行手册](../../../部署/cs-dispatch-变更窗口执行手册.md)包含目标确认、备份、迁移、压测及演练步骤。
- 预检、压测和演练的机器结果按需输出到 `docs/reports/`；任务目录只记录当前结论与未闭合门禁，不留旧批次报告。

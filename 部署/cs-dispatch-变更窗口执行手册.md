# 客服派单/RBAC 变更窗口执行手册

> 本手册只记录可复用的部署操作。当前迁移状态、阻塞和证据见 [客服派单验收任务](../docs/tasks/cs-dispatch-rollout-acceptance/PROGRESS.md)；所有执行必须使用隔离或获批准的目标环境。

## 变更前检查

- 确认操作人、目标环境、版本、APISIX 地址、回滚联系人和变更窗口。
- 禁止对共享开发数据库、Redis、APISIX 或其他会话使用的容器执行写操作或故障注入。
- 数据库操作前确认目标库、`customer_service`/`auth` schema 备份位置和可恢复性。
- 当前开发环境状态以实时预检为准，不使用本手册中的历史快照。

## 门槛一：028/029 数据库迁移

迁移 028 会规范无 `handoff_id` 的旧 assignment 状态，并对孤儿/重复数据执行前置检查；不满足约束时应停止并先核实数据。迁移 029 会扩展 `auth.users`，属于共享表 DDL。两项在正式目标上执行前必须备份并处于批准的低峰/停写窗口。

```bash
# 只读预检
python scripts/cs_dispatch_shared_migrate.py --json

# 备份两个相关 schema；替换为目标环境凭据和容器名
pg_dump -h <目标数据库> -U <操作账号> -d <目标库> -n customer_service -n auth -f <安全备份目录>/pre-028-029.sql

# 仅在确认目标、备份和窗口后执行
python scripts/cs_dispatch_shared_migrate.py --apply --confirm --operator <标识>

# 复核迁移对象和服务健康状态
psql -h <目标数据库> -U <操作账号> -d <目标库> -c "\\d customer_service.handoffs" -c "\\d auth.rbac_audits"
```

如果迁移失败，不得跳过约束或手改生产数据。029 的新增对象可按迁移回滚方案处理；028 发生状态更新后优先前滚修复，任何回滚先根据备份核对受影响 assignment。

隔离临时库的 PostgreSQL 并发验收：`python scripts/cs_dispatch_pg_acceptance.py`。它不能替代目标环境迁移验收。

## 门槛二：APISIX 负载测试

正式压测只对指向隔离后端的部署 APISIX 执行；不得压测共享开发入口 `localhost:9080`。

```bash
curl -fsS http://<隔离网关>:9080/health
python scripts/cs_dispatch_loadtest.py --gateway http://<隔离网关>:9080 --burst-only
python scripts/cs_dispatch_loadtest.py --gateway http://<隔离网关>:9080
```

正式负载为 200 并发突发和 20 RPS × 10 分钟持续；按验收方案检查 5xx、P95/P99、队列、assignment 容量和 offer 投递。只有明确批准写路径数据与清理责任后才加 `--include-write`。输出 JSON 按需写入 `docs/reports/`。

## 门槛三：故障恢复演练

```bash
# 只读预检
python scripts/cs_dispatch_chaos.py

# 唯一自动受控写操作：停启本任务的 cs-dispatcher
python scripts/cs_dispatch_chaos.py --target dispatcher --apply
```

Redis、PostgreSQL 和 API 实例属于共享依赖，脚本只生成手动演练步骤；值班人仅可在获批窗口中执行。检查 fail-closed、无重复/超容量绑定、dispatcher 恢复健康，以及 outbox 在 1–2 个 worker tick 内收敛。

## 启动与放量顺序

1. 028/029 预检、备份、迁移和对象复核通过。
2. 启动 `cs-dispatcher`，确认健康检查与 Redis 心跳。
3. 以 `CS_DISPATCH_MODE=shadow` 观察，并检查 `/cs/ops/dispatch/stats`、Prometheus 指标及告警。
4. 经批准后运行 `scripts/cs_dispatch_rollout.py --set <百分比> --operator <标识>`，按验收方案观察各档，再进入 `enforce`。
5. 任一数据不一致、超容量或恢复异常，立即切回 `off` 并停止放量。

## 记录结果

将目标环境、代码版本、迁移复核、负载指标、故障现象、恢复判据和未通过项更新到 `docs/tasks/cs-dispatch-rollout-acceptance/PROGRESS.md`。机器输出只按当前验收需要保留。

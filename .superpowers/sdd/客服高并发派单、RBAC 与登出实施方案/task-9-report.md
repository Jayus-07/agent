# Task 9 report — P9 压测脚本、故障演练与真实 PG 验收

## 真实 PostgreSQL 派单验收（本任务核心证据，已真实执行）

隔离临时库（`cs_dispatch_acceptance_*`，跑后即 drop），迁移链
002 → 006 → 014 → 020 → 028 幂等应用。

| 组 | 场景 | 结果 | 判定 |
| --- | --- | --- | --- |
| A 并发绑定 | 40 工单 × 8 并发 dispatcher 任务 | 40 dispatched；674 次 SKIP LOCKED 竞争全部安全退避；**0 重复绑定 / 0 超容量 / 状态与 assignment 全一致** | ✅ |
| B 容量上限 | 1 坐席 cap=2，10 工单 | 恰好 2 dispatched，其余 no_candidate | ✅ |
| C 负载均衡 | 3 等容量坐席，15 工单 | 每人恰 5 单（极差 0，完美轮询） | ✅ |

报告：`docs/reports/cs-dispatch-pg-acceptance-2026-09-20.json`。
**关闭了 Task 6 遗留的「共享库缺 028 字段」验收门槛**（当时显式 skip，
不用内存 fake 冒充）。

## 放量控制器（shadow → 5% → 20% → 50% → 100%）

- `CS_DISPATCH_ROLLOUT_PERCENT` 登记进 `sys_config` 白名单（DB 覆盖 +
  15s TTL 刷新 + env 兜底），**免重启放量**，复用既有配置治理机制。
- `dispatch_once`（enforce）按 `tenant:conversation` 的 SHA1 稳定哈希
  分桶，`rollout_skipped` 不写行；shadow 恒全量计算，保持对比能力。
- 控制器 `scripts/cs_dispatch_rollout.py`：`--show` 读生效值与来源；
  `--set N` 经 `sys_config.set_value` 写 DB 覆盖 + 审计 JSON 落
  `docs/reports/`；off 模式下拒绝直接放量。
- 单测：percent=0 只挡 enforce 不挡 shadow；桶位稳定边界（=桶位拦截 /
  +1 放行）；默认 100 不门控。

## 压测脚本

`scripts/cs_dispatch_loadtest.py`：突发 200 并发 + 持续 20 RPS × 10min
（`--duration` 可调），p50/p95/p99/max 汇总 JSON 落 docs/reports/。
执行前先 probe，5xx/不可达不压。

**⚠️ 诚实登记**：本会话曾向 `localhost:9080` 冒烟执行 `--burst-only`，
probe 被接受后突发段超过 8 分钟未完成——疑似网关把 200 并发转发到了
共享后端。为保护共享栈已终止进程（确认无残留）。**网关挂载的正式压测
必须在部署环境 + 变更窗口执行**，登记为门槛。

## 故障演练

`scripts/cs_dispatch_chaos.py` 分两层：

- **preflight（只读，已执行）**：PG 可达 ✅、Redis 可达 ✅、dispatcher
  心跳 key 空（mode=off 未运行，符合预期）、共享库确认无 028（生产部署
  门槛继续成立）、`customer_service.events` 不存在（同上）。
- **受控演练 `--target dispatcher --apply`**：唯一写操作 = stop/start
  `cs-dispatcher`（本任务专属服务），观测 30s 心跳过期与 outbox 积压。
  本会话未执行（dev 栈共享，需变更窗口）。
- **Redis / PG / API 故障**：共享容器，不提供自动 stop；脚本输出人工
  演练清单（步骤、期望现象、恢复判据），由值班人在窗口执行。

## 门槛汇总（不宣称通过）

1. 网关挂载压测（突发 200 / 持续 20 RPS × 10min）→ 部署环境执行。
2. dispatcher 停启演练、Redis/PG/API 故障演练 → 变更窗口执行。
3. 共享库 028/029 迁移链（028 建表结构已在临时库验证幂等可执行）。

## 测试与静态检查

- `test_dispatch_service.py` 28 passed, 1 skipped（含 3 个新放量用例）
- compileall / Ruff（4 个脚本）/ `git diff --check` 通过

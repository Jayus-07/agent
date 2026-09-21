# Task 8 report — P8 持久 outbox relay 与可观测性

## 交付范围

| 项 | 内容 |
| --- | --- |
| Prometheus 指标 | `cs_dispatch_queue_depth`、`cs_dispatch_offered_total{result}`、`cs_dispatch_wait_seconds`、`cs_dispatch_reaped_total{action}`、`cs_dispatch_online_agents`、`cs_outbox_pending`、`cs_outbox_lag_seconds`、`cs_outbox_published_total{result}`（/metrics 自动暴露） |
| 埋点位置 | worker `run_tick`（派单结果计数、reaper 计数、relay 计数、每秒 gauge 刷新）；`outbox.relay_pending_events`（单事件 published/deferred + pending/lag gauge）；`reaper`（released / closed_max_attempts / closed_total_deadline）；`service.dispatch_once`（入池→派出等待时长直方图） |
| 运营统计 | `GET /cs/ops/dispatch/stats`（`require_admin_user` 门禁）：排队/在派/启用坐席/outbox 积压与 lag 的 DB 实时快照，口径与 /metrics 一致 |
| 告警规则 | `docker/prometheus-alert-rules.yml` 新增 `agent-platform-cs-dispatch` 组（6 条：3 critical + 3 warning） |

## 告警规则清单

| Alert | 表达式要点 | 级别 |
| --- | --- | --- |
| CsOutboxLagHigh | `cs_outbox_lag_seconds > 5` for 1m（P8 标准 P99<2s） | warning |
| CsOutboxBacklogGrowing | `cs_outbox_pending > 100` for 5m | warning |
| CsDispatchQueueDepthHigh | `cs_dispatch_queue_depth > 50` for 5m | warning |
| CsNoOnlineAgents | `cs_dispatch_online_agents == 0` for 2m | critical |
| CsPresenceUnavailableSpike | presence_unavailable 5m 增量 > 5 | critical |
| CsReaperClosedSpike | closed_* 10m 增量 > 10 | warning |

## 验收对照（方案 §六 P8 完成标准）

| 标准 | 结果 |
| --- | --- |
| 杀死 dispatcher 后事件不丢 | outbox 行与状态同事务提交（P7 落地写侧），relay 重试 pending；进程级演练归 P9 chaos 脚本 |
| 重复投递由 event_id 去重 | relay 用 `FOR UPDATE SKIP LOCKED` 分片，同一行只会被一个副本投递；客户端按 event_id 幂等 |
| outbox lag P99 < 2s | `cs_outbox_lag_seconds` gauge + CsOutboxLagHigh 告警；P99 实测属部署门槛 |
| 所有审计字段完整 | outbox 行携带 tenant_id / handoff_id / target_agent_id / actor_user_id / created_at / published_at |

## 测试证据

- P6–P8 聚焦：`80 passed, 1 skipped`
  - `test_tick_records_p8_metrics`：tick 埋点计数与 lag gauge
  - `test_cs_ops_stats.py` 3 例：快照形状、无 pending 时 lag=0、DB 不可用 503
- `docker/prometheus-alert-rules.yml` YAML 解析通过；Ruff / compileall / diff-check 通过。

## 未验证（登记为门槛）

- Prometheus 实际抓取与 Alertmanager 通知链路（需部署环境）。

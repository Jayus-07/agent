# 客服派单/RBAC 变更窗口执行手册

> 任务：客服高并发派单、RBAC 与登出（P0–P9 已全部落地，分支 `codex/cs-dispatch-rbac-logout`，
> 提交链 `5b9175a`..`8ed6e8d`，客服域回归 949 passed / 8 skipped）。
> 本手册覆盖 P9 收口时登记的三项遗留门槛，供变更窗口值班人逐步执行。

## 环境快照（2026-09-21 03:45 实测，只读检查）

| 项 | 实测值 |
|---|---|
| 共享 PG | 容器 `agent-postgres-1`，宿主 `127.0.0.1:5433`→5432，业务库 **`agent_memory`**（另有 `agent_business`，本任务不涉及） |
| 028 状态 | **未部署**（18/18 关键对象全部缺失，见 `docs/reports/cs-dispatch-migrate-preflight-*.json`） |
| 029 状态 | **未部署**（`auth.rbac_audits` 不存在、`auth.users` 无 version/tenant_id） |
| cs-dispatcher 容器 | **未运行**（compose 定义已在，部署窗口随栈启动） |
| APISIX | `agent-apisix` 运行于 127.0.0.1:9080（共享入口，**勿直接压测**） |
| WS 路由 | `apisix/apisix.yaml` 已含 `/ws/cs/*` 路由；HTTP API 走 B1 基线兼容路由 |

## 门槛一：共享库 028/029 迁移部署

⚠️ **执行前必读**：

- **028 并非纯加法**：含一条存量数据 UPDATE（`assignments` 中无 `handoff_id` 的
  offered/accepted 行降级为 `released`），并有 4 组孤儿/重复数据 RAISE EXCEPTION
  检查——**存量数据不干净时迁移会主动失败**（这是设计行为，不是事故）。
- **029 会给 `auth.users` 加列**（version/tenant_id，均有 DEFAULT，不锁业务写入，
  但仍是共享表 DDL）。
- 两文件均幂等（`IF NOT EXISTS`），重复执行安全；每文件单事务，失败整体回滚。

### 执行步骤

```bash
# 1. 预检（只读，可随时跑）
python scripts/cs_dispatch_shared_migrate.py --json

# 2. 备份（必须；只备相关 schema，避免全库拖太久）
docker exec agent-postgres-1 pg_dump -U postgres -d agent_memory \
  -n customer_service -n auth \
  -f /tmp/pre_028_029_backup.sql
docker exec agent-postgres-1 ls -la /tmp/pre_028_029_backup.sql
# 拷出容器留存：
docker cp agent-postgres-1:/tmp/pre_028_029_backup.sql ./backup-pre-028-029.sql

# 3. 确认低峰/停写窗口内，执行（脚本自带复核 + JSON 报告）
python scripts/cs_dispatch_shared_migrate.py --apply --confirm --operator <你的标识>

# 4. 人工抽查（脚本复核之外的双保险）
docker exec agent-postgres-1 psql -U postgres -d agent_memory -c \
  "\d customer_service.handoffs" -c "\d auth.rbac_audits"
```

### 回滚预案

- 029：`DROP TABLE IF EXISTS auth.rbac_audits;` + `ALTER TABLE auth.users DROP COLUMN IF EXISTS version, DROP COLUMN IF EXISTS tenant_id;`（新对象，回滚零风险）。
- 028：**优先前滚修复而非回滚**（新列/索引/约束均为加法，可保留）。若必须回滚：恢复 pg_dump 中 customer_service schema 即可；`assignments` 状态降级 UPDATE 的逆向不可自动推导，回滚前先从备份表核对。
- 验收脚本（隔离临时库，不碰共享库）随时可复跑：
  `python scripts/cs_dispatch_pg_acceptance.py`（A/B/C 三组，判据见报告 JSON）。

## 门槛二：网关挂载压测（APISIX）

⚠️ **教训（2026-09-20）**：曾误向共享网关 `localhost:9080` 冒烟压测，200 并发被
转发到共享后端，8 分钟未完成——已强杀并确认无残留进程。**正式压测只在部署窗口、
对指向隔离后端的网关执行。**

```bash
# 1. 确认目标网关指向部署窗口专用后端（非共享 9080）
curl -s http://<隔离网关>:9080/health

# 2. 先 burst-only 冒烟（200 并发一次性突发）
python scripts/cs_dispatch_loadtest.py --gateway http://<隔离网关>:9080 --burst-only

# 3. 全量：突发 200 并发 + 20 RPS × 10 分钟持续
python scripts/cs_dispatch_loadtest.py --gateway http://<隔离网关>:9080

# 写路径压测（转人工入池）会落测试数据，需显式开启并自担清理：
python scripts/cs_dispatch_loadtest.py --gateway http://<隔离网关>:9080 --include-write
```

判据（报告 JSON 落 `docs/reports/cs-dispatch-loadtest-<ts>.json`）：
burst P95 < 2s 零 5xx；sustained 全程零 5xx、P95 稳定无爬升。

## 门槛三：容器级故障演练

脚本分两层，共享资源故障只出人工清单（内置期望现象与恢复判据）：

```bash
# 1. 只读预检（基线证据：Redis 连通/心跳、outbox 积压、028 列、API /health）
python scripts/cs_dispatch_chaos.py

# 2. 受控演练（唯一写操作：停启本任务专属 cs-dispatcher，不碰共享容器）
python scripts/cs_dispatch_chaos.py --target dispatcher --apply

# 3. Redis / PG / API 故障：按脚本输出的人工清单在变更窗口执行
#    （stop → 观察 60s → start；判据：fail-closed 不产生脏绑定、
#     outbox pending 恢复后 1-2 tick 清零、dispatcher 循环不退出）
```

## 部署后启用顺序（一次变更窗口内的推荐节奏）

1. **门槛一**：028/029 部署 + 复核通过。
2. 启动 dispatcher：`docker compose up -d cs-dispatcher`（healthcheck 过 + Redis 心跳
   `cs:dispatcher:heartbeat:*` 出现）。
3. `CS_DISPATCH_MODE=shadow` 观察 ≥24h：`/cs/ops/dispatch/stats` 与 Prometheus
   组 `agent-platform-cs-dispatch`（6 条告警）无异常、shadow 不写真实绑定。
4. 放量：`python scripts/cs_dispatch_rollout.py --set 5 --operator <标识>` →
   稳定后 20 → 50 → 100（sys_config DB 覆盖 + 15s TTL，免重启）。
5. 确认 100% 稳定后切 `CS_DISPATCH_MODE=enforce`。
6. **门槛二/三**在步骤 2 之后、步骤 3 之前或并行窗口内完成。

## 登记状态

| 门槛 | 状态 | 证据 |
|---|---|---|
| 028/029 共享库部署 | ✅ **已执行**（2026-09-21 04:12，操作人 `workbuddy-20260921T0411`，低峰窗口） | 执行前预检 `cs-dispatch-migrate-preflight-20260921T041116.json`（PENDING_DEPLOY，风险项全 N/A）；备份 `backup-pre-028-029.sql`（475KB，customer_service+auth 双 schema，容器 `/tmp` 与 worktree 根各一份）；执行后复核 `cs-dispatch-migrate-apply-20260921T041215.json`（**ALL_APPLIED**）；人工抽查 handoffs 新列 5/5、`auth.rbac_audits` 建表、`auth.users` 补 version/tenant_id；存量数据零降级（assignments 仅 1 条 released）、共享后端 /health 正常 |
| 网关挂载压测 | 未执行（待隔离环境） | 2026-09-20 误压已终止，无残留进程 |
| 容器级故障演练 | dispatcher 受控项就绪（dispatcher shadow 已启动）；Redis/PG/API 停启演练待窗口 | chaos 只读预检 `cs-dispatch-chaos-20260920T202023.json`（⚠️ 该脚本读 `.env` 默认连本机 `localhost:5432/demo`，对共享库跑必须显式覆盖 `PGHOST=127.0.0.1 PGPORT=5433 PGDATABASE=agent_memory`，否则误报 028 未应用） |

## 部署后启用进度（随窗口滚动更新）

| 步骤 | 状态 | 备注 |
|---|---|---|
| 1. 028/029 部署 + 复核 | ✅ 2026-09-21 04:12 | ALL_APPLIED |
| 2. 启动 dispatcher | ✅ 2026-09-21 04:24 | `CS_DISPATCH_MODE=shadow` 双副本 healthy；启动期每副本 1 条 `dispatch iteration failed`（event loop 重建瞬时错误，不复发）；心跳 `cs:dispatcher:heartbeat:*` 双实例各 1 条 |
| 3. shadow 观察 ≥24h | ⏭️ **取消**（用户决策：开发阶段不灰度，2026-09-21 04:37） | shadow 实际运行 13 分钟（04:24–04:37）已完成使命：relay 清空 456 条积压、零脏绑定 |
| 4. 放量 5→20→50→100 | ⏭️ 不适用 | `CS_DISPATCH_ROLLOUT_PERCENT` env 兜底默认即 100（`backend/config/cs_dispatch.py:96`），sys_config 无覆盖行，enforce 天然全量 |
| 5. 切 enforce | ✅ 2026-09-21 04:37 | 双副本 `--force-recreate` 为 `CS_DISPATCH_MODE=enforce`，日志确认 `mode=enforce`、零错误、心跳换新实例；当前队列空闲（19 closed / 2 human_active），首个真实转人工将秒级派单 |

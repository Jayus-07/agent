# Task 6 实施报告（P6 自动 dispatcher）

## 交付范围

新增：`backend/config/cs_dispatch.py`、`backend/customer_service/dispatch/{repository,presence,event_relay}.py`、
`backend/workers/cs_dispatcher.py`、三个测试文件；修改：`dispatch/service.py`（追加派单事务）、
`dispatch/__init__.py`、`docker-compose.yml`（新增 `cs-dispatcher` 服务）。

## 冻结契约落实情况

| Brief 条目 | 落点 | 状态 |
| --- | --- | --- |
| 1. 工单查询带 `tenant_id` + `FOR UPDATE SKIP LOCKED` | `repository.lock_waiting_handoff_stmt`（队列头预读 `next_waiting_handoff_stmt` 不带锁，理由见下） | 满足 |
| 2. presence 只认 45s TTL；Redis 不可用本轮不派单 | `presence.online_agent_ids` 用 `AgentHub.presence_key` + `MGET`；不可用/异常/结果畸形一律返回 `None` → `status=presence_unavailable` | 满足 |
| 3. 候选需同租户 + enabled + available + accepting + 在线 + 活动数 < `max_conversations` | `repository.list_accepting_agent_ids` + `least_loaded_agent_stmt` 的 WHERE 与容量谓词（活动数只计 `offered`/`accepted`） | 满足 |
| 4. 坐席行 `FOR UPDATE SKIP LOCKED`；排序 活动数↑ → `last_assigned_at NULLS FIRST`↑ → `agent_id`↑ | `least_loaded_agent_stmt`（`FOR UPDATE OF cs_agents SKIP LOCKED`） | 满足 |
| 5. 同事务更新 handoff / assignment / 会话投影 | `dispatch_once` 单个 `session.begin()`：handoff→`agent_offered`+`assigned_agent_id`+`assignment_version`+`attempt_count`+`offered_at`+`offer_expires_at`；插入 `CSAssignment(state=offered)`；conversation 投影 `assigned_agent_id` 且 `handling_mode` 保持 `waiting_human` | 满足 |
| 6. 同事务写 `CSEvent`，提交后 `publish(..., persist=False)`，广播失败不回滚 | 事务内落 `CSEvent(type=conversation.offered, tenant_id, handoff_id, target_agent_id, event_id, outbox_status=pending)`；提交后经 `event_relay.publish_persisted_event` 广播，`event_id` 与持久行一致（客户端据此去重） | 满足 |
| 7. 每次最多一单；结构化结果；无工单/无候选/Redis 故障为非错误 no-op | `run_once` 每次只绑定一张；`DispatchResult(status, handoff_id, conversation_id, agent_id, assignment_version, offer_expires_at)` | 满足 |
| 8. worker `run_once`/`run_forever`、可配间隔、30s 心跳、异常继续、实例名可区分 | `cs_dispatcher.run_once/run_forever`；`CS_DISPATCH_INTERVAL_SECONDS`；`presence.write_dispatcher_heartbeat`（`setex` TTL 30）；异常只记日志；`resolve_instance_id`（env 优先，否则 hostname） | 满足 |
| 9. compose 独立 `cs-dispatcher`，复用镜像，依赖 postgres/redis healthy，声明 2 副本与 healthcheck | `docker-compose.yml` → `cs-dispatcher`：`command: python -m backend.workers.cs_dispatcher`、`depends_on: postgres/redis service_healthy`、`deploy.replicas: 2`、`healthcheck` 检查容器 PID 1 argv | 满足 |

## 关键设计决策（超出 brief 但必要）

### 1. 加锁顺序 `conversations → handoffs → cs_agents` + 无锁预读

P4 入池事务（已提交的 `_create_in_transaction`）先锁会话行、再锁活动工单行。若派单
反过来先锁工单再更新会话投影，同一会话上两条路径会互相等待形成**死锁**：派单持 H 等 C，
P4 持 C 等 H（用户对同一会话重复点击转人工时可达）。

因此派单先用一次**无锁预读**（`next_waiting_handoff_stmt`）拿到队头会话 ID，再按
`conversations → handoffs → cs_agents` 顺序取锁。预读不构成派单依据：真正的绑定权仍归
`lock_dispatchable_handoff` 的 `FOR UPDATE SKIP LOCKED`；若预读到的候选在取锁前被别的
副本拿走/状态变化，返回 `contended` 并整轮不动任何行，由下一轮重试。

三个锁全部 `SKIP LOCKED`：竞争者立即让开而不是排队，避免队头被反复串行争抢。

### 2. 多租户下"每次最多一单"与全局优先级

`waiting_tenant_heads_stmt` 用 `DISTINCT ON (tenant_id)` 取每个租户的队头，再按
`priority DESC, created_at ASC, id ASC` 排序，worker 依次尝试租户直到第一个成功。
这样既保持"每次只绑定一张工单"，又不会因为某个租户无在线坐席就整体停摆，
且跨租户仍按全局优先级顺序尝试。

### 3. shadow 模式实现为真正的 dry-run

`CS_DISPATCH_MODE` 三态：`off` 完全不碰数据库；`shadow` 走完取单/在线/容量/排序
后 **不写任何行、不广播**（事务内无写操作，退出即提交空事务并释放读锁）；
`enforce` 真实派单。P9 灰度梯子（shadow→5%→20%→50%→100%）直接复用 `shadow`。

### 4. Redis 故障与"没人在线"必须区分

`online_agent_ids` 对空候选返回 `set()`（正常 no-op → `no_candidate`），对
Redis 不可用/异常返回 `None`（→ `presence_unavailable`）。把二者混为一种会让
Redis 故障静默变成"当前无人接单"，与方案 §五"Redis 故障时可以安全停止派单"矛盾。

Redis 客户端是同步实现（与 P5 的 `AgentHub` 同一份 `get_redis()`），因此 presence
查询与心跳都经 `asyncio.to_thread` 执行 —— 与 P5 复核轮次提出的"同步 Redis 调用不得
阻塞事件循环"保持一致。

## 红灯/绿灯记录

先写测试、后写实现：`test_dispatch_presence.py` / `test_dispatch_service.py` /
`tests/workers/test_cs_dispatcher.py` 在实现前全部因模块不存在而 ERRORS（RED）。

实现后聚焦用例：

```text
cd backend && D:/Python/python.exe -m pytest \
  tests/customer_service/test_dispatch_service.py \
  tests/customer_service/test_dispatch_presence.py \
  tests/workers/test_cs_dispatcher.py -q --no-cov
53 passed, 1 skipped in 16.86s
```

其中的 skip 是显式的部署门槛，不是通过（见下）。

静态检查：

```text
compileall backend/customer_service/dispatch backend/workers/cs_dispatcher.py backend/config/cs_dispatch.py  → OK
ruff check <本任务全部新增/修改 Python 文件>  → All checks passed
git diff --check → 通过
docker compose config --services → 含 cs-dispatcher（YAML 与变量解析通过）
```

域内回归（无新增失败）：

```text
cd backend && D:/Python/python.exe -m pytest tests/customer_service tests/workers \
  tests/api/test_cs_dispatch.py tests/api/test_cs_agent_ws_ticket.py -q --no-cov
900 passed, 8 skipped in 140.29s
```

入口与真实 Redis 冒烟（非测试进程，真实依赖）：

```text
python -c "…cs_dispatcher.run_once()…"
  → mode 默认 off / instance id = <hostname> / status='disabled'（未触库）
  → run_forever(iterations=2) 返回 2 个 disabled 结果，心跳写入成功无告警

对真实 Redis（localhost:6379/0）:
  空候选 → set()（不访问 Redis）
  两个不存在的坐席 → set()
  key = cs:agent:presence:smoke-tenant:smoke-agent（与 P5 写侧一致）
  写入 presence key 后 → {'smoke-agent'}；删除后 → set()
  write_dispatcher_heartbeat('smoke-instance') → True（冒烟 key 已清理）
```

### 覆盖矩阵（说明测试真的锁住了什么）

- **SQL 契约**（编译后的 PostgreSQL 语句）：租户谓词、`ORDER BY priority DESC,
  created_at ASC, id ASC`、`FOR UPDATE SKIP LOCKED`、坐席"活动数→last_assigned_at
  NULLS FIRST→agent_id"三段排序、容量谓词 `< max_conversations`、活动数只计
  `offered`/`accepted`（绑定参数实测为 `['offered','accepted']`）、在线候选集合以绑定
  参数进入 `IN`。
- **事务语义**（脚本化 session）：空队列/无候选/满载/Redis 故障**均不改动任何行**；
  成功派单一次写入 handoff 状态、1 条 `offered` assignment、会话投影与 1 条持久事件；
  `flush` 早于 `commit`；广播发生在 `commit` 之后且复用持久 `event_id`；广播抛异常
  不回滚、`status` 仍为 `dispatched`；第二次派单递增 `assignment_version`/`attempt_count`；
  `dry_run` 只读不写；缺失可信租户时连事务都不开。
- **worker**：空队列不调用派单；跳过无候选租户并对后续租户继续；首个成功即停止；
  `off` 模式不触库；`shadow` 传 `dry_run=True`、`enforce` 传 `False`；心跳用实例级 key
  且 TTL=30；心跳异常不抛；`run_forever` 单轮异常不退出循环、每轮都写心跳。

> 说明：SQL 契约断言是在实现后对照 `sqlalchemy.dialects.postgresql` 的**真实编译
> 输出**逐条校正的（最初 4 条因 schema 限定名写错而失败，已按实际 SQL 修正并复跑）。
> 实现前的 RED 证据覆盖的是模块缺失与事务语义，不覆盖编译产物断言。

## 未验证项（部署门槛，不宣称通过）

1. **真实 PostgreSQL 并发与容量验收未跑**。实测共享库 `customer_service.handoffs`
   仍缺 `tenant_id/offer_expires_at/assignment_version/attempt_count`（028 迁移未应用，
   查询返回 0 列），因此"100 路并发 dispatcher 只产生 1 条活动 assignment / 1 个
   `assigned_agent_id`"、"500 次派单负载差 ≤1"、"任意时刻无人超载"**没有实测证据**。
   用例 `test_real_postgres_dispatch_requires_p1_schema` 显式 skip 并打印原因，
   不使用内存 fake 冒充。这与 Task 1/2/4 登记的同一门槛一致（028→029 部署链）。
2. **`FOR UPDATE SKIP LOCKED` 的真实并发行为未验证**：三处锁的编译产物已断言，
   但"多副本同时竞争时恰好一个赢"仍依赖 PG 语义，需在上条完成后补测。
3. **compose 服务未实际启动**：只做了 `docker compose config` 解析校验。原因是
   本机 dev 栈由其他会话共用（`agent-app-1` / `agent-rag-service-1` 均处于运行中），
   起 2 个副本会改动共享运行态，需用户确认后再执行
   `docker compose up -d cs-dispatcher`。
4. **端到端通知链路未验证**：offer 事件经 Redis pub/sub 送到坐席浏览器需要 P7
   的工作台"待接单"界面，属 P7 范围。
5. **Redis 故障演练未做**：fail-closed 只有 fake client 单测，未做真实断网/停 Redis。
6. **超总等待期（600s）工单只被跳过，不在这里关闭**：关闭并通知用户属 reaper（P7，
   方案 Q5）。
7. **会话已 `resolved` 仍会派单**：派单只按工单状态与截止时间过滤，不检查
   `conversation_status`；若 P7 reaper 未及时关闭工单会出现"已结束会话仍进队列"。
   加这个过滤会让该类工单永久占据队头（预读每次都命中它），故留给 P7 关单解决。

## 改动文件

- `backend/customer_service/dispatch/service.py`（追加 `dispatch_once`/`DispatchResult`）
- `backend/customer_service/dispatch/repository.py`（新增）
- `backend/customer_service/dispatch/presence.py`（新增）
- `backend/customer_service/dispatch/event_relay.py`（新增）
- `backend/customer_service/dispatch/__init__.py`
- `backend/workers/cs_dispatcher.py`（新增）
- `backend/config/cs_dispatch.py`（新增）
- `docker-compose.yml`（新增 `cs-dispatcher` 服务）
- `backend/tests/customer_service/test_dispatch_service.py`（新增）
- `backend/tests/customer_service/test_dispatch_presence.py`（新增）
- `backend/tests/workers/test_cs_dispatcher.py`、`backend/tests/workers/__init__.py`（新增）
- 本报告

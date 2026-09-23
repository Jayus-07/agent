# Travel Distributed Context — STOP G0 审计与设计

> 日期:2026-09-24 ｜ 基线 HEAD:07d39bf(STOP F 全绿,971 tests)
> 结论:**STOP_G0_PASS=true**(本节末尾复核清单)

---

## 〇、Detected WIP 清单(并行会话保护,任务书 §24)

开工前 `git status --short` / `git diff --cached` 实测,以下文件属于**其他会话**,本轮禁止覆盖 / reset / stash / 收编:

- **staged**:backend/context_budget/*(4)、customer_service/experts/base.py、customer_service/graph_builder.py、rag/chain.py、tests/context_budget/*(4)
- **unstaged**:backend/.env.example、config/tasks.py、infra/llm/*(6)、observability/metrics.py、orchestration/checkpoint/task_executor.py、services/task_service.py、tasks/*(7)、dev-svc.bat、docker-compose.yml、docs/customer-service/*、scripts/init_db.py
- **untracked**:observability/worker_metrics.py、sql/migrations/045、tests/infra/test_model_*、tests/test_task_observability.py、tests/test_worker_topology.py、docs/2026-09-23-代码审查报告-基线.md

本轮预计触碰:`orchestration/context/`(本轮主战场,他人零 WIP)、`orchestration/graph/travel_graph_node.py`、`orchestration/graph/runner.py`、`orchestration/context/routing_context.py`、`config/conversation_context.py`、`travel/slot_filler.py`、`observability/metrics.py`(**他人 WIP 文件,只追加自己的 metric hunk**)、tests/travel、tests/orchestration。docker-compose.yml / .env / .env.example **不动**(compose 内已有 `REDIS_ENABLED=true` 与 redis 容器,无需变更即可实机验证)。

---

## 一、G0-1 ConversationContext Store 现状

### 1.1 实现(`backend/orchestration/context/conversation_context.py`)

| 项 | 现状 |
|---|---|
| store | 进程内 `dict[(tenant_id,user_id,conversation_id)] → ConversationContext`(对象引用) |
| TTL | `CONVERSATION_CONTEXT_TTL_SECONDS`(env,默认 1800),按 `updated_at` 惰性过期 |
| LRU | `CONVERSATION_CONTEXT_MAX_ENTRIES`(默认 5000),dict 插入序 + 淘汰最旧 |
| lock | `threading.Lock`(仅保护 dict 结构) |
| 接口 | `get()`(**惰性创建并返回可变对象引用**)/ `peek()`(只读)/ `reset()` |
| 写语义 | **无 save/update——对象即存储**,调用方 get 到引用后就地改 |

`ConversationContext` 字段组:摘要槽位(destination/cities/origin/start_date/days/party_size/budget_cny/preferences/must_go/avoid/current_topic)、Evidence ID 组、funnel 候选组、路由组(active_domain/last_intent/last_action/pending_question)、Travel Run 组(travel_run_seq/travel_run_id/travel_stage/travel_pending)。

### 1.2 生产调用方全景(已 grep 实证)

| 调用方 | 读/写 | 用法 |
|---|---|---|
| `routing_context.assemble_routing_context` | 读 | `peek` → `snapshot()` 组装 routing_context |
| `routing_context.mark_domain_turn` / `set_pending_question` | 写 | `get` → `ctx.mark_turn(...)` 就地改(router_node 6 处 + runner) |
| `runner._resolve_followup_query` | 读+写 | `get` → `resolve_followup(q, ctx, ...)` 读槽位 → `apply_resolution_to_context(ctx, ...)` 就地写(overwrite_destination/set_topic) |
| `travel_graph_node._resolve_entry_mode` | 读 | `peek` → checkpoint miss 时 reconstruct 基底(destination/days/travel_run_id) |
| `travel_graph_node._sync_travel_run` | 写 | `sync_travel_run_to_context`(begin/stage/pending)+ `mark_travel_run_completed` |
| `sync_funnel_candidates_to_context` / `read_funnel_candidates_from_context` | 写/读 | funnel 候选跨轮载体(2026-09-23 E1) |
| `sync_travel_brief_to_context` | 写 | brief 摘要槽位 merge_slots(被 sync_travel_run_to_context 复用) |

**共用面结论**:Router ✓(routing_context/continuation/followup)、Travel ✓、Selection Funnel ✓、CS ✓(经 router_node `mark_domain_turn` 间接写)。四者同一进程单例,STOP G 必须整体迁移,不能只迁 Travel 路径。

### 1.3 真实调用链(任务书要求画出)

```
request
  ↓
assemble_routing_context (peek→snapshot)          ← routing_context.py:58
  ↓
routing_context {active_domain, brief_summary.travel_pending, travel_run_id}
  ↓
resolve_travel_pending (纯函数零 IO)               ← travel_pending_resolver.py:98
  ↓ 命中 → route_mode=travel → travel_graph_node
travel_graph_node
  ├ _resolve_entry_mode: peek (checkpoint miss → reconstruct)
  └ _sync_travel_run → sync_travel_run_to_context (get→就地改)   ← conversation_context.py:413
                      → mark_travel_run_completed (get→就地改)   ← conversation_context.py:486
```

### 1.4 Travel Run 现有语义(STOP F 冻结面)

- `begin_travel_run()`:seq+1 → `trv_{sha1(conv)[:8]}_{seq:03d}`,清 pending。
- `mark_travel_run_completed`:stage=completed + 清 pending,**保留 run_id 与摘要槽位**(归因/续改),软失败。→ §19 要求的「规划已完成 ≠ 会话 run 身份删除」**STOP F 已正确区分**,无冲突,不改契约。
- `clear_travel_run()`:清 seq/run_id/stage/pending、**保留摘要槽位**——但生产代码**零调用**(仅测试引用)。即 STOP F Deferred 的「取消规划」识别确实未实现,本轮 G3 闭环。
- **发现(与 §18 冲突点)**:`clear_travel_run` 把 `travel_run_seq` 清零 → cancel 后新规划 run_id 会重新从 `_001` 撞号,违背任务书 §18「杭州新规划必须 run_002」。处理:新增 `CANCEL_TRAVEL_RUN` 语义(**seq 单调保留**、stage=cancelled、清 pending、保摘要),`clear_travel_run` 方法保留不动(STOP F 契约不变,生产零调用不受影响)。
- **发现**:slot_filler/brief 有 `lodging` 槽位(`travel_pending_resolver._VALUE_SLOTS` 含 lodging、`models/brief.py:101`),但 `CONTEXT_SLOT_FIELDS` 白名单**不含 lodging** → lodging 摘要不进 context。T7(并发 budget+lodging)需把 lodging 纳入 `CONTEXT_SLOT_FIELDS`(向后兼容:仅新增可携带字段,brief 有值即同步)。

---

## 二、G0-2 多 worker 问题实锤(非理论)

探针脚本 `/d/tmp/stopg0_probe.py`(已运行,输出如下):构造 `store_A`/`store_B` 两个独立 `ConversationContextStore` 实例模拟 worker-A/worker-B 进程地址空间。

```
[worker-A] U1: run=trv_09f62c8c_001 stage=slot pending=tq_001 dest=福州   (pending 已创建)
[worker-B] U2: peek → None → routing_context = empty
>>> travel_pending missing / active_domain missing / travel_run_id missing 全部复现
>>> TravelPendingResolver 判定 active_domain=='travel' 不满足 → 放行正常路由,追问没人接住
```

**结论**:跨 worker 时 `assemble_routing_context` 返回空上下文,T1/T2/T3(续跑/PATCH/NEW_RUN)全部失效;仅当下一轮请求恰好落回同进程才可靠——「下一轮大概率同 worker」假设不成立(app 容器多 worker 扩容、4 个 Celery worker 与 app 异进程均已在架构上存在,`agent-app-1` Cmd 实测单 uvicorn 进程 + REDIS_ENABLED=true)。

---

## 三、G0-3 Redis 基础设施审计(复用,不新建)

| 项 | 现状 | 复用决策 |
|---|---|---|
| client factory | `infra/redis/client.py::get_redis()` 进程单例(同步 redis-py,`decode_responses=True`,pool max=`REDIS_MAX_CONNECTIONS`=20,socket timeout=5s,失败返 None + 60s probe cooldown) | ✅ **唯一客户端来源**,禁止业务模块新建 `redis.Redis(...)`(任务书禁令) |
| 配置 | `config/redis.py`:`REDIS_ENABLED`(compose 容器已 true)/`REDIS_URL`(redis://redis:6379/0)/`REDIS_KEY_PREFIX="agent:"` | ✅ key 前缀沿用 `agent:` |
| 序列化先例 | `infra/redis/session_store.py`(Redis Hash + JSON + 本地 fallback) | 参考其 fallback 形态;其「SET 整对象覆盖、无三元组 key、无 CAS」是本轮要超越的点,不照抄 |
| 共享状态先例 | 熔断状态 Redis 共享(`CIRCUIT_BREAKER_SHARED_ENABLED`,退回进程内=方向安全) | 复用「开关 + 可观测降级」模式 |
| 部署 | compose `redis`:redis:7-alpine,**maxmemory 512mb allkeys-lru**,**appendonly yes + everysec**,volume redis_data,宿主 127.0.0.1:6379 | T10 恢复实测可用;allkeys-lru 意味键可被驱逐 → 「shared ≠ durable」须在报告明示 |
| 测试先例 | `tests/test_task_admission.py::redis_client`(直连 REDIS_URL,flushdb 隔离,不可用 skip) | ✅ G4 真实 Redis 测试沿用该 fixture 形态 |

同步客户端抉择:既有调用链(路由层/图节点)为同步线程语义,复用 `get_redis()` 同步客户端与现状一致;不引 `redis.asyncio`(任务书 §23.6 禁止散落新建客户端)。

---

## 四、G0-4 PostgreSQL 现状与 backend 对比

- `sql/migrations/000~045` 全量扫描:**不存在** conversation_context / session_context / agent_context / conversation_state 任何表。不重复建表 ✓。
- 对比:Context 的职责是「**shared hot conversation state**」——TTL 短(1800s)、每轮读写、体积小、可丢失可重建(checkpoint + reconstruct 兜底)。Redis TTL 键天然匹配;PG 表需要迁移 + 清理任务 + 连接池占用,收益为零。**决策:Redis = shared hot state,PostgreSQL checkpoint = durable graph execution state**,两类职责不混(任务书 §G0-4 冻结方向)。
- T10 口径:Redis AOF everysec + volume 使重启可恢复;但 allkeys-lru 驱逐 = 可能丢。ConversationContext 定义为 **ephemeral shared session state(shared ≠ durable)**,丢失路径 = checkpoint resume / reconstruct / fresh(STOP F 已闭环,不 500)。

---

## 五、G0-5 并发语义实锤

探针三个场景(同脚本,真实输出):

1. **stale pending 覆盖(单进程也会发生)**:pending=tq_002 活跃时,tq_001 的 resolve 晚到执行 `set_travel_pending(None)` → tq_002 被误清。`set_travel_pending` 无 CAS。
2. **stale run 覆盖(单进程也会发生)**:run_001 处理中 NEW_RUN 出 run_002;run_001 的 reporter 晚到 `mark_travel_run_completed`(无 expected_run_id)→ **run_002 被误标 completed**。
3. **read-modify-write 结构**:所有写路径 = `get()` 拿共享对象 → 就地 setattr。同进程下字段级并发因共享引用通常都保留(GIL),**跨 worker 是结构性丢失**(B 根本拿不到 A 的对象)——lost update 不是概率问题,是结构问题。

→ 必须 G2 原子 mutation + version/CAS,详见冻结 Contract。

---

## 六、冻结 Contract(本轮实现准绳,不再边写边改)

### 6.1 架构

```
Application (router_node / runner / travel_graph_node / funnel / sync_*)
        ↓ 只依赖统一接口,不知 Redis key/JSON/Lua/TTL
ConversationContextRepository (Protocol: get/peek/save/mutate/delete)
        ├── MemoryConversationContextRepository   (单测/本地开发/显式 fallback)
        └── RedisConversationContextRepository    (Lua 原子 mutation,生产)
get_conversation_context_repository() 进程单例工厂
```

- `ConversationContext` dataclass 保留,新增 `version:int=1` 与 `lodging:str=""`(CONTEXT_SLOT_FIELDS 追加 lodging);新增 `to_dict()/from_dict()`(JSON 序列化 + schema_version 演进钩子)。
- `ConversationContextStore` 类保留(存量单测契约不变);**生产写路径全部切到 repository**,读路径 get/peek 返回**快照副本**(改副本不再影响存储——迁移期由测试守住「改快照无效」语义)。

### 6.2 Redis Contract

| 项 | 冻结值 |
|---|---|
| key | `{REDIS_KEY_PREFIX}conversation_context:v1:{tenant}:{user}:{conv}`,三元组组件 `urllib.parse.quote(safe="")` 编码(防 `:`/中文/注入) |
| value | JSON(禁 pickle):`schema_version=1` + `version` + 全部 context 字段 + `tenant/user/conversation_id` + `updated_at`/`expires_at` |
| TTL | 复用 `CONVERSATION_CONTEXT_TTL_SECONDS`(1800,不建第二套);仅**成功** mutation 刷新(Lua 内 SETEX 同事务),失败/冲突不刷新 |
| 写原子性 | Lua EVAL:GET→decode→CAS/version 校验→应用 mutation→version+1→SETEX,单 key 单脚本原子 |
| 失效读 | peek 未命中不创建键(与现 peek 语义一致);get 读不再惰性创建(读路径零写) |

### 6.3 Mutation 集(业务意图,禁整对象覆盖)

`MARK_TURN` / `MERGE_TRAVEL_SUMMARY{slots}` / `PATCH_TRAVEL_SUMMARY{slots,expected_run_id?}` / `START_TRAVEL_RUN{conv_hash8}`(seq 服务端 Lua 递增,run_id 服务端拼接) / `SET_TRAVEL_STAGE{stage}` / `SET_TRAVEL_PENDING{pending}` / `RESOLVE_TRAVEL_PENDING{expected_question_id}`(**CAS,不匹配=stale 丢弃**) / `MARK_TRAVEL_COMPLETED{expected_run_id}`(**CAS**) / `CANCEL_TRAVEL_RUN{expected_run_id}`(**CAS**;seq 保留、stage=cancelled、清 pending、保摘要) / `CLEAR_TRAVEL_RUN{expected_run_id}`(CAS;STOP F 语义) / `CLEAR_EVIDENCE` / `SET_TOPIC{topic}` / `OVERWRITE_DESTINATION{destination,keep_days}` / `SET_FUNNEL_CANDIDATES{candidates,run_id}` / `CLEAR_FUNNEL_CANDIDATES` / `APPLY_FOLLOWUP_RESOLUTION{overwrite_destination?,topic?}`。

### 6.4 Backend Policy(任务书 §八)

- `CONVERSATION_CONTEXT_BACKEND = redis | memory`(默认 **redis**;生效前置 = `REDIS_ENABLED=true` 且可连)。
- `CONVERSATION_CONTEXT_REQUIRE_SHARED`(默认 false):true 时 Redis 不可用 → **startup fail-fast + runtime fail-closed**(读=确定性 miss、写=拒绝且不落 memory,不产生「跨 worker 假一致」;error log + metric + health unhealthy);false 时 → fallback 进程内 memory + warning + metric + health degraded(可观测,不静默)。
- backend status 三态:healthy / degraded / disabled,复用熔断共享的开关+降级模式;挂 `/health` 响应。

### 6.5 Travel Run Lifecycle(G3)

```
NONE(seq=0) → ACTIVE_SLOT(stage=slot, pending∈) → ACTIVE_PLANNING(stage=planned)
    → COMPLETED(mark_travel_run_completed, run_id/摘要保留)
    → CANCELLED(CANCEL_TRAVEL_RUN, seq 保留单调 → 下一 run 递增,防撞号)
任意 ACTIVE 态可 PATCH/REPLAN/CONTINUE(不换 run);仅 NEW_RUN/CANCEL/NEW 会话换 run。
```

Cancel 识别(slot_filler 新增纯函数 `is_cancel_run_query`,与 `is_new_run_query` 同层同风格):「不规划了/取消这次行程/先不规划了/别规划了/取消规划」整段短句 → CANCEL;「不去海游馆了」类含具体地名的排除句 → PATCH avoid,不误判。挂点:`travel_graph_node` 入口(active run 存在 + 命中 cancel → 短路出取消文案 + CANCEL_TRAVEL_RUN mutation,不跑专家图)。

### 6.6 可观测(G5,冻结 label 口径)

- metrics(追加到 observability/metrics.py,仅自己的 hunk):`conversation_context_read_total{backend,status}`、`conversation_context_write_total{backend,status}`、`conversation_context_mutation_total{mutation,status}`、`conversation_context_conflict_total{mutation}`、`conversation_context_stale_update_total{kind=pending|run}`、`conversation_context_backend_error_total{backend,operation}`、`conversation_context_fallback_total{reason}`、`conversation_context_operation_seconds{operation}`。禁止 tenant/user/conv/run/question id 进 label(对齐 SQL Agent STOP C 低基数先例)。
- log 事件:`conversation_context.read/.mutate/.conflict/.stale_run/.stale_pending/.backend_degraded`、`travel.run.cancelled`。
- trace tags(在 STOP F4 打点位追加):`conversation_context_backend`/`conversation_context_status`/`conversation_context_version`/`travel_pending_question_id`。

---

## 七、G0 复核清单

| 问题 | 结论 |
|---|---|
| store 实现 | 进程内 dict 三元组 + TTL/LRU + threading.Lock,对象即存储、无 save |
| 多 worker 实锤 | ✅ 探针复现 pending/run/domain 全丢(§二) |
| Redis 基础设施 | ✅ `get_redis()` 唯一来源,prefix `agent:`,先例充分(§三),不新建客户端 |
| PG 已有表? | 无任何 context 表,不重复建表;Redis=hot shared / PG=durable checkpoint(§四) |
| 并发语义 | get-modify-set 结构,无 CAS;stale pending/run 覆盖复现(§五) |
| Contract 冻结 | §六完成 |

**STOP_G0_PASS=true** —— 依此文档进入 STOP G1。

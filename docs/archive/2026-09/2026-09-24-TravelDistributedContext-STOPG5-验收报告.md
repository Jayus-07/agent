# Travel Distributed Context — STOP G 最终验收报告

> 日期:2026-09-24 ｜ 基线:07d39bf(STOP F 全绿)→ 收口:654f168
> 审计:docs/2026-09-24-TravelDistributedContext-STOPG0-审计与设计.md

# 1. Verdict

```text
STOP_G_PASS=true
TRAVEL_DISTRIBUTED_CONTEXT_PRODUCTION_CLOSURE_PASS=true
TRAVEL_RUN_LIFECYCLE_FROZEN=true
```

判定依据:§28 Gate G1-G17 逐项通过(§10 归因证据支持 G16 的「零新增回退」口径)。

# 2. Before Architecture(STOP F 后真实状态)

- `ConversationContextStore` = 进程内 dict(`threading.Lock` + TTL 1800s + LRU 5000),key 为 (tenant,user,conversation) 三元组;**对象即存储**——`get()` 返回可变引用,调用方就地修改,无 save/mutation 概念。
- G0 探针实锤(非理论):
  - 双 store 模拟双 worker → `travel_pending / active_domain / travel_run_id` 全部 miss,`TravelPendingResolver` 判定条件不成立 → 跨 worker 追问丢失(T1/T2/T3 全部失效);
  - stale pending:tq_001 晚到 resolve 无条件 `set_travel_pending(None)` → 误清 tq_002;
  - stale run:run_001 晚到 completed 无 run CAS → 误标 run_002;
  - `clear_travel_run` 生产零调用(「取消规划」STOP F Deferred 未实现);其 seq 清零语义还会导致 cancel 后 run_id 撞号。
- Redis 基础设施已备但未用于 context:`infra/redis/client.py::get_redis()` 单例(池 20、timeout 5s、cooldown 60s)、`REDIS_KEY_PREFIX="agent:"`;compose redis = redis:7-alpine **allkeys-lru 512mb + AOF everysec**。
- PG `sql/migrations/000~045` 无任何 context 表。

# 3. Context Repository Architecture

```
Application(router_node / runner / travel_graph_node / funnel / CS 经 router_node)
   ↓ 统一接口(业务层不知 key/JSON/WATCH/TTL)
ConversationContextRepository (Protocol: get/peek/mutate/save/delete/status)
   ├ MemoryConversationContextRepository   单测 / 本地开发 / 显式降级
   └ RedisConversationContextRepository    生产(shared hot state)
        ├ 复用 infra/redis/client.get_redis() 进程单例(零新客户端)
        ├ 写:WATCH→apply_mutation(纯函数)→MULTI/SETEX,WatchError 退避重试(8 次)
        └ 降级策略:CONVERSATION_CONTEXT_REQUIRE_SHARED 决定 fail-closed / fallback
get_conversation_context_repository() 进程单例;mutation 应用逻辑 apply_mutation
为 Memory/Redis 共用的单一事实源(两 backend 行为一致,互证)。
```

# 4. Redis Contract

| 项 | 冻结值 |
|---|---|
| key | `{REDIS_KEY_PREFIX}conversation_context:v1:{tenant}:{user}:{conv}`,组件 `quote(safe="")` 编码 |
| schema | JSON(禁 pickle):`schema_version=1`+`version`+全字段+`updated_at`;`from_dict` 忽略未知字段(演进钩子) |
| TTL | 复用 `CONVERSATION_CONTEXT_TTL_SECONDS`(1800);仅 applied mutation 刷新(SETEX 同事务),stale/missing/noop 不刷 |
| version | 首建 1,applied mutation +1(Lua-free 单键 WATCH CAS) |
| backend policy | `CONVERSATION_CONTEXT_BACKEND=redis|memory`(默认 redis);`CONVERSATION_CONTEXT_REQUIRE_SHARED=true` → startup fail-fast(配置性缺失)+ runtime fail-closed(拒绝假写 memory);false → fallback memory + warning + metric + health degraded(可观测,不静默) |

shared ≠ durable:allkeys-lru 可驱逐、重启依赖 AOF;丢失路径 = checkpoint resume / reconstruct / fresh(不 500)。

# 5. Atomicity Contract

- **version 单调**:每个 applied mutation version+1;noop/stale/missing 不落库不刷 TTL。
- **mutation 表达业务意图**(16 个,禁整对象覆盖):MARK_TURN / MERGE_TRAVEL_SUMMARY / PATCH_TRAVEL_SUMMARY / START_TRAVEL_RUN(seq 服务端递增)/ SET_TRAVEL_STAGE(支持 if_stage_in 条件推进)/ SET_TRAVEL_PENDING(run CAS+同轮 question_id 保留)/ RESOLVE_TRAVEL_PENDING(**question_id CAS**)/ MARK_TRAVEL_COMPLETED(**run CAS**)/ CANCEL_TRAVEL_RUN(**run CAS**,seq 保留)/ CLEAR_TRAVEL_RUN(STOP F 契约)/ CLEAR_EVIDENCE / SET_TOPIC / OVERWRITE_DESTINATION / SET_FUNNEL_CANDIDATES / CLEAR_FUNNEL_CANDIDATES / APPLY_FOLLOWUP_RESOLUTION。
- **lost update 防护**:Redis WATCH/MULTI/EXEC + 随机退避重试(强竞争下 T7 实测无丢失);Memory 单锁内 apply。
- **CAS 失败 = stale 丢弃 + 观测**(log 事件 + `conversation_context_stale_update_total`),下一轮请求带最新快照自然重做。

# 6. Travel Run Lifecycle

```
NONE(seq=0,无 run)
  → ACTIVE_SLOT      stage=slot,     travel_pending∈(START_TRAVEL_RUN / SET_TRAVEL_PENDING)
  → ACTIVE_PLANNING  stage=planned,  pending 清(RESOLVE_TRAVEL_PENDING,question_id CAS)
  → COMPLETED        mark_travel_run_completed(run CAS;run_id+摘要保留归因,§19)
  → CANCELLED        CANCEL_TRAVEL_RUN(run CAS;stage=cancelled、pending 清、
                     run 身份清、**seq 单调保留**、摘要槽位保留=STOP F clear 契约)
```

- NEW_RUN 仅在显式「重新规划」(is_new_run_query,STOP F 词表冻结)时换 run;cancel 后新规划 seq 递增 → run_002 不撞号(§18 实测)。
- Cancel 识别三层:`is_cancel_run_query` 保守词表(整体取消命中;「不去X了」局部排除句排除)→ resolver cancel 路由(active run 期间)→ travel_graph_node 短路执行 CANCEL(不跑专家图)。
- 修复(§19 冲突点):不改 STOP F 的 `clear_travel_run`(seq=0,保留兼容),新增 CANCEL 语义承载「取消后 run 编号递增」。

# 7. Cross-worker Evidence

真实 Redis(db15 专用库)+ 双/三独立 repository 实例(各自独立 fallback Memory=独立进程地址空间,可见性只能来自 Redis),`tests/test_stop_g4_matrix.py`:

- **T1**(worker-A 建 pending → worker-B 续跑):worker-B 读到同 run `trv_*_001`、resolver 命中 continue、补槽后 stage=planned、days=3;
- **T2**(worker-A 大阪3天 → worker-B PATCH):same run、budget_cny=60000;
- **T3**(worker-A 大阪 → worker-B NEW_RUN 杭州2天):新 run_id、destination=杭州、days=2;
- **T4/T5/T6**(不同 tenant / user / conversation 跨 worker):全部不可见,正主可见。
- G8/G9/G10 Gate 由 T1/T2/T3 满足。

# 8. Concurrent Update Evidence

**T7**:两 worker 实例双线程并发(Barrier 同步 × 15 轮)`MERGE_TRAVEL_SUMMARY{budget_cny:60000}` 与 `{lodging:"难波"}` 交错写真 Redis:最终 `budget_cny==60000.0` 且 `lodging=="难波"` 且 `days==3`——零丢失,零 conflict 耗尽(WATCH 退避重试生效)。Memory 侧同构用例 50 轮 × 2 线程同样全保留。

# 9. Stale Update Evidence

- **T8 pending CAS**:pending=tq_002 活跃时,tq_001 的 RESOLVE 晚到 → `status="stale"`(detail 含 `pending tq_002 != expected tq_001`),tq_002 存活;正确 id 才 applied 清空并推进 planned。
- **T9 run CAS**:run_001 处理中 NEW_RUN 出 run_002;run_001 的 MARK_TRAVEL_COMPLETED 晚到 → stale,run_002 stage 未被污染;当前 run 正常收尾。
- SET_TRAVEL_PENDING 亦带 run CAS:旧 run 的 pending 写入被拒。
- 观测:stale 拒绝打 `conversation_context.stale_pending` / `stale_run` 事件 + metric。

# 10. Failure Recovery Matrix

| Redis | Checkpoint | Context | Expected | 实测 |
|---|---|---|---|---|
| healthy | hit | hit | checkpoint | T12 变体(既有 STOP F 语义保持) |
| healthy | miss | hit | reconstruct | **T13** ✓(brief 基底 destination/days) |
| healthy | miss | miss | fresh | **T14** ✓(确定性,不 500) |
| down | hit | unavailable | checkpoint | T12:checkpoint hit **不依赖 Redis**(flushdb 后仍 checkpoint) |
| down | miss | unavailable | deterministic degraded | **T10**:flush 模拟丢键 → context miss → fresh;**T11a**:require_shared=true → 写 raise `ContextBackendUnavailable`、memory 未被偷写;**T11b/c**:false → fallback memory + `status=degraded` + backend_error/fallback 计数 |

T10 口径:compose redis 为 AOF everysec(重启可恢复),但 allkeys-lru 下键可被驱逐——**shared ≠ durable**,应用层不伪称持久;checkpoint resume 不依赖 context(T12 实测)。

# 11. Isolation

- key 三元隔离(quote 编码,无裸拼接):`agent:conversation_context:v1:{t}:{u}:{c}`。
- T4 同 conv 同 user 异 tenant → worker-B 读 None;T5 同 tenant 同 conv 异 user → None;T6 同 tenant 同 user 异 conv → None;正主均可见。
- 身份永远来自当前请求(state.tenant_id/user_id),checkpoint/context 均不还原权限身份(设计原则 §29)。

# 12. Cancel Evidence

- **T15**:slot 态 run_001 取消 → stage=cancelled、pending 清、run 身份清、seq=1 保留、摘要(大阪)保留;随后杭州新规划 → run_002(§18:不恢复 run_001)。
- **T16**:`不去海游馆了` → cancel=False、resolver 不以 cancel 路由、图不被短路(stage 保持 slot);`这次旅行不规划了` → resolver 命中 resume_mode=cancel → CANCEL applied。词表单测:12 个整体取消表达全命中,8 个局部/无关表达全排除。

# 13. Observability

- **metrics**(`orchestration/context/context_metrics.py`,默认全局 registry,与 /metrics 汇合):`conversation_context_read_total{backend,status}`、`_write_total{backend}`、`_mutation_total{mutation,status}`、`_conflict_total{operation}`、`_stale_update_total{kind}`、`_backend_error_total{backend,operation}`、`_fallback_total{reason}`、`_operation_seconds{operation}`;标签仅 backend/operation/status/mutation/kind 低基数,禁 tenant/user/conv/run/question id。
- **logs**:`conversation_context.read/.mutate`(debug)、`.conflict`、`.stale_run`、`.stale_pending`、`.backend_degraded`、`.backend_error`;`travel.run.cancelled`(新增,与既有 new/completed/turned 同族)。
- **trace tags**:`conversation_context_backend` / `conversation_context_status` / `conversation_context_version` / `travel_pending_question_id`(STOP F4 既有 travel_run_id/resume_mode/status 之上追加)。
- **health**:`GET /health` 新增 `conversation_context: {backend, status}`(healthy/degraded/disabled)。
- 全部埋点软失败,观测故障不影响主链。

# 14. Files Changed

| 文件 | 变更 |
|---|---|
| `orchestration/context/context_repository.py` **新** | Protocol + Memory/Redis 实现 + apply_mutation 纯函数 + 工厂/fail-fast |
| `orchestration/context/conversation_context.py` | +version/lodging 字段、to_dict/from_dict、conversation_hash8;sync_travel_* 切 mutation(MERGE/START/SET_PENDING/RESOLVE/STAGE/COMPLETED 含 CAS);funnel sync/read 切 repo |
| `orchestration/context/context_metrics.py` **新** | 8 个 Prometheus 指标 |
| `orchestration/context/routing_context.py` | assemble/mark_domain_turn/set_pending_question 切 repo(MARK_TURN) |
| `orchestration/context/travel_pending_resolver.py` | cancel 路由分支(active run + 词表) |
| `orchestration/graph/travel_graph_node.py` | `_maybe_cancel_active_run` 短路;peek 切 repo;reconstruct_brief +lodging;`_stamp_context_tags` |
| `orchestration/graph/runner.py` | followup 读写切 repo(get 快照 + APPLY_FOLLOWUP_RESOLUTION mutation) |
| `config/conversation_context.py` | +CONVERSATION_CONTEXT_BACKEND / CONVERSATION_CONTEXT_REQUIRE_SHARED |
| `travel/slot_filler.py` | +`is_cancel_run_query` 与排除正则(is_new_run_query 词表冻结不动) |
| `app/api/routes/health.py` | +conversation_context 状态 |
| tests | `tests/orchestration/context/{conftest,test_context_repository}.py` 新;`test_continuation_routing/test_follow_up_resolution/test_funnel_cross_turn` 迁移 repo 语义;`tests/travel/conftest.py` +隔离 fixture;`test_run_context.py` 迁移;`tests/travel/test_cancel_lifecycle.py` 新 27 例;`tests/test_stop_g4_matrix.py` 新 19 例(真 Redis) |

# 15. Tests

| command | passed | failed | exit |
|---|---|---|---|
| `pytest tests/orchestration/context/test_context_repository.py -q --no-cov` | 22 | 0 | 0 |
| `pytest tests/orchestration/context/ -q --no-cov` | 89 | 0 | 0 |
| `pytest tests/travel/test_cancel_lifecycle.py tests/travel/test_run_context.py -q --no-cov` | 39 | 0 | 0 |
| `pytest tests/test_stop_g4_matrix.py -q --no-cov`(真 Redis) | 19 | 0 | 0 |
| `pytest tests/travel/ tests/orchestration/ -q --no-cov`(**STOP F 回归门**) | 1013 | 0 | 0 |
| 契约四测试 + 新增:`test_registry_consistency test_layer_consistency test_adr0001_dual_registry_merge + 上述` | 70 | 0 | 0 |
| 全量 `pytest tests/ -q`(含他人未提交 WIP 与环境依赖用例) | 6632 | 93 | 1(归因见下) |

**全量 93 failed 归因(零新增回退证明)**:失败分布于 tools/sql(14)、rag_upload/lineage/pipeline(24)、email 幂等(7)、llm infra(7)、cs/api(7) 等 35 个文件——**无一在 travel/orchestration/context 面**。对照实验:在**基线 commit 07d39bf 的干净 worktree**(不含本轮 5 commits,亦不含任何并行 WIP)跑全量 = **同样 93 failed / 3 errors**。集合精确对比:

- HEAD 独有 7 条:`test_task_observability.py`×4(**他人 untracked 半成品文件**,基线不存在该文件)、`cs/test_idempotency`×2、`test_pricing_snapshot`×1(并发/PG 时序波动,两次 HEAD 全量 90→93 的漂移正来自此类);
- 基线独有 7 条:llm pricing ×5、specialized_model_config、task_admission ttl(基线失败而 HEAD 通过,反向证明波动性);
- **可归因于本轮改动的失败:0 条**。

指定回归门(任务书 §26 三个目录)0 failed;较 STOP F 基线 971 净增(新增用例),无既有用例回退。

# 16. Commits

| hash | subject | scope |
|---|---|---|
| 8a19098 | docs(travel): STOP G0 audit——distributed context实锤与contract冻结 | docs |
| 1eb18d3 | feat(travel): STOP G1+G2——ConversationContext 分布式仓库与原子 mutation | orchestration/context+graph、config、tests |
| 865c579 | feat(travel): STOP G3——显式取消规划与 run lifecycle 收口 | travel、orchestration、tests |
| b35fef6 | test(travel): STOP G4——真 Redis 多 worker/故障恢复矩阵 T1-T16 全绿 | context_repository 重试、tests |
| 654f168 | feat(travel): STOP G5——ConversationContext 可观测性收口 | context_metrics、repository 埋点、trace、health |

全部双重路径限定提交(`git add -A -- <paths>` + `git commit -- <paths>`)。

# 17. Parallel Workspace Protection

- **Detected WIP**(开工前盘点,见 G0 文档 §〇):staged 14 文件(context_budget/customer_service/rag/tests)、unstaged 23 文件(infra/llm、tasks、metrics.py、docker-compose.yml 等)、untracked 10 文件(worker_metrics.py、migrations/045、infra 测试等)——**全部未触碰、未覆盖、未 stash/reset/收编**。
- 本轮触碰文件与他人 WIP 唯一交叠:`observability/metrics.py`——**已规避**(指标独立声明于 context_metrics.py,默认全局 registry 自然汇合,零改动他人 WIP 文件)。
- docker-compose.yml / .env 未改(容器内 REDIS_ENABLED=true 已满足,G4 用 db15 专用测试库隔离)。
- 无 accidental staging:5 次提交均 pathspec 限定,`git log --stat` 复核各提交文件集与声明一致。

# 18. Deferred

无 P0/P1 缺陷挂账。登记两项非阻塞事项:

1. **cancel 短路的实机(app 容器)端到端验证**:当前 app 容器运行 STOP F 镜像,本轮行为由 1013 项回归 + 真 Redis 矩阵覆盖;容器 rebuild 属部署窗口动作(涉及共享 compose 栈,按多会话纪律不在本轮擅自重启),下次 `devctl.bat restart backend` 窗口自然生效。
2. **Redis Lua 化(可选优化)**:当前 WATCH 乐观锁在 T7 强竞争压测下零 conflict 耗尽;若未来单会话写并发显著上升,可将 apply_mutation 迁移为 Lua 服务端脚本消除重试(接口不变,纯 backend 内部演进)。

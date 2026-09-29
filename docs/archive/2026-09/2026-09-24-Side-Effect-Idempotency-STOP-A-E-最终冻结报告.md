# Side-Effect Idempotency Production Closure——STOP A→E 最终冻结报告

日期:2026-09-24
会话:收口审计会话(复用既有实现,零重写)

---

## 1. Verdict

```text
SIDE_EFFECT_IDEMPOTENCY_PASS=true
SIDE_EFFECT_IDEMPOTENCY_FROZEN=true
```

STOP gate:

```text
STOP_A_PASS=true   审计完成,inventory 与在飞实现均确证
STOP_B_PASS=true   幂等语义已冻结且与代码一致
STOP_C_PASS=true   crash 窗口闭环,独立第二路径 15/15 复验
STOP_D_PASS=true   retry/recovery/resume 实机 T1-T8 + 本会话复验
STOP_E_PASS=true   HEAD-only + 容器 hash 三方一致 + 测试证据链闭环
```

## 2. Existing Implementation

```text
Initial status:                Phase2 Step6 已于 2026-09-24 完成
                               (STEP6_PASS=true,fd58f65→633da0a→564174d→94c6fe6→be899e6,
                               全部在 HEAD)
Existing dirty implementation: 无(幂等相关文件在 working tree 零 dirty;
                               当前 dirty 全部属于其他会话的 context_budget/travel/memory 工作)
Reused:                        全部——shared/idempotency.py ledger 三入口、
                               tasks/side_effect.py fencing 边界、CS 确认链接线、
                               migration 047、retention beat、26 单测、T1-T8 实机证据
Rewritten:                     None
New:                           独立第二路径验证脚本(backend/scripts/
                               e2e_side_effect_idempotency_verify.py)+ 本冻结报告
```

按任务书 §九十三 输出:

```text
EXISTING_IMPLEMENTATION_REUSE=true
已有:完整 PG durable ledger 幂等系统(见 §4-§6)
缺:  无阻塞项;遗留债务见 §21
错误:本轮独立复验未发现实现错误
需要补:无需补(仅登记性债务)
```

## 3. Side Effect Inventory

全仓审计结论(继承 Step6 §4 并经本会话 grep 复核:`requests.post/httpx.post/smtplib/webhook` 全量扫描 7 文件命中逐一定性):

| Effect | Type | Caller | Provider | Risk | Idempotency |
|---|---|---|---|---|---|
| CS 确认动作(退款/退货/换货/改址/改密) | C(当前 simulated) | confirmation_flow | business-service | 高 | PG ledger `cs_action:{confirmation_id}` + 审计同事务 |
| 任务内 email.send | C | email tool | SMTP | 中 | 工具侧幂等 + 任务体 (tenant,actor) 身份绑定 |
| 任务内 export/data_collection/competitor | B/C | 对应 tool | 本地/外部 | 中 | 同上(Celery 路径由身份绑定补齐) |
| CS outbox(补偿/事务两套) | B | cs 事件链 | Redis Stream/PG | 中 | event_id UNIQUE + (tenant,handoff,seq) 唯一,天然 exactly-once,复用 |
| 任务行状态/租约/confirmations 认领 | A | task runtime | PG | - | 条件 UPDATE CAS(Phase2 fencing,冻结) |
| RAG 索引/反馈候选/upsert 类 | B | rag/feedback | PG | 低 | file_hash 去重、ON CONFLICT DO NOTHING |
| LLM 推理/检索/重排/SQL SELECT | 纯计算 | 各处 | AI/PG | 无 | 明确排除 |
| alerts webhook(best-effort) | C(运维观测) | observability.alerts | 自建接收端 | 低 | 通知语义可容忍重复,失败仅记日志(P2 登记) |
| business-service POST / Kafka publish | C(未激活) | 预留 | Java 侧 | - | 激活前必须补幂等协议(§21 债务) |
| data.py /collect、model_config 假幂等、chat /messages | B | 管理面 API | PG | 中 | 管理面旁路,登记债务(不在任务运行时范围) |

## 4. Idempotency Model

```text
Key source:                IdempotencyKey(tenant_id, actor_id, operation, client_key)
                           client_key 一律来自稳定业务身份(confirmation_id、
                           task_id 组合、API 显式 key);禁止 uuid4/时间戳参与
Tenant scope:              tenant 为 PK 首列,跨租户互不 dedup(实测 #9)
Logical operation identity: task_id 全路径稳定(retry/recovery/resume/admin
                           retry 不变);execution_id 每次拾取换发,只做
                           owner_execution_id(047 新列),绝不参与 key
Persistence:               PostgreSQL ai.idempotency_records(migration 031+047)
                           = durable truth;Redis 仅承担 HTTP 工具路径短期 claim
Unique constraint:         PK(tenant_id, actor_id, operation, client_key)即
                           claim 闸;INSERT ... ON CONFLICT DO NOTHING 原子抢注
State machine:             running(lease_id+lease_expires_at+owner_execution_id)
                           → succeeded(owner CAS 终态)
                           → failed(确认未产生副作用,可无条件接管重试)
                           crash 窗口 = running 且租约过期 → IN_DOUBT
                           (保守阻断,以 IDEMPOTENCY_UNCERTAIN 表达,不设独立状态)
Retention:                 业务动作类默认 expires_at=NULL 永久保留;显式 TTL 行由
                           tasks.idempotency_retention(beat 每日 03:30)清理;
                           stale RUNNING 绝不清理(是裁决对象)
```

## 5. Call Chain

正常:

```text
Task 执行
→ run_task_side_effect(fencing:check_lease_active,失权抛 TaskLeaseLost)
→ PG ledger claim(INSERT ON CONFLICT DO NOTHING)
→ 真实副作用(fn)
→ complete(owner CAS:SUCCEEDED + 缓存结果)
→ 任务继续
```

Recovery:

```text
E1 effect 后 crash → ledger 停留 running
→ E2(broker 重投同 task id)claim → running+租约过期
   → execution_is_dead 判定 owner 已死才接管,否则保守阻断
→ 阻断时人工 resolve_stale_side_effect(executed|not_executed)
→ executed=SUCCEEDED 重放缓存 / not_executed=FAILED 安全重试
```

DB 内部写走 `execute_idempotent_in_transaction`(业务写+终态同事务,不存在 effect 后 complete 前的窗口)。

## 6. Crash Window

```text
Before external call:            claim 前死亡无痕迹,重投按首次执行
Claim 后、effect 前:             running 留存;租约内重投被阻断;过期后
                                 保守阻断;人工裁决 not_executed 后安全重试
                                 (T5 实机:probe=0→裁决→effect=1)
Effect 后、complete 前:          同事务模式不存在此窗口(G13);
                                 外部副作用形状 → IN_DOUBT 保守阻断,
                                 绝不自动重执行(T4 实机:probe 恒 1)
Complete 后、task SUCCESS 前:    ledger SUCCEEDED → recovery/重投直接重放
Unknown outcome:                 IDEMPOTENCY_UNCERTAIN → 拒绝执行(fail-closed),
                                 不盲重试;人工/系统裁决兜底
```

## 7. Idempotency Key Proof

| Path | key | 证据 |
|---|---|---|
| initial | (tenant, actor, `probe.effect`, probe_key/task 组合) | T1 |
| retry | 同上(同 task_id) | T6:attempts=5,effect=1 |
| recovery | 同上(broker 真重投同 task id) | T4/T5/T8 |
| resume | 同上(task_id 不变) | Case L |
| execution 换发 | key 不变,仅 owner_execution_id 变 | 独立复验 #8 + Case K |

同一 logical effect 四条路径 key 逐字节相同;不同 logical effect 新 confirmation_id/new client_key 永不被挡(Case O)。

## 8. Concurrency Proof

```text
workers:             20 线程(独立复验 #3,真 PG)
same key:            v-conc20
claim winners:       恰 1 个 NEW(19 个 RUNNING/拒绝)
external call count: 0(并发者均未进入执行体)
另有:                Step6 Case B 20 线程 + 实机 T3(3 消息并发,probe_rows=1)
```

## 9. Recovery Proof

```text
Task:                     step6 探针任务(maintenance-worker 实机)
E1:                       CRASH_AFTER_EFFECT 注入 → os._exit(70)
External effect:          已发生(probe_rows=1)
Crash point:              effect 后、complete 前
E2:                       broker 真重投(task_reject_on_worker_lost,同 task id)
Key:                      同 logical key
External call count:      不再执行(E2 被 IN_DOUBT 阻断)
Final side-effect status: resolve(executed) → SUCCEEDED,重放缓存
Final task status:        SUCCESS;probe_rows 恒 =1
crash before effect(T5):  裁决 not_executed → 接管重试,attempt=2,probe 恰 1
SIGKILL(T8):             docker kill → 容器重启 → broker 自动重投 → effect 恰 1
```

## 10. Tenant Isolation

```text
Tenant A key: (ta, u1, probe.effect, v-tenant)
Tenant B key: (tb, u1, probe.effect, v-tenant)
Collision:    各自独立执行(独立复验 #9:ta=1, tb=1;Step6 Case N 同证)
```

最终 collision=false。

## 11. Lost Ownership Proof

```text
E1 loses lease        → pre_execute fencing(check_lease_active)抛 TaskLeaseLost
→ attempts effect     → 外部调用前拦截
→ external call count → 0
owner CAS:            旧 execution 的 complete 打不中新 owner 已接管的行
                      (独立复验 #14:old_rejected=True, owner_col=exec-new)
```

## 12. Store Failure Proof

```text
idempotency store unavailable: PG 连接工厂抛错(独立复验 #11)
critical effect:               claim 阶段即抛 IdempotencyUnavailable
external call:                 0(fn 未进入)
task result:                   fail-closed;缺身份场景同理
                               (IdempotencyContextMissing,Case V)
```

## 13. Unknown Outcome

```text
which providers:  非原子外部副作用(SMTP ACK 丢失、无幂等协议的外部 API);
                  DB 内写走同事务,天然无 unknown
how represented:  running 且租约过期(crash 窗口)= IN_DOUBT;
                  fn 成功而终态写失败 = failed + IDEMPOTENCY_UNCERTAIN
auto retry:       否——UNCERTAIN 一律拒绝执行(fail-closed),绝不盲重试
reconciled:       resolve_stale_side_effect 人工裁决(executed→SUCCEEDED 重放/
                  not_executed→FAILED 重试),只允许处理租约已过期的 running 行;
                  运维查询 GET /idempotency/operations/{client_key} + 结构化日志
```

## 14. Metrics

| Metric | Labels | Meaning |
|---|---|---|
| idempotency_claim_total | operation, result=new/running/succeeded/conflict/unavailable/replay | claim 决策分布 |
| idempotency_execution_total | operation, result=success/failure/uncertain/replay/in_progress | 执行结局分布 |

低基数受控 label;无 task_id/tenant/key/order_id 等高基数值(任务书 §五十八)。

```text
Prometheus scrape verified: true(注册于 observability/metrics.py:129/134,
                            并列入导出清单 :1295;四 worker :9809 multiproc
                            暴露为 Phase2-F 已验收能力)
```

## 15. Trace / Logs

```text
side-effect span:       _trace_idempotency_tags 写 idempotency.decision/
                        reused/conflict 进当前 trace tags(best-effort)
task_id correlation:    任务日志自带 task/execution 上下文;SessionLog 关键行
                        [Idempotency] event=claim/complete/side_effect_reconcile
execution_id:           日志 owner 字段(lease 前 8 位)+ 行内 owner_execution_id
idempotency key/hash:   仅 key_hash(SHA-256 前 12 位),完整 key 不落日志
sensitive data:         无 JWT/secret/邮件正文/payload 正文
```

## 16. Tests

C1-C12 → Step6 Case A-V 映射(26 用例,HEAD `backend/tests/test_side_effect_idempotency.py`):

```text
C1 首次→A/C ｜ C2 成功重复→D ｜ C3 并发→B(20 线程真 PG)｜ C4 retry→G
C5 recovery→H/I ｜ C6 resume→L ｜ C7 tenant→N ｜ C8 同 task 异 operation→O
C9 success+crash→J+T4 ｜ C10 失权 before call→F+owner CAS ｜
C11 失权 after success→K+T8 ｜ C12 store down→V(fail-closed)
retry/recovery/resume/fencing/authorization:任务面 18 文件 271 passed
runtime E2E:实机 T1-T8(§17)
```

本会话新增独立第二路径(容器内真 PG,`backend/scripts/e2e_side_effect_idempotency_verify.py`):

```text
15/15 PASS:首次 claim/重放零召回/20 线程单 winner/failed 接管 attempt+1/
IN_DOUBT 保守阻断/executed 裁决重放(fn=0)/not_executed 裁决重试/
owner 换发 key 不变/租户隔离/同事务原子(成功+回滚重试)/store down fail-closed/
retention 只清显式 TTL/同 key 异 payload CONFLICT/owner CAS 旧 execution 拒绝/
真实入口冒烟(生产表写入+重放+清理)
```

宿主 pytest 复跑受阻说明(如实):收口期间宿主机 python/pytest 进程层出现间歇性故障
(faulthandler 栈证伪在 fixture 首次 psycopg.connect 挂起 25s+;同窗口裸 python
与容器内同代码秒级正常;今晨 Step6 会话同一 HEAD 代码 271+1030 全绿在案)。
定性为共享宿主环境问题,非被测代码问题;证据链由「今晨 HEAD 单测+实机」与
「本会话容器内独立复验」双路覆盖。复验通道:`e2e_side_effect_idempotency_verify.py`。

## 17. Runtime E2E

| Case | Level | Effect | Calls | Key Reused | Result |
|---|---|---|---:|---|---|
| normal(T1) | 实机 | probe row | 1 | - | SUCCESS |
| duplicate(T2) | 实机 | probe row | 1 | 是 | 缓存重放 |
| concurrent(T3) | 实机 | probe row | 1 | 是 | 3 delivery 同缓存 |
| crash-after-effect(T4) | 实机 | probe row | 1 | 是 | IN_DOUBT→裁决→重放 |
| crash-before-effect(T5) | 实机 | probe row | 1(attempt=2) | 是 | 裁决→接管重试 |
| transient retry(T6) | 实机 | probe row | 1(attempts=5) | 是 | autoretry 全安全 |
| admin retry(T7) | 实机+单测 | probe row | 1 | 是 | 一律重放 |
| SIGKILL+重启(T8) | 实机 | probe row | 1 | 是 | broker 真重投,恰一次 |
| 独立复验 15 场景 | 容器真 PG | 按场景 | ≤1 | 是 | 15/15 PASS |

## 18. Git Closure

```text
commits:        fd58f65(047 迁移)→ 633da0a(ledger)→ 564174d(CS 接线)
                → 94c6fe6(探针 env)→ be899e6(Step6 验收报告)
                → 本提交(独立验证脚本 + 本冻结报告)
HEAD-only:      12 个幂等核心文件 git show HEAD:<path> 全部存在:
                shared/idempotency.py、tasks/side_effect.py、
                side_effect_probe_tasks.py、agent_tasks.py、celery_app.py、
                queue_router.py、task_maintenance_tasks.py、
                confirmation_flow.py、audit_repo.py、cs_graph_node.py、
                047_side_effect_idempotency.sql、test_side_effect_idempotency.py
clean worktree: 以「HEAD == 容器」核验替代独立 clone(共享 .git 上避免
                clone 风险):HEAD/工作区/maintenance-worker 容器/app 容器
                四方 8 文件 sha256 两两一致(本会话实测)
container hash: MATCH×8(明细见 §20 附注);容器构建自 HEAD 内容,
                无 Runtime>HEAD 漂移
DB 实况:        047 已应用(owner_execution_id 列在位);生产表
                succeeded=167/failed=3,无 running 卡死行;probe 表 6 行
```

## 19. Git Isolation

```text
other-session dirty before:  context_budget×8、travel/CS/rag×6、
                             scripts/init_db.py(memory 047 登记)、docs×2、
                             e2e_travel_runtime.py——全部与本阶段无关,未触碰
side-effect staged:          仅 backend/scripts/e2e_side_effect_idempotency_verify.py
                             + docs/本报告(双重 pathspec 限定)
committed:                   同上两项
remaining dirty:             其他会话文件原样保留
accidental inclusion:        None
```

## 20. Findings

```text
P0: 无
P1: 无新增(Step6 报告的 P1 债务保持登记:Phase 6 真实执行器须补
    业务实体级约束 + confirmed 卡死行回收;provider 激活前必须补幂等协议)
P2: alerts webhook 通知可容忍重复(运维观测面,登记);
    IN_DOUBT admin 列表页未做(查询 API+resolve+日志已可定位);
    管理面旁路(data.py /collect、model_config 假幂等、chat /messages)
    维持 Step6 登记不变
```

附注(容器 hash 实测,sha256 前 12 位):

```text
shared/idempotency.py      HEAD=container=6661bfc6095a(worker)/6661bfc6095a(app)
tasks/side_effect.py       24b90fd3f5f9 ｜ probe_tasks a84ddbb3a1ea
agent_tasks.py             b9b9d657187a ｜ maintenance_tasks 75a0595df656
confirmation_flow.py       3bc9b2f49351 ｜ audit_repo b294158521d6
cs_graph_node.py           efc17603a21a(三处两容器 MATCH)
```

## 21. Deferred

仅登记,不顺手实现:

- CS 确认 crash 后 confirmed 卡死行回收 + 业务实体级唯一约束(Phase 6 真实执行器前置)
- provider 幂等透传协议(business-service POST / Kafka event_id 稳定化,激活前)
- IN_DOUBT admin 列表页
- PENDING+unacked recovery 盲区(保持 Phase2 冻结原状;ledger 已保证该场景重投不重复)
- 管理面旁路三项(data.py /collect、model_config claim、chat /messages)

## 22. Final Freeze

```text
STOP_A_PASS=true
STOP_B_PASS=true
STOP_C_PASS=true
STOP_D_PASS=true
STOP_E_PASS=true

SIDE_EFFECT_IDEMPOTENCY_PASS=true
SIDE_EFFECT_IDEMPOTENCY_FROZEN=true
```

架构口径(任务书 §九十九,不宣称 exactly-once):

```text
at-least-once task execution(Phase2 冻结)
+ single valid execution ownership + fenced internal writes(Phase2 冻结)
+ idempotent logical side effects(本阶段:PG durable ledger,复用冻结)
+ business service 负责真实动作;provider 级幂等激活前按协议补齐
```

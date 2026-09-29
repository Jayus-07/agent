# Phase 3 STOP A — 全仓审计报告（Production Reliability & Side-effect Closure）

> 日期：2026-09-24 ｜ 性质：只读审计，未修改任何运行时代码
> 前置：`PHASE2_ASYNC_RUNTIME_PASS=true` / `PHASE2_ASYNC_RUNTIME_FROZEN=true`（295fed6 + 9759cf6）
> 本报告是 Phase 3 唯一事实源审计；所有结论带 file:line 证据，找不到证据处明确标 `UNKNOWN`。

---

## 0. Verdict

**STOP A：PASS —— 允许进入 STOP B，但 STOP B 的安全前提必须包含本报告 §4.3 的三条护栏。**

- Phase 2 六层运行时（lease/fencing、retry、QueueRouter、admission、topology、idempotency）实现与文档口径一致，`backend/tests/test_phase2_runtime_contract.py` 13 个契约测试（C1×2/C2/C3×3/C4×3/C5×3/C6）全部有真实代码对应，无虚假冻结。
- **核心债务坐实**：PENDING stranded task 无任何主动恢复通道（sweeper 只扫 RUNNING；broker 重投是唯一发现者，最坏 1950s；broker 消息丢失则永久滞留，且 **resume 与 admin retry 双双无法重踢 PENDING**）。
- 副作用面：**全仓零 Provider-native 幂等**（无任何 `Idempotency-Key` 透传）；本地 durable ledger（`ai.idempotency_records` + owner CAS + IN_DOUBT 保守阻断）已闭环但**人工裁决无 API**；CS **业务实体级幂等缺失**（operation 级 ≠ 实体级）；**CONFIRMED 卡死确认存在**。
- Kafka 未启用（`KAFKA_ENABLED=false`），但 event identity 契约必须按任务书要求先冻结。

一句话生产口径（Phase 3 结束时必须仍然成立）：at-least-once delivery + lease/fencing 单一执行权 + bounded classified retry + effectively-once where semantics permit + unknown effect fail-closed reconciliation。禁止声称 distributed exactly-once。

---

## A1. PENDING 生命周期真相

### A1.1 真实状态图（无 QUEUED 状态，不虚构）

```
CREATE (INSERT, autocommit, status='PENDING')          task_service.py:85,92
  │
  ├─ publish 成功 → mark_queued 回填 celery_task_id+queue+queued_at   task_service.py:100-113
  │      （「已入队」= queued_at 非空 + celery_task_id 非空，无独立状态）
  │
  ▼ broker 队列 ──► worker 拾取（acks_late, prefetch=1）  celery_app.py:57-59
  │                    │
  │                    ├─ 状态短路守卫（CANCELLED/SUCCESS/PAUSED/WAITING_USER → NO-OP）agent_tasks.py:249-266
  │                    ├─ FAILED → 显式回 PENDING（重试轮）  agent_tasks.py:269-275
  │                    ▼
  │              try_acquire_lease：单条 CAS
  │              PENDING→RUNNING + 换发新 execution_id + lease_expires_at=now()+120s
  │              （同时兜 RUNNING+lease过期 的接管）      task_service.py:116-179
  │                    ├─ CAS 失败 → RUNNING_ELSEWHERE 退出（防双消费）agent_tasks.py:284-288
  │                    ▼
  │              admission acquire（fail-closed，defer 有界退避）agent_tasks.py:296-306
  │                    ├─ 拒绝 → release_lease_for_defer：RUNNING→PENDING（清租约字段）+ 消息 countdown 重投
  │                    ▼
  │              heartbeat 15s 续租 + execution_id fencing 写   lease_heartbeat.py:73-99
  │
  └─ publish 失败：
       main 入口 → 标 FAILED + HTTP 503（不残留 PENDING）      routes/tasks.py:103-134
       rag_index 入口 → 【孤儿：行残留 PENDING，无标记无兜底】  rag_upload.py:938-958（见 A2.3）

终态：SUCCESS / FAILED / CANCELLED ｜ 中间态：WAITING_USER / PAUSED（均白名单封闭）
```

### A1.2 status 枚举与 schema 事实

- 枚举全集（`backend/models/task.py:49-55`）：`PENDING / RUNNING / WAITING_USER / PAUSED / SUCCESS / FAILED / CANCELLED`。**没有 QUEUED**；列 `VARCHAR(32) DEFAULT 'PENDING'` 无 CHECK 约束（`backend/tasks/schema.sql:13`）。
- **存在**的证据列（schema.sql）：`queued_at`:35、`started_at`:36、`finished_at`:37、`queue`:27、`worker`:28、`execution_id`:29、`lease_heartbeat_at`:59、`lease_expires_at`:60、`recovery_count`:61、`retry_exhausted`:63、`celery_task_id`:24、`retry_count`/`max_retries`:23/25、`trace_id`:30、`biz_type/biz_id`:31-32。
- **不存在**：`dispatched_at`、`broker_message_id`、`dispatch_attempt`、`routing_version`（routing_version 只在 QueueRoute 内存快照与日志，`queue_router.py:40,59,187-207`，不落库）。
- tasks 表 schema 权威在 `backend/tasks/schema.sql`，由 `ensure_schema()` 启动幂等执行（`task_service.py:22,40-57`）；`backend/sql/migrations/` 下无 public.tasks 迁移（搜过全部 47 个）。
- 既有部分索引 `idx_tasks_lease_expiry ... WHERE status='RUNNING'`（schema.sql:66-67）；PENDING 无专用索引（有 `idx_tasks_status` 部分索引含 PENDING，schema.sql:70-71）。

### A1.3 PENDING 产生路径全集（2 INSERT + 5 UPDATE）

| # | 路径 | 位置 | 入队后回填 | publish 失败行为 |
|---|---|---|---|---|
| P1 | HTTP `POST /tasks` INSERT | `routes/tasks.py:81-89` → `task_service.py:85` | mark_queued ✅ | 标 FAILED + 503 ✅ |
| P2 | RAG 上传入队 INSERT（graph=rag_index） | `routes/rag_upload.py:938` → `index_task_runtime.py:40-63` | mark_queued ✅ | **孤儿残留 PENDING ❌**（rag_upload.py:1054-1062 不触碰行） |
| P3 | resume 认领 expected→PENDING | `task_service.py:473-488`（claim_for_resume） | ✅ | 回滚 PAUSED ✅ |
| P4 | admin retry（=resume allow_failed） | `routes/admin_tasks.py:142-143` | ✅ | 同 P3 |
| P5 | sweep 认领 RUNNING→PENDING | `task_service.py:648-679` | ✅ | revert 回 RUNNING（保 stale 语义下轮重试）✅ |
| P6 | worker retry：FAILED→PENDING | `agent_tasks.py:271-275` / `index_task_runtime.py:161` | （消息由 self.retry 发出） | unacked broker 重投兜底 |
| P7 | admission defer 释放 RUNNING→PENDING | `task_service.py:491-511`（调用 agent_tasks.py:175、index_task_runtime.py:206） | countdown 重投 | unacked broker 重投兜底（设计如此，agent_tasks.py:143-144） |

非路径澄清：broker 不可达时上传回退进程内索引不建行（`rag_upload.py:1062-1090` 直接 `_do_index_sync`）；`ai.agent_tasks`（002 迁移，整数主键）是另一张旧表，与本 tasks 表无关。

### A1.4 五分类判据可行性（Phase 3 设计输入）

| 分类 | 现有判据 | 可行性 |
|---|---|---|
| PENDING_NOT_DISPATCHED | `queued_at IS NULL AND celery_task_id=''` | ✅ 可靠（mark_queued 是 publish 成功后唯一回填点；main 入口 publish 失败会标 FAILED，仅 rag_index 路径会残留） |
| PENDING_DISPATCHED | `queued_at 非空 AND celery_task_id 非空 AND status='PENDING'` | ✅ |
| PENDING_UNACKED | DB **无法区分**「消息在 broker 队列」vs「已被 worker 取走 unacked」——broker 侧状态不落 DB | ❌ 无本地证据；只能时间阈值 + broker 侧查询辅助，保守按 stale 处理 |
| PENDING_DEFERRED | defer 释放时清租约字段、行回 PENDING；无专门标记列 | ⚠️ 只能靠 `updated_at` 新鲜度区分（defer 刚回队 updated_at 很新） |
| PENDING_ORPHAN | 消息确实丢失（broker 未持久化重启） | ❌ 与 UNACKED 在 DB 侧不可区分；同上保守处理 |

**结论（回答审计问题 5）**：五分类无法从现有 DB 列完全机械区分；UNACKED/ORPHAN/DEFERRED 三类只能合并为「PENDING 且超过阈值无进展」统一保守处理——这正是 STOP B 采用「保守重投 + 三层兜底护栏」而非精确分类的原因（见 §4.3）。

---

## A2. Dispatch Truth（enqueue/publish/pickup/lease 完整调用链）

### A2.1 全部 dispatch 点（12 处）

统一顺序模式：**DB 先行（autocommit INSERT/UPDATE 认领）→ apply_async 经 QueueRouter → mark_queued 回填**。无 commit-after-publish gap（连接 autocommit=True，`task_service.py:34-37`）；publish-after-commit gap = rag_index 路径的孤儿窗口。

| # | 位置 | 方式 | 经 QueueRouter | 失败行为 | msg id 落库 |
|---|---|---|---|---|---|
| 1 | `task_manager.py:174-175` enqueue_task | apply_async | ✅ resolve_for_task(:169) | 上抛→FAILED+503 | ✅ |
| 2 | `rag_upload.py:948-950` initial | apply_async | ✅ resolve_for_workflow(:947) | **孤儿 PENDING ❌** | ✅(:951-954) |
| 3 | `index_task_runtime.py:86-88` redispatch | apply_async | ✅ | resume 回滚 PAUSED / sweep 回滚 RUNNING | ✅ |
| 4 | `agent_tasks.py:456-457` retry | self.retry(queue=…) | ✅(:447-450，复查行状态仍 FAILED 才投) | unacked 重投 | ❌（celery_task_id 保持旧值） |
| 5 | `index_tasks.py:251-255` retry | self.retry | ✅ | 同上 | ❌ |
| 6 | `agent_tasks.py:180-182` defer | apply_async(countdown) | ✅(:176) | unacked 重投 | ❌ |
| 7 | `index_tasks.py:217-225` defer | apply_async(countdown) | ✅ | 同上 | ❌ |
| 8 | beat 9 项周期任务 | beat publish | ✅（全部 beat_queue()，celery_app.py:105-165） | Celery 自身语义 | N/A（maintenance 不写 tasks） |
| 9 | recovery republish | dispatch_task(type="recovery")（task_manager.py:410，beat 30s） | ✅ | revert 回 RUNNING | ✅ |
| 10 | resume / admin retry | dispatch_task（task_manager.py:328；admin_tasks.py:142-143） | ✅ fail-closed(:217-224) | 回滚 PAUSED | ✅ |
| 11 | metadata shadow | apply_async（rag/preprocessing/metadata_shadow.py:284-287） | ✅ | 上抛 | N/A（写影子表） |
| 12 | e2e 脚本 send_task | `scripts/e2e_async_runtime.py:165` | ❌（测试直发，非运行时路径） | — | — |

**关键缺口**：没有任何针对 PENDING 的兜底 republish。beat 两个维护任务（`task_maintenance_tasks.py:28-47`）只处理 RUNNING。`schema.sql:71` 的状态部分索引虽覆盖 PENDING，但无消费者。

### A2.2 worker pickup → lease 链（I1 的机制基础）

1. 消息达 `tasks.execute_agent`（acks_late=True `agent_tasks.py:469-476`；index 同 `index_tasks.py:180-187`）
2. 读行 + 状态短路守卫（`agent_tasks.py:249-266`）——终态/PAUSED/WAITING_USER 消息一律 NO-OP
3. FAILED → 显式回 PENDING（:269-275）
4. **`try_acquire_lease` = PENDING→RUNNING 唯一 UPDATE**（`task_service.py:116-179`）：单条 CAS 同时写 status/execution_id(新 uuid4,:143)/started_at(COALESCE)/heartbeat/expires(+120s)/worker，WHERE `status='PENDING' OR (RUNNING AND lease 过期)`（:159-168）
5. CAS 0 行 → `RUNNING_ELSEWHERE` 退出（`agent_tasks.py:284-288`）
6. admission acquire（fail-closed）→ 拒绝走 defer 释放循环
7. `LeaseHeartbeat` 15s 续租（`lease_heartbeat.py:73-99`，renew WHERE execution_id=self AND status=RUNNING）
8. execution_id 进 ContextVar，全部执行期写（status/progress/checkpoint）带 fencing（`task_service.py:377-387,431-442,589-601`）；信号层 `_owns_execution` 校验后才允许收尾（`signals.py:31-45,111-114,145-146`）

防双消费四层：(a) acks_late+prefetch=1+reject_on_worker_lost（celery_app.py:57-59）；(b) visibility_timeout=1950s；(c) lease CAS 认领即换 execution_id；(d) 执行期/信号期 execution_id fencing。**fencing 是「写时校验」而非单调递增 token**（UNKNOWN 风险：时钟回拨未防护，lease 判定用 DB now() 缓解）。

---

## A3. Recovery Truth + Gap Matrix

### A3.1 逐故障模式矩阵

| 故障模式 | 谁发现 | 多久发现 | 谁重投 | 换 execution_id | fencing | 释放 admission |
|---|---|---|---|---|---|---|
| RUNNING worker SIGKILL | stale sweeper（beat 30s→maintenance 队列） | TTL120+grace15+sweep30 ≈ **165s 上界** | sweeper CAS 认领→重投新消息（task_manager.py:379-424） | 新 worker 抢租约换发 | 旧 owner renew 0 行→lost；写点 TaskLeaseLost | token TTL 120s 自愈；takeover 不双占 |
| 同上第二层 | broker visibility 1950s 重投 | 1950s | broker；后到消息 lease CAS 判 RUNNING_ELSEWHERE | — | 同上 | 同上 |
| prefork 子进程被杀（WorkerLost） | Celery 立即 requeue（reject_on_worker_lost） | 立即 | broker 即刻；lease 仍活则第二条 RUNNING_ELSEWHERE | — | 同上 | 同上 |
| **worker 死于 lease 前（PENDING+unacked）** | **仅 broker visibility**；**无任何 DB sweeper 覆盖 PENDING**（task_service.py:634 `status=%s` 只传 RUNNING；zombie 收尸 task_service.py:241 同） | **1950s；消息丢失则永不** | broker 重投后新 worker 抢 PENDING 租约 | 是 | 不涉及（未持租约） | 无 token |
| sweeper 链路整体失效 | zombie_reconcile（beat 300s） | updated_at 停更 1890s+300s | 不重投，收尸 FAILED(ZOMBIE_RECONCILED) | — | 条件 UPDATE 复核心跳 | reconcile 顺带清 token |
| **zombie 兜底也失效（beat 单点死）** | **无自动层**，需人工 | UNKNOWN（无告警覆盖证据） | 无 | — | — | — |
| 重投消息×2 双活窗口 | 后到消息自身 lease CAS | 毫秒级 | 不重投 | — | lease CAS+fencing | 后到者未 acquire |
| sweep 重投失败（broker 不可达） | sweep 自身 | 当轮 | revert 回 RUNNING 保 stale，下轮重试（task_service.py:682-695） | — | — | — |
| 恢复循环 | recovery_count≥3（sweep 内判定） | 第 3 次后 | 终态 FAILED(ZOMBIE_RECONCILED)（task_service.py:698-725） | — | — | token 随终态释放 |
| admission Redis 故障 | acquire except 即时 | 即时 | defer 循环（10→60s 有界+jitter，60 次上限→FAILED(admission_rejected)） | defer 换消息不换行 | 租约被 defer 释放 | fail-closed 默认（config/tasks.py:164） |
| broker 重启消息丢失（RUNNING 中） | stale sweeper（不依赖 broker） | 165s | sweeper 重投全新消息 | 是 | 同 RUNNING 模式 | 同 |

### A3.2 Recovery Gap（Phase 3 必须补）

| Gap | 证据 | 定级 |
|---|---|---|
| **G-R1：PENDING 无主动恢复**。sweeper SQL `WHERE status='RUNNING'`（task_service.py:634）；zombie 同（:241）；测试 `test_recovery_defer_pending_not_rescanned`（tests/test_task_admission_runtime.py:345）表明 defer-PENDING 不重扫是有意设计——但该设计同时把「publish 失败孤儿」「broker 丢消息」「PENDING+unacked」三类全部挡在自动恢复之外 | task_service.py:634,241 | **P0** |
| **G-R2：孤儿 PENDING 无人工逃生门**。resume 对 RUNNING/PENDING 幂等 no-op（task_manager.py:310-314）；admin retry 走 resume_task 且 `record.status not in TaskStatus.resumable()` 先 409（admin_tasks.py:133-135）；PENDING 不在 resumable 集合 → **只能直连 DB 手改** | task_manager.py:310-314；admin_tasks.py:133-135 | **P0** |
| G-R3：rag_index initial publish 失败孤儿（P2 路径残留 PENDING，fallback 分支不触碰行） | rag_upload.py:938-958,1054-1062 | **P0**（真实产生路径） |
| G-R4：beat 单点：sweep 与 zombie 同走 beat→maintenance 队列，beat 死则全无自动恢复，只剩 broker visibility 空转重投 | celery_app.py:105-165 | P2 |
| G-R5：broker 无 disconnect 处理（仅 broker_connection_retry_on_startup=True）；Redis 持久化配置 UNKNOWN | celery_app.py:67 | P2 |
| G-R6：时钟回拨下 lease 判定未防护（依赖 DB now() 缓解） | task_service.py:159-168 | P3 |

### A3.3 STOP B 安全前提（机械可验证的 I1-I10 映射）

已存在的机制（STOP B 直接复用，不新建）：
- **I1 单一合法 execution**：lease CAS 认领即换 execution_id（task_service.py:143,159-168）——恢复消息与原消息并发时，只有一个能 CAS 成功
- **I2 业务幂等语义**：恢复只是重投消息，不触碰 `ai.idempotency_records`
- **I3 重新经 QueueRouter**：dispatch_task 内 resolve（task_manager.py:206-224）
- **I4 重新经 Admission**：agent_tasks.py:296-306（takeover 单槽语义，store.py:46-59）
- **I5 重新 acquire lease**：恢复消息与首次拾取走同一条 try_acquire_lease 路径
- **I6 旧 execution fencing**：execution_id 写时校验 + 心跳 lost 判定
- **I7-I9 终态/PAUSED/WAITING_USER 不复活**：拾取短路守卫 agent_tasks.py:249-266 + resume/cancel CAS 白名单
- **I10 SUCCESS 重投短路**：agent_tasks.py:249-259 SUCCESS 直接 NO-OP

R3 场景（recovery republish + 原 broker 消息后到 redelivery → effect=1）机制上已闭环：原消息后到 → 若新执行体 RUNNING 且租约活 → RUNNING_ELSEWHERE；若已终态 → 短路；若恰逢再次 stale → lease CAS 唯一认领。副作用层另有 ledger 兜底。**需要实机注入证明（R1-R3），非代码缺口。**

---

## A4. 全仓 Side-effect Inventory（A-F 分类）

保护机制基线：`ai.idempotency_records`（PG durable ledger，PK 四元组 tenant+actor+operation+client_key，`shared/idempotency.py:669,710-753,905-951`）；`run_idempotent_operation`（Redis claim 60s lease + PG 终态，Redis 不可用 fail-closed，idempotency.py:1221,1262）；`execute_idempotent_in_transaction`（业务写+ledger 同事务，:954-1045）；写操作审批门 `security/tool_approval.py`。

### A4.1 真实外部副作用（出网/不可撤销）

| # | 副作用 | 入口 | 分类 | 台账 | Provider 幂等 | crash window | 风险 | Phase3 动作 |
|---|---|---|---|---|---|---|---|---|
| E1 | SMTP 发邮件 | tools/email.py:40,132-138（Skill email.send、workflow call_email 上游） | **C**（三层台账）但带 **F 级窗口** | 审批+run_idempotent_operation+进程内指纹(600s) | ❌ SMTP 协议无键 | sendmail 成功→complete 前崩溃：Redis claim 60s 过期后同 key 重试**重新 NEW → 重复发信** | **高** | STOP C：业务稳定键延长 claim 语义/明确 IN_DOUBT；不可 SMTP 层修 |
| E2 | 无租户兼容直调发信 | tools/email.py:90 | **F** | 仅进程内指纹 | ❌ | 同 E1 且更弱 | **高**（任务内 _bind_task_identity 已缓解 agent_tasks.py:207） | STOP C：封死或强制身份 |
| E3 | 库存告警邮件（workflow step retry=2） | workflows/inventory_alert.py:303 | C（继承 E1） | 同 E1 | ❌ | 同 E1 | 中 | 随 E1 |
| E4 | 日报邮件+daily_reports 落库 | workflows/daily_report.py:175-221 | C + 无幂等 DB 写 | 邮件走 E1；报表无键 | ❌ | 双写不同事务，可能缺行或重发 | 中 | 随 E1；报表写加 run_id 键 |
| E5 | Kafka `message.created` | infra/messaging/kafka.py:83,103；conversation_store.py:117,234 | **E**（UNKNOWN 效果） | ❌ | ❌ event_id=uuid4 每次**新造**（kafka.py:103）；producer 无 enable_idempotence（:62-68） | fire-and-forget | 中（KAFKA_ENABLED=false 当前旁路） | STOP F 冻结 identity 契约 |
| E6 | Kafka `ai.reply.created`（Java 消费后发 WhatsApp） | infra/messaging/consumer.py:266 | **E** | 入站 SETNX 有；**出站无键** | ❌ | producer 重试/consumer 重放→重复回复用户 | **高**（启用后） | STOP F（启用前必须收口） |
| E7 | Kafka `conversation.state_changed` | customer_service/state_transition.py:179 | E | ❌ | 同 E5 | 同 | 低 | STOP F |
| E8 | POST business-service `/internal/messages`（CS_WRITE_SOURCE=java 时） | conversation_store.py:93-98；http/business_client.py:22,94,127 | **C 弱**（POST 不重试→丢而非重） | ❌ 无幂等键 | ❌ | 请求已到响应丢失→不重试不重复，但**状态漂移** | 低-中 | STOP C：Java 协议加 Idempotency-Key（激活 cutover 前必须） |
| E9 | POST `/internal/state-transitions` | state_transition.py:97 | **E** | ❌ | ❌；Java 侧是否幂等 UNKNOWN | Java 已生效而 Python 判失败 | 中 | STOP C 同上 |
| E10 | 告警 webhook（企微/钉钉/飞书） | observability/alerts.py:111-144 | D（无重试→丢而非重） | 进程内 300s 冷却 | ❌ | 无 | 低 | 不动（丢告警可接受，登记） |
| E11 | 数据采集写库（append/replace/upsert） | tools/data_collection.py:21,85；data_collection/writers/sqlalchemy_writer.py:104-151 | C（append 模式弱） | 审批+run_idempotent_operation("data.collect") | ❌ | append 跨进程重试重复行；replace 重跑清表 | 中 | STOP C：采集批次稳定键 |
| E12 | 竞品快照 append+watchlist | tools/competitor.py:73-164；competitor/pipeline.py:119-175 | C 弱（append-only 重试重复行，缓存量级注释自认 competitor.py:10-11） | run_idempotent_competitor_operation | ❌ | 同 | 低-中 | STOP C 低优先 |
| E13 | 竞品/网页抓取（出网读） | tools/web.py:29,144；competitor/anti_ban.py:366 | D | 不适用 | GET | 不适用 | 低 | 不动 |

### A4.2 数据库写副作用（本地，非出网）

| # | 写什么 | 入口 | 分类 | 依据 |
|---|---|---|---|---|
| D1 | CS 动作审计双表落库 | customer_service/repository/audit_repo.py:106-175 | **A** | ai.idempotency_records(operation='cs.action.persist') INSERT ON CONFLICT DO NOTHING 闸（commit 边界同 AsyncSession，UNKNOWN 是否真同事务——STOP E 复核） |
| D2 | CS 确认→执行链 | confirmation_flow.py:43,114-154；confirmation_repo.py:124 | **C**（simulated 无实害；真实执行器接入后=E） | DB 条件 UPDATE 认领 + run_idempotent_side_effect("cs.action.execute", cs_action:{confirmation_id})；执行体现为 simulate_execute（refund_service.py:102） |
| D3 | 转人工 handoff/工单 | dispatch/service.py:132,194；experts/handoff.py:134-155 | **A** | FOR UPDATE 行锁+活动唯一索引+idempotency_key 列+IntegrityError 复读 |
| D4 | 事件 outbox→relay→Redis Stream | dispatch/outbox.py:38,120 | **A** | append 与业务同事务；relay FOR UPDATE SKIP LOCKED+PUBLISH 回执；event_id UNIQUE 幂等 |
| D5 | RAG 索引写（upsert/先删后插） | rag/indexing/doc_registry_pg.py:286,536；chunk_store_pg.py:124,137 | **D** | upsert/替换语义天然幂等（embedding 调用是成本副作用 X1，有 Redis 缓存无持久台账） |
| D6 | 长期记忆写+每日衰减 | tools/memory.py:65；tasks/memory_maintenance_tasks.py:34 | **D**（衰减重复执行语义 UNKNOWN，需 STOP E 复核下限保护） | 自述幂等条件 UPDATE |
| D7 | 旅行偏好 upsert | tools/travel/preferences.py:125 | **D** | PK ON CONFLICT DO UPDATE |
| D8 | 库存告警事件/case 写 | workflows/inventory_alert.py:220-256 | **UNKNOWN** → STOP E 复核条件性 | 未细查 |
| D9 | tasks 元库写（租约/状态/fencing/checkpoint） | task_service.py 多处 | **A** | 全条件 UPDATE+execution_id fencing |
| D10 | metadata shadow 状态 | metadata_shadow_tasks.py:20 | **A** | 条件认领 |
| D11 | CS QA 日报 | cs_qa_tasks.py:31 | **D** | 按日覆盖写 |
| D12 | 副作用探针（测试设施） | side_effect_probe_tasks.py:65-143 | C（仅 SIDE_EFFECT_PROBE_ENABLED） | PG ledger，故意验证 IN_DOUBT 窗口 |
| D13 | 上传文件落盘 | routes/data.py:46；rag_upload.py:540-632 | **D** | uuid 文件名/tmp+os.replace+.bak |
| D14 | CSV 导出 | tools/export.py:12,116 | **D** | 同名覆盖；run_idempotent_operation("data.export") |
| D15 | 报告快照/Markdown | business_report/report_generator.py:152,224 | **D** | 时间戳命名不冲突 |
| D16 | 管理面配置写 | services/sys_config.py:235；model_config.py:171-909 | **UNKNOWN/弱**（部分 history 追加；model_config 强制 Idempotency-Key 但**未做 claim=假幂等**，Step6 §4 已登记） | STOP G 顺带登记 |

### A4.3 出网读（成本型）

X1 LLM/embedding/rerank（重试重复计费，**中**，供应商 chat API 无幂等键，C 类不可行→ledger 已兜）；X2 模型健康探测（300s 周期计费，低）；X3 腾讯 LBS×10（GET+缓存+熔断，低）；X4 business-service 读（低）；X5 报表 APIFetcher 可配 POST（**UNKNOWN**，STOP E 复核有无写语义）；X6 Prometheus 代理（低）。

**分类汇总**：A=4（D1,D3,D4,D9,D10）｜B Provider-native=**0**（全仓零 Idempotency-Key 透传）｜C=7（E1,E3,E4,E11,E12,D2,D12）｜D=8（D5,D7,D11,D13,D14,D15,E10,E13）｜E=4（E5,E6,E7,E9）+2 UNKNOWN（D8,X5）｜F=1（E2）。**无未分类项。**

---

## Provider Idempotency Matrix（STOP C 设计输入）

| Provider/出口 | supports_idempotency_key（现状） | 现用 identity | 稳定性 | Phase3 契约 |
|---|---|---|---|---|
| SMTP 邮件 | ❌ 协议无键 | ledger client_key（operation+业务键） | ⚠️ E1 的 Redis-lease 60s 窗口 | 不透传；本地键改 PG-first（claim 绑 PG 而非 Redis lease），window 收敛进 IN_DOUBT |
| business-service HTTP（POST messages/state-transitions） | ❌ 协议无键字段 | 无 | — | **可改造**：Python 生成稳定键透传 header `Idempotency-Key`，Java 侧落键表（跨仓协议，登记契约文档） |
| Kafka producer | ❌ 未开 enable_idempotence | event_id=uuid4 每次新造 | **不稳定** | STOP F：event_id=稳定逻辑身份哈希 + producer 开 enable_idempotence（启用前置条件） |
| LLM/embedding/rerank | ❌（供应商 API 不支持） | ledger 兜（run 内） | — | 不透传；保持 C 类 ledger + 成本观测 |
| 腾讯 LBS/抓取 | GET 天然幂等 | — | — | 不适用 |
| `ProviderIdempotencyCapabilities` 声明对象 | **不存在** | — | — | STOP C 新建：supports_idempotency_key/header/scope/retention/replay_semantics/status_lookup |

禁止用作 provider identity 的：execution_id / attempt / random uuid / timestamp（现状 E5 的 uuid4 event_id 正是反例）。

---

## Business Entity Idempotency Gap（STOP D 设计输入）

现状是 **operation 级（confirmation_id 级）幂等，无业务实体级幂等**：

1. ledger key = `(tenant, user_id, 'cs.action.execute', cs_action:{confirmation_id})`（confirmation_flow.py:146-154），confirmation_id 每次发起 `uuid4` 新生成（customer_service/action.py:99、experts/action.py:379）→ **两次逻辑等价确认 = 两个 key = 都执行**。
2. `customer_service.confirmations` 表唯一约束只有 `confirmation_id UNIQUE`（sql/migrations/006_customer_service.sql:195），**没有 tenant_id 列**（ORM 同 models/confirmation.py:22）。
3. 创建入口无防重：`_build_new_proposal`（experts/action.py:193-215）不查 has_pending、不查同 target 历史；同 session 整行覆盖（confirmation_store.py:235-242），**跨 session/跨 conversation 可并存多条同实体 pending**。
4. 已有保护（保留）：并发双确认同一 pending 行 → claim 条件 UPDATE 只有一方成功（confirmation_flow.py:171-189、confirmation_repo.py:119-143）；审计落库 INSERT ON CONFLICT（audit_repo.py:117-175）。
5. Step6 验收报告 §18.2 已登记此缺口（docs/2026-09-24-Phase2-Step6-Side-Effect-Idempotency-验收报告.md:283-286）。

**STOP D 方案**：业务实体唯一闸 = DB 硬约束（新迁移：`cs.business_entity_guards` 或 confirmations 加 `(tenant_id, action, entity_type, entity_id, scope, active_state)` 唯一/部分唯一索引），逻辑 identity = tenant_id + business_action + entity_type + entity_id + semantic fingerprint（refund_scope 等）；创建 confirmation 时 INSERT ON CONFLICT 拒绝/合并；Redis 只能做前置快查，**权威必须是 DB**。

---

## IN_DOUBT / Confirmation Recovery Gap（STOP E 设计输入）

1. **IN_DOUBT 表达**：非独立 DB 状态 = ① running+租约过期+无接管判定 → 读取期判 UNCERTAIN 保守阻断（idempotency.py:807-821）；② failed+error_code=IDEMPOTENCY_UNCERTAIN（:792-797）。
2. **人工裁决函数存在但无 API**：`resolve_stale_side_effect`（idempotency.py:1048-1108，executed→SUCCEEDED / not_executed→FAILED），全仓调用方仅测试与注释；admin 路由 grep 零命中。唯一查询面 `GET /idempotency/operations/{client_key}`（routes/idempotency.py:23-53，单键白名单字段）。**当前裁决=直连 DB 手工 UPDATE。**
3. **CONFIRMED 卡死坐实**：claim 在独立事务提交 confirmed（confirmation_store.py:261-269）后进程崩溃 → 行永久 confirmed；`expire_stale` 只扫 `state=='pending'`（confirmation_repo.py:179-207）；**无 confirmed/executing 超时回收**。Step6 §18.2 登记。
4. **EXECUTING 从不落库**：CS 执行是请求内同步调用（confirmation_flow.py:196），内存 transition 校验但不写 executing 状态；崩溃后 ledger 留 running+300s 租约，过期后因**未传 takeover_allowed**（confirmation_flow.py:147-154）永久保守阻断。
5. **VERIFYING 是死状态**：枚举与迁移表定义了（confirmation.py:30,44-52）但全仓无迁入代码，且 DB CHECK（006:198-206）不含 'verifying'/'not_required'——真写入即 CHECK violation。
6. **CS ledger 调用未传 owner_execution_id**（confirmation_flow.py:147-154）→ 终态 CAS 缺 owner 子句，弱于任务运行时双条件 CAS。
7. `run_task_side_effect`（fencing+execution_is_dead 接管判定的完整形态）**无生产调用方**（仅测试引用）——自动恢复能力已建成未接线。
8. `resolve` 之外的恢复策略分类（SAFE_TO_RETRY/KNOWN_SUCCESS/KNOWN_FAILED/IN_DOUBT/MANUAL_RECONCILIATION）目前不存在；禁止 catch Exception→retry 的红线现状守得住（接管需显式 takeover_allowed）。

---

## Kafka Event Identity Gap（STOP F 设计输入）

- Kafka 真实代码存在但**默认关闭**（KAFKA_ENABLED=false，config/messaging.py:15；compose 显式 false docker-compose.yml:170,788）；consumer 双开关默认关（messaging.py:59）。
- **event_id = str(uuid.uuid4()) 每次调用新造**（kafka.py:103）→ 业务层重调 publish_event 生成全新 id；producer retries=3 是传输层同消息重发（id 不变）但未开 enable_idempotence（:62-68）→ broker 端可产生重复副本。
- 调用点三处：state_transition.py:178-193、conversation_store.py:116-127/233-243、consumer.py:246-248。
- 对比正面样板（已稳定的两处，STOP F 沿用其形态）：CS realtime/outbox 事件 event_id 落库 UNIQUE + relay 复用（realtime.py:274-291、event_repo.py:27-48、outbox.py:57-80）。
- **任务事件（Redis pub/sub `agent:task:events:<task_id>`，task_manager.py:530-546）完全没有 event_id/seq**——retry/恢复重投后 SSE 事件重复，客户端无从去重（P2，随 STOP F 一并冻结契约）。
- 现状结论：Kafka 未启用 ⇒ STOP F 只冻结 contract（event_id=stable logical identity：tenant+aggregate+type+version+occurred_at 派生哈希；producer retry 不变；consumer dedup(event_id)）+ 单测锁形状，不改运行时。

---

## Observability Gap（STOP H 设计输入）

已有（metrics.py:1253-1309 + admission :1185-1232）：task_enqueued_total、task_terminal_total、task_execution_duration_seconds、task_queue_wait_seconds、task_lease_events_total、task_fenced_write_total、**task_retry_total**（:1293，agent_tasks.py:318）、**task_recovery_total**（:1299，task_manager.py:375）、task_authorization_denied_total、admission 全套 8 个。multiproc :9809 四 worker 已闭环（9a4c030 修复 + test_worker_metrics_multiproc_timing）。

**缺失 6 个（全仓零命中）**：task_pending_stale_total、task_pending_recovered_total、task_pending_recovery_latency、idempotency_in_doubt_total、idempotency_reconciliation_total、provider_idempotency_reused/conflict_total。

高基数 label：**无违规**，且有明文禁令（metrics.py:62-64,1181-1183,1242-1247）；idempotency key 只落 sha256 前 12 位摘要（idempotency.py:35-37）。STOP H 新增指标沿用同一禁令。

---

## Configuration Closure（STOP H 设计输入）

- 集中读取：`backend/config/tasks.py`（全部 os.getenv 集中于此，业务代码无散落读取）。关键默认：lease TTL 120s(:98)、heartbeat 15s(:99)、sweep 30s(:101)、grace 15s(:102)、max recoveries 3(:104)、visibility 1950s(:116-117，>hard 1830s 推导注释 :110-115)、zombie 1890s(:80-82)、admission fail-closed(:164)+四层限额(:185-190)+defer 10/60/60(:229-236)。
- **根/.env.example 与 backend/.env.example 均未暴露任何 TASK_LEASE_*/TASK_RECOVERY_*/TASK_ADMISSION_*/VISIBILITY/IDEMPOTENCY_* 项**（逐行核对，仅队列名与并发数 :161-171）。
- **关系校验不存在**：heartbeat<lease、visibility>hard、grace<sweep 等只以注释存在（config/tasks.py:93-97,110-115）；config/startup.py 只校验 DB 池/LLM/auth（:67-359），不校验任务运行时关系。负数/非法枚举有导入期 fail-fast（:165-182）。

---

## P0/P1/P2/P3 风险表

| 级 | 风险 | 证据 | 归属 STOP |
|---|---|---|---|
| **P0** | PENDING stranded 无主动恢复：1950s 最坏 + broker 丢消息永久滞留 | task_service.py:634,241 | B |
| **P0** | 孤儿 PENDING 连人工通道都失效（resume no-op + admin retry 409） | task_manager.py:310-314；admin_tasks.py:133-135 | B |
| **P0** | rag_index publish 失败残留 PENDING 孤儿（真实产生路径） | rag_upload.py:938-958,1054-1062 | B |
| **P0** | CONFIRMED 卡死（expire_stale 只扫 pending）+ 跨 confirmation 重复执行（真实执行器接入即事故） | confirmation_repo.py:179-207；action.py:99 | D+E |
| **P0** | IN_DOUBT fail-closed 无裁决 API → 无法收口（运营死角） | idempotency.py:1048-1108 无调用方 | E+G |
| **P1** | Provider 幂等契约缺失：全仓零 Idempotency-Key；E1 邮件 Redis-lease 60s 重复窗口；E2 无租户直调 | email.py:78-90；kafka.py:103 | C |
| **P1** | 业务实体级幂等缺失（operation≠entity） | 006:195；experts/action.py:193-215 | D |
| **P1** | Kafka/任务事件 identity 不稳定（启用前必须冻结） | kafka.py:103；task_manager.py:530-546 | F |
| **P1** | run_task_side_effect（自动接管判定）建成未接线 | 仅测试引用 | C（接线决策） |
| **P2** | IN_DOUBT/stale/恢复延迟 6 指标缺失；配置未文档化+无关系校验 | §Observability/§Configuration | H |
| **P2** | VERIFYING 死状态 + DB CHECK 与枚举不一致；CS ledger 未传 owner_execution_id | confirmation.py:30；006:198-206；confirmation_flow.py:147-154 | E |
| **P2** | beat 单点、broker 无 disconnect 处理、任务事件无 event_id | §A3.2 G-R4/R5 | H 登记 |
| **P3** | 时钟回拨 lease 判定、E12 快照 append 重复、D8/X5 UNKNOWN 复核 | §A4 | 登记 |

---

## STOP B-H 精确实施顺序与预计修改文件

顺序依据：B 是 P0 且不依赖其他；C 的 provider 契约是 D/E 的工具层前提（D 的实体闸独立于 C，但 D 依赖 B 的 admin 逃生门做联调）；E 依赖 C 的 reconcile 原语与 D 的实体闸；G 依赖 E 的裁决语义；F 独立可穿插；H 收口横切。

| STOP | 内容 | 预计修改/新增文件（新增标 +） |
|---|---|---|
| **B** | PENDING Recovery Accelerator：stale PENDING detector（beat，复用 maintenance 队列）+ 保守重投（经 QueueRouter/dispatch_task 现有链）+ 配置化阈值 + admin/resume 对 PENDING 的逃生门 + rag_upload 孤儿修复 | +`backend/tasks/pending_recovery_tasks.py`；`task_service.py`（+find_stale_pending、+claim_for_pending_recovery CAS）；`task_manager.py`（+recover_pending，resume no-op 语义保留、新增独立恢复入口）；`config/tasks.py`（+TASK_PENDING_RECOVERY_*）；`routes/admin_tasks.py`（PENDING 逃生门）；`rag_upload.py`（publish 失败标行）；+`schema.sql`/迁移（queued_at 部分索引）；beat 注册 celery_app.py |
| **C** | ProviderIdempotencyCapabilities + 稳定键派生（业务身份哈希，禁 execution_id/attempt/uuid/timestamp）+ E1 邮件窗口收敛 + E2 封死 + business-service 契约文档 | +`backend/shared/provider_idempotency.py`；`tools/email.py`；+`docs/contracts/provider-idempotency-protocol.md`；`shared/idempotency.py`（+capabilities 挂钩） |
| **D** | Business Entity Unique Guard：实体闸表/部分唯一索引 + confirmation 创建接入 + 语义指纹 | +`backend/sql/migrations/049_business_entity_guard.sql`；+`backend/customer_service/entity_guard.py`；`experts/action.py`、`confirmation_flow.py` |
| **E** | CONFIRMED/EXECUTING 卡死回收 + reconcile 分类（SAFE_TO_RETRY/KNOWN_SUCCESS/KNOWN_FAILED/IN_DOUBT/MANUAL_RECONCILIATION）+ resolve 服务化 + VERIFYING/DB CHECK 修正 + owner_execution_id 补传 | `confirmation_repo.py`（+claim_stale_confirmed）；`confirmation_flow.py`；+`backend/customer_service/reconciliation.py`；+migration（CHECK 修正）；`shared/idempotency.py`（+分类判定只读函数） |
| **F** | Kafka/任务事件 identity 冻结：stable event_id 派生 + producer enable_idempotence 开关 + consumer dedup 契约 + 任务事件补 seq/event_id | `infra/messaging/kafka.py`；+`backend/shared/event_identity.py`；`task_manager.py`（任务事件 envelope）；+契约文档 |
| **G** | Operator/Admin Recovery Plane：stale/IN_DOUBT/idempotency/execution/recovery history 查询 + retry/reconcile-success/reconcile-failed/cancel/resume/expire 操作（全部走 service/runtime API，RBAC+audit+tenant 隔离+CAS） | +`backend/app/api/routes/admin_recovery.py`；`admin_tasks.py`（对齐）；+audit 挂钩 |
| **H** | 6 个缺失指标 + .env.example 配置面 + 启动关系校验 fail-fast + Final Closure 报告（R 矩阵实机） | `observability/metrics.py`（+6 指标）；`config/tasks.py`（+validate_relations）；`config/startup.py`（挂钩）；`.env.example`×2；+Final 报告 |

每个 STOP 独立提交（审计→实现→测试→实机证据→文档→commit，路径限定）。

## 必须新增的测试（Failure Injection Matrix 映射）

| 用例 | 验证 | 归属 |
|---|---|---|
| R1：建行后 publish 前崩溃（monkeypatch apply_async 抛）→ 不永久 PENDING | B | B 系列单测 + e2e |
| R2：publish 成功、worker lease 前 SIGKILL → 阈值窗口（非 1950s）内 recovery 重投 | B | +`tests/tasks/test_pending_recovery.py` |
| R3：recovery republish + 原消息后到 redelivery → effect=1（probe 表行数恒 1） | B | 扩展现有 probe E2E |
| R4：provider 支持键：crash 后同键重试 effect=1（capabilities 对接） | C | +`tests/test_provider_idempotency.py` |
| R5：provider 不支持键：IN_DOUBT、无盲重试 | C/E | 同上 |
| R6：双 confirmation 同实体同动作并发/先后 → 业务 effect=1 | D | +`tests/customer_service/test_entity_guard.py` |
| R7：admin reconcile 与 worker retry 并发 → CAS 正确 | E/G | +reconciliation 并发测试 |
| R8：producer 重试 event_id 稳定 | F | +`tests/messaging/test_event_identity.py` |
| R9：admission Redis 故障 fail-closed | 已有基线复跑 | 回归 |
| R10：PG ledger 故障 fail-closed | 已有（V 用例）复跑 | 回归 |
| 回归门 | pytest backend/tests/test_phase2_runtime_contract.py（13 契约）+ tasks/queue/recovery/retry/admission/lease/pause/resume/cancel/idempotency/topology 全量绿 | 每 STOP |

## Phase 2 frozen 文件红线（原则上不修改）

- **绝对只读**：`backend/tests/test_phase2_runtime_contract.py`（新增能力以新增测试表达；任何触碰必须在 commit message 说明必要性）
- **只加不改语义**：`tasks/queue_router.py`、`tasks/retry_policy.py`、`tasks/lease_heartbeat.py`、`tasks/admission/*`、`tasks/error_taxonomy.py`、`services/task_service.py` 的既有 CAS/白名单（`models/task.py` 状态机白名单不动）、`tasks/agent_tasks.py` 的 lease→admission→fencing 主干、`shared/idempotency.py` 的 claim/owner-CAS/UNCERTAIN 判定
- **语义红线**：at-least-once 语义、acks_late/prefetch、visibility_timeout 推导（>hard+余量）、RUNNING 快照失权、I1-I10 既有机制
- tasks 表结构变更只走 ensure_schema 增量（幂等 ALTER IF NOT EXISTS）+ 独立迁移文件登记 MIGRATION_TARGETS

---

## 附：UNKNOWN 清单（如实登记，未猜测）

1. Redis broker 持久化配置（部署层事实，代码不感知）
2. Java 侧 `/internal/state-transitions` 是否幂等（跨仓）
3. D1 审计闸与业务写是否真同事务提交边界（AsyncSession commit 位置）
4. D6 记忆衰减重复执行下限保护、D8 库存 case 写条件性、X5 报表 POST 数据源写语义（STOP E 复核）
5. beat/队列的监控告警覆盖（是否有 G-R4 场景的告警）
6. 运维侧是否存在手工 SQL 兜底手册（G-R2 的现实 workaround）

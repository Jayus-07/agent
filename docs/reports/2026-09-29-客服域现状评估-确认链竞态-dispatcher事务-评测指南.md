# 客服域（Customer Service）现状评估 —— 含确认链竞态、Dispatcher 事务顺序、评测集运行指南

> 生成日期：2026-09-29 ｜ 性质：只读代码审查报告（评估用，非实施记录）
> 读者：外部 AI 评审（GPT）。本文自包含，不依赖仓库上下文。
> 口径来源：全部结论来自对工作区代码的实读（标注 `文件:行号`），未运行测试；「官方报告」指 `docs/archive/2026-09/2026-09-19-智能客服优化-已完成未完成计划报告.md`。

---

## 0. 系统背景（写给无仓库上下文的读者）

- 平台：电商 RAG + Multi-Agent 平台。后端 FastAPI + LangGraph（Python），网关 APISIX(:9080) 是唯一入口，剥 `/api` 前缀转发 :8000。数据库 PostgreSQL（业务库 + 元数据库，Docker 容器映射宿主 **5433**）。
- 主图：`POST /chat/stream` 同步执行，SSE 直返（帧序 meta → status/log/delta → done/error）。主图另有 planner/critique/supervisor 支线与旅游域图、选品漏斗域图——本文只覆盖**客服域**。
- 客服域 = 独立 LangGraph 域图（AI 客服）+ 坐席调度子系统（人工客服）+ 两端前端（用户端客服抽屉、坐席工作台）。
- 路径约定：下文 `backend/` 指仓库内 `backend/` 目录；`config/customer_service.py` 指 `backend/config/customer_service.py`。

### 规模口径

| 组成 | 规模 |
|---|---|
| 后端 `backend/customer_service/` | 101 个 Python 文件，约 16,640 行 |
| HTTP/WS 路由 | 6 个 `cs_*.py` 文件，约 30 个端点 |
| 域图 | 9 节点（loader / pending_handler / supervisor / reporter + 5 专家），`CS_GRAPH_RECURSION_LIMIT=20` |
| 坐席调度 | 独立容器 `cs-dispatcher`（compose 声明 2 副本），1 秒 tick 循环 |
| 前端 | 用户端 frontend(:3100) 客服抽屉 + 坐席工作台 frontend-cs(:3300) |
| 测试 | `backend/tests/customer_service/` 80 个文件 **932 条用例** |
| 评估集 | 320 条锁版（manifest v2.0，sha256 固定）+ cs-v2 六类 300 条 |

---

## 1. 消息全链路（AI 客服侧）

### 1.1 入口：双通路

**通路① 客服窗口锁域**：用户端客服抽屉（`frontend/src/hooks/useCSChat.ts:47`）每条消息带 `domain_hint="customer_service"`。主图 router 节点置 `cs_forced` 后**跳过域检测门与灰度**直接进 CS 管线——但跳不过三样（`orchestration/graph/cs_prefilter.py:32-34,160-169`）：
1. `CS_ENABLED` 总闸（关闭则整体降级回主路由）；
2. 显式转人工直通与人工接管期强制接管（见下）；
3. CS InputGuard 拦截（block/clarify 时直接回话术，不进图）。

**通路② 全局入口**（主聊天框，domain_hint 空）：`try_cs_prefilter`（cs_prefilter.py:17）按序执行——

1. **显式转人工直通**：正则命中「转人工/找真人…」（`detect_handoff_trigger`）→ 合成 conf=1.0 的 human_handoff 路由，不做任何推断（cs_prefilter.py:49-55）；
2. **人工接管期强制接管**：查 HandoffStore，会话处于 `waiting_human/human_active` 时用户的一切消息都转达人工，不允许被域检测漏判进主图（cs_prefilter.py:63-69，实测事故驱动的修复）；
3. **纯规则域检测**：每域关键词+正则至少命中 `CS_RULE_MIN_HITS=2` 条才算客服（2026-09-17 已删向量通道，现为纯正则 + 5min 进程内缓存）；
4. **灰度放量**：md5(session_id) 稳定哈希百分比 + 白名单（`CS_ROLLOUT_PERCENT` 默认 100=全量；用 md5 而非内置 hash 是因为 Python hash 有随机盐，重启会改变分组，cs_prefilter.py:261-274）；
5. **实体感知改写**（缺陷9 修复）：「查 MO-3C052B3A」无客服关键词也能提取订单号改写路由；「那它到哪了」继承上一轮唯一订单号（cs_prefilter.py:91-126，零 LLM）；
6. CS Router（域内 coarse→fine 意图路由）出 `CSRouteResult`。

**锁域非绝对**：无客服规则信号且命中旅游/选品强信号时走 `redirect_main` 转出主图；LLM 语义仲裁（`CS_REDIRECT_MAIN_LLM_ENABLED`）**默认 OFF**，靠正则兜着（`config/customer_service.py:314-337`）。守护测试：`tests/orchestration/graph/test_router_prefilter_order.py`。

### 1.2 域图内部（`customer_service/graph_builder.py:124-161`）

```
START → cs_state_loader → cs_pending_handler ─┬─ (无 pending) → cs_supervisor ⇄ 5专家 → cs_reporter → END
                                              └─ (有 pending) → 处理确认/取消/超时 → cs_reporter → END
（5 专家执行完一律回到 supervisor，由 supervisor 用 Command(goto) 决定下一个节点）
```

- **cs_state_loader**（graph_builder.py:40）：从 PG 加载会话快照（handoff/confirmation/pending 状态），并把「确认中动作的实体（订单号）+ 最近订单号」注册为上下文预算锚（request pin），保证后续裁剪不丢关键实体。
- **cs_pending_handler**（pending_handler.py:29）：有挂起确认单时**短路整个 supervisor→expert 链**。三类走向：`need_info` 型（等用户补订单号）转 action 专家补槽；有效 pending 委托 `confirmation_flow.process_confirmation`（唯一实现）；过期出超时回复。
- **cs_supervisor**（supervisor.py:95-241）三层决策，确定性规则优先：
  - **L1 硬规则**：①handoff 拦截——handoff_state ≠ ai_active 时强制派 handoff 专家（supervisor.py:122-136）；②循环上限——专家循环 ≥5 次（`CS_EXPERT_MAX_LOOPS`）强制 finish；③低置信降级——confidence < 0.6 且无专家历史时 finish，但知识类低风险请求放行检索兜底（supervisor.py:169-198）。
  - **L2 状态组合**：①确认 pending 时 finish 等用户回复；②同一专家连续执行 2 次强制 finish 防死循环。
  - **L3 LLM 兜底**：仅「低置信 + 已有专家历史」时调 LLM 选专家（800ms 线程级超时 `sync_call_with_timeout`，失败确定性回退 route_path 映射；supervisor.py:253-320）。注释明确记载：ChatOpenAI 的 `config={"timeout"}` 实测不生效，必须线程级限时。
  - 另有**人工接入超时回退**（supervisor.py:335-392）：`waiting_human` 超 120s（`CS_HANDOFF_TIMEOUT_SECONDS`）无坐席接 → 关闭转接、恢复 AI 服务、写审计。
- **cs_reporter**：汇总专家结果出 Markdown。知识域拒答时附带追问建议（`_clarify`，「拒答转追问」机制，orchestration/graph/cs_graph_node.py:66-102）。

**跨轮持久化**：跨轮状态权威在 **PG 业务表**（conversations / confirmations / handoffs），不靠 LangGraph checkpointer——`CS_CHECKPOINTER_ENABLED` 默认 false，PostgresSaver 代码处于待启用态（graph_builder.py:191-227）。thread_id 已按 `cs:{tenant}:{user}:{conv}` 命名空间化，防跨租户/跨用户串档（cs_graph_node.py:255-275）。

### 1.3 输出与审计

CS 子图结果经 `orchestration/graph/cs_graph_node.py`（适配器，仅做 state 转换）写回主图：`final_answer` / `cs_context` / `cs_action_result` / `cs_pending_action`（done 帧透传给前端确认卡）。审计条目与动作记录在此**同事务幂等落库**（cs_graph_node.py:143-176：动作经 `insert_action_idempotently`，重复持久化不产生第二条 agent_actions）。CS 图整体异常时降级返回固定兜底话术，不崩主图（cs_graph_node.py:51-53,239-252）。

### 1.4 五专家（选择事实源 = graph_state.py 三张映射表，P2.2 收口）

| 专家 | 职责 | LLM 调用 | 数据源 | 节点限时 |
|---|---|---|---|---|
| **knowledge** | 知识问答。**复用主平台 RAG 单例 pipeline**（非独立实现），按 cs_* 六库检索 + `subject_type=customer` 授权，匿名用户落 `cs-anon:{session}` 会话命名空间 | 间接 1~2 次（查询改写+生成，role=main 模型） | RAG | ✅ 60s |
| **query** | 只读业务查询：订单/物流/工单进度。复合问题先 LLM 分解（3s 超时，失败确定性降级单意图） | 0~1 次 | **http 模式查 Java business-service**（`CS_BUSINESS_GATEWAY_MODE=http`）；网关挂了报友好错误**绝不降级回 sandbox 假数据**（order_service.py:169） | ❌（内部自带） |
| **action** | 写操作：退款/退货/换货/改地址/改密码。缺订单号**持久化追问绝不猜 latest**；建 proposal 前过「业务实体唯一守卫」 | **0 次** | 写 confirmations 表；执行走幂等账本（见 §2） | ✅ 60s |
| **complaint** | 投诉检测（12 正则，规则 0 命中才 LLM 兜底评估严重度）→ 建工单 → 安抚话术（模板非 LLM）→ 自动升级转人工。防重入：同 turn 幂等 + 跨 turn 查活跃转接 | 0~1 次 | 工单落库 fire-and-forget | ❌ |
| **handoff** | 显式/自动转人工；对已排队/已接入状态幂等复用工单（防 supervisor 同 turn 重复派发） | 0 次 | handoff 状态机 + 工单落库 | ❌ |

所有专家经 `run_expert_safely`（experts/base.py:50）安全壳：异常转 `status=failed` 不穿透图；超时用 **per-call 独立 ThreadPoolExecutor(max_workers=1)** + `contextvars.copy_context()` 实现（超时孤儿线程无法强杀，只能丢弃结果）。**只有 knowledge/action 传了 60s 限时，query/complaint/handoff 未传**（见 §8 瓶颈）。

### 1.5 内部路由与理解层（易混点澄清）

- `customer_service/router/`（domain_detector → coarse_router → fine_router → cs_router 门面）是**客服域内部**意图路由：6 域粗分类 → 21 个 fine intent → `INTENT_PROFILES` 出 route_path/kb_ids/risk。confidence = (coarse+fine)/2。
- `orchestration/router/`（rule→vector→LLM 三层）是**主平台**能力路由。二者不复用代码；CS Router 是主三层 Router 之前的「域级短路前置」——命中则主三层完全不执行。
- `customer_service/understanding/`：纯规则零 LLM 的单轮统一理解（8 类实体正则、槽位表、情绪/紧迫/风险信号）。实体/槽位已接入 router_node 与专家；**情绪/风险/next_action 信号已产出但无决策消费者**（只进 Trace）。
- 规则词表规模：6 域 × (4~11 关键词 + 4~11 正则) + 12 条投诉正则 + fine 意图词表 + signals 词表，全部硬编码在 config/intents，无热更新。

---

## 2. 深读一：确认链竞态细节（写操作安全）

### 2.1 状态机与五段式链路

状态机（`confirmation.py`）：`PENDING → CONFIRMED → EXECUTING → SUCCESS/FAILED`，`PENDING → CANCELLED/EXPIRED`，外加结果未知态 `VERIFYING`。

退款这类动作的完整链路：

1. **资格检查**：查真实订单（http 模式查 Java 网关）；退款查重 **fail-closed**——查重查询失败直接拒绝申请，防止双退款（refund_service.py:175-185）；
2. **Proposal 确认卡**：写 `confirmations` 表（TTL 900s、追问 retry ≤ 3，config:17-18）。前端 done 帧渲染 CSConfirmCard，用户点确认/取消，或文字命中关键词表（「确认/好的/ok…」vs「取消/算了…」，config:23-30）；
3. **原子认领**：`claim_for_execution`（见 2.2）；
4. **幂等执行**：`run_idempotent_side_effect` PG durable ledger，key=(tenant, user, `cs.action.execute`, `cs_action:{confirmation_id}`)——多实例并发确认、expert 线程超时重入、未来 Phase 6 执行器的 retry/recovery/admin retry，**最多真实生效一次**并复用同一 action_record（confirmation_flow.py:114-154）；
5. **结果未知兜底**：执行结果未知（`SideEffectOutcomeUnknown`）→ 确认行落 `VERIFYING`，**继续占住 051 唯一索引的 active 生命周期**（active 谓词含 verifying），人工裁决前禁止同语义操作再次创建；**绝不落 failed**——那会释放守卫、打开「UNKNOWN→FAILED→释放→二次执行」绕过通道（confirmation_flow.py:239-271，Phase3 STOP D/E 冻结语义）。

⚠️ **关键现状**：第 4 步的执行体是 `simulate_execute`——只构造 `status="simulated"` 的记录，**不写任何业务表**。用户成功文案明确标注「当前为模拟模式，实际写操作将在 Phase 6 启用」（confirmation_flow.py:226）。即：**安全框架全部建成且经实机并发验收，真实执行器未接**。

### 2.2 双层存储与原子认领（`confirmation_store.py`）

存储结构：**进程内 dict 作 L1 缓存 + PG 作唯一事实源**，key=(user_id, session_id)。

**写序不变量**：只有 DB 持久化成功后才更新 L1（save 的实现，confirmation_store.py:223-230）；DB 写失败时严格模式（生产 `CS_STORE_STRICT_WRITES=true`）抛 `StoreWriteError`，**禁止静默降级成内存态**——注释记载此前 cache-only 降级导致内存与 DB 永久分叉（audit-report §P0-5 事故）。

**原子认领 `claim_for_execution`（confirmation_store.py:242-289）三分支**：

```
1) DB 认领成功（单条条件 UPDATE pending→confirmed，并发只有一方影响行数>0）
   → 移除 L1 条目，返回 confirmation_id          ← 多实例安全的正路
2) DB 无行（可能是历史 save 降级未落库）
   → 回退进程内 L1 认领（L1 pop 原子 + tombstone 集合 _claimed_l1 防恢复后重复认领）
3) DB 异常：严格模式抛 StoreWriteError（执行必须失败，不冒双执行之险）；
   非严格模式（仅测试）降级 L1 认领 + 告警
```

tombstone 细节：DB 认领异常时 L1 只能先消费 pending；若 DB 随后恢复而原行仍为 pending，下一次确认不能把同一动作再认领出来——`_claimed_l1` 集合只覆盖当前进程的降级窗口，新动作 save/cache 时清除（confirmation_store.py:192-195）。

### 2.3 业务实体唯一守卫（Phase3 STOP D，跨 confirmation 防重）

建 proposal / 更新 proposal 时（`_async_save`，confirmation_store.py:367-459）：

- **身份计算**：`compute_identity(pending_action, tenant_id)` 产出 (action_type, target_type, target_id, semantic_fingerprint)；正式业务写缺身份 → fail-closed（严格模式抛错）。
- **并发决胜在 DB，不在应用层**：migration **051 partial unique index**——active 生命周期谓词 `('pending','confirmed','executing','verifying')` 上按身份唯一。应用层预检（`_raise_on_active_conflict`）只负责把 DB 拒绝转译为可读业务语义；**禁止「先查后插」**（注释 §17：并发由 DB 唯一索引保证原子）。
- **INSERT 撞索引**：rollback 后重读赢家行，抛 `BusinessOperationAlreadyActive`（业务冲突，非 500）（confirmation_store.py:143-165）。
- **UPDATE 撞索引**（need_info 升级为正式 proposal、reask 更新计数时身份原子刷新撞上另一 active 操作）：`begin_nested` savepoint 内只回滚内层，转译 `BusinessOperationConflict`（D19/D20）。
- **IN_DOUBT 防线**（`_raise_on_in_doubt_ledger`）：legacy failed 行若在 Phase2 durable ledger 留有 UNCERTAIN/running 记录，守卫必须继续占住，拒绝重复发起（confirmation_store.py:89-117）。
- **FK 生命周期修复**（缺陷6.5）：confirmations.conversation_id 是 NOT NULL FK，而 conversation 行历史上在 turn 结束才懒创建——流中写 confirmation 先于 conversation insert 会 FK violation。现改为：写 confirmation 前同事务幂等 get_or_create 会话行 + flush；并发双提交在 conversations 唯一键上竞争时 savepoint 内重试、复用对方已提交的行（confirmation_store.py:402-420）。

### 2.4 竞态矩阵总结（实测验收数据）

| 竞态场景 | 防线 | 实测 |
|---|---|---|
| 同一用户并发重复点确认 | DB 条件 UPDATE 单方获胜（claim），败方返回 duplicate 话术 | 官方验收：20 并发确认 1 胜 19 败 |
| 20 个坐席并发认领同一工单 | offers 版本 CAS（见 §3.5） | 1 成功 19 个 409 |
| 多实例部署重复执行 | durable ledger key 含 confirmation_id，最多生效一次 | Phase2 Step6 冻结 |
| 同语义动作重复发起（新 confirmation） | 051 partial unique index 决胜 + 预检转译 | STOP D 十连接并发决胜测试 |
| 执行结果未知 | VERIFYING 占住索引 + 人工裁决 CLI（reconciliation） | STOP E 双环境 18 项测试全过 |
| DB 断网后 L1 伪成功 | 严格模式抛 StoreWriteError；L1 认领走 tombstone | 71e14c1 修复 |
| 追问无限循环 | retry ≤ 3（`CS_MAX_CONFIRMATION_RETRIES`），超限按过期处理 | confirmation_flow.py:330-343 |
| 确认过期 vs 重复确认口径 | clear(final_state=) 区分 success/failed/expired/cancelled（此前一律写 cancelled 导致审计失真，P1 修正） | confirmation_store.py:232-240 |

---

## 3. 深读二：Dispatcher 事务顺序（坐席自动派单）

### 3.1 总体结构

独立容器 `backend/workers/cs_dispatcher.py`（compose 服务 `cs-dispatcher`，2 副本），1 秒 tick，三段顺序刻意设计：

```
1. reap_stage   回收过期 offer / 关闭超期工单（P7，CS_REAPER_ENABLED）
2. run_once     派单（P6，CS_DISPATCH_MODE 三态：off/shadow/enforce；当前 .env=enforce）
3. relay_stage  投递 pending 事件（P8，CS_OUTBOX_RELAY_ENABLED）
```

「先把过期 offer 放回队列，同一 tick 就能重新派出去」（官方标准：过期后 2 秒内释放并重派；tick=1s 时回收→重派同秒完成）。`CS_DISPATCH_MODE=off` **只关派单段**——回收和投递是恢复路径，被门控关掉会把用户永久卡死在 agent_offered、让已提交通知永远到不了坐席。

进程内不持有任何派单权威：绑定/容量/幂等全靠 **PG 事务 + 部分唯一索引**，多副本之间不通信、不抢 Redis 锁。异常只记日志循环不退出；每轮写 30s TTL 心跳供「dispatcher 心跳中断」告警。

### 3.2 全局加锁顺序（死锁防线）

**约定：`conversations` → `handoffs` → `cs_agents`，禁止反向获取**（repository.py 模块 docstring）。

理由：P4 用户入池事务先锁会话行、再锁活动工单行；派单事务若反过来先锁工单再更新会话投影，两条路径会在同一会话上互相等待形成死锁。因此派单先用一次**无锁预读**（`next_waiting_handoff_stmt`：`priority DESC, created_at ASC, id ASC` 严格排序）确定候选会话——预读不构成派单依据，真正的绑定权归 `lock_dispatchable_handoff` 的 `FOR UPDATE SKIP LOCKED`。

**全部锁用 SKIP LOCKED**：竞争者立即让开返回 `contended`，由 worker 下一轮重试，而不是排队等待（避免队列头被占时同一租户被反复阻塞）。所有查询强制携带 tenant_id（多租户漏条件防线，不允许调用方自行拼接）。

reaper 同样遵守此序：候选无锁读出 → 逐行按 conversations → handoffs 取锁 → 锁内复核状态，拿不到锁就跳过等下一轮（reaper.py docstring：「多副本安全，不使用任何 Redis 协调」）。

### 3.3 P4 入池（`dispatch/service.py:179-218` `create_or_reuse_handoff`）

单 PG 事务（`session.begin()`）内：锁会话行 → 校验归属（user/tenant 不符抛 403 语义）→ 查活动工单（有则**幂等复用**）→ 校验状态（resolved 会话/已人工接管拒绝）→ 插 `waiting_human` 工单（priority=50，`total_deadline_at = now + 120s`）→ 同事务更新会话投影（handling_mode=waiting_human）。IntegrityError（活动唯一索引第二道防线被并发命中）→ rollback 后重读赢家；**重读不到赢家就报数据库失败，绝不把未提交结果当成功**。

### 3.4 P6 派单（`dispatch_once`，service.py:241-476）——单事务八步

全部写操作在一个 `session.begin()` 内：

1. 无锁预读队列头可派工单（waiting_human 且未超 total_deadline）→ 无则 `no_handoff`；
2. 按序锁 conversations（拿不到 → `contended`）→ 锁 handoffs（复核仍可派，拿不到 → `contended`）；
3. **灰度门控**（P9，enforce 下）：生效值走 sys_config（**DB 覆盖 env，免重启放量**），percent<100 时按 sha1(tenant:conversation_id) 分桶，桶外返回 `rollout_skipped`；shadow 模式全量计算不受影响；
4. 在线坐席筛选：`list_accepting_agent_ids`（DB）∩ presence 心跳（**Redis MGET 单次往返，45s TTL；Redis 不可用返回 None → `presence_unavailable`，本轮 fail-closed 不派单**——绝不允许把「查不到」当成功语义，presence.py docstring）；
5. 自动置忙过滤（agent_busy，**fail-open**：Redis 不可用返回空集不影响在线判定）；
6. 选坐席 `lock_least_loaded_agent`：在线集合内最少负载轮询（active assignment 计数 + `last_assigned_at` 排序，`FOR UPDATE of CSAgent SKIP LOCKED`）+ 技能匹配（cs_agents.skill == handoffs.required_skill，默认 general）+ **本单冷却排除**：本单拒过/超时过的坐席永久排除（2026-09-21 治理：原 60s 冷却改为按工单排除；released 除外——主管重派是人工决策允许指定回同一坐席）→ 无则 `no_candidate`；
7. 写状态：handoff → `agent_offered`（assignment_version+1、offer_expires_at=now+30s）、`agent.last_assigned_at=now`（轮询公平性）、插入 CSAssignment(state=offered)、会话投影 assigned_agent_id 更新、**事件同事务写 outbox**（`conversation.offered`）；
8. 事务提交后才做一次快速广播（`event_relay.publish_persisted_event`）；**广播异常不回滚绑定**——真正的投递保证由 P8 relay 按 outbox pending 行重放承担（service.py:466-475）。排队等待时长指标在提交后打点（含此前超时重派的全部轮次）。

### 3.5 P7 reaper 与 P5 接单（时间驱动的状态边）

reaper 两条边：`agent_offered ──30s 超时──▶ waiting_human`（attempt 预算未用尽，回队列）；`attempt ≥ 3 次或超 120s 总期限 ──▶ closed`（关单、会话恢复 AI、坐席归属清空、广播 `handoff_closed` 事件；池空超期给「留言兜底」文案而非冷冰冰的超时）。超时回收与主动拒单同语义：对当事坐席记 `record_agent_reject`（fail-open，Redis 故障不影响回收事务）。

坐席接单 `accept_offer`（offers.py:192-263）：锁对（conversation+handoff 同序）→ owner 断言 → **offer_version CAS**（与工单当前版本不一致 = 重派后点了旧接单按钮，返回 `OfferStale` 409——**不能当幂等成功**）→ 活动 assignment 复核（已被 reaper 解除但工单状态未刷新时以 assignment 为准拒绝，避免「接单成功但无人负责」）→ handoff → `human_active`、assignment → accepted、会话 handling_mode=human、同事务写 `claimed` 事件。重复点接单：状态已流转 → 同样 409（文案区分「已由本坐席接单」）。

### 3.6 P8 事件投递（outbox）

状态变更与事件**同事务**落 `cs_events`（outbox）；提交后快速广播一次，失败不影响绑定；relay 段每 tick 重投 pending 行（按 `after_seq` 游标序），投递成功才标记——**事件不丢、不乱序、幂等（event_id 唯一）**。官方验收：连续 20 次 ACK 前 worker 中断后恢复/幂等回归通过；补偿器在不支持 XAUTOCLAIM 的 Redis 上可退回 XPENDING+XCLAIM。

### 3.7 已知异常矩阵

| 场景 | 行为 |
|---|---|
| Redis 挂（presence 查询失败） | 本轮 fail-closed 不派单（`presence_unavailable`），绝不产生无坐席绑定 |
| Redis 挂（置忙查询失败） | fail-open 继续派（最坏派给刚置忙坐席，下轮 reaper 可回收） |
| 队列头被其他副本锁住 | SKIP LOCKED 立即返回 contended，下一 tick 重试 |
| 广播失败 | 绑定已提交不受影响，relay 下轮重投 |
| 坐席全满/全离线 | `no_candidate`，工单留队列；池空超 120s 由 reaper 关单恢复 AI 并通知用户 |

---

## 4. 深读三：评测集怎么跑

### 4.1 数据集

- **权威文件**：`backend/evaluation/datasets/cs/cases.jsonl`（V3 canonical，**320 条锁版**）：20 条 v1（CS-001~020 旧 schema 平移）+ 300 条 cs-v2（六类分布 faq 60 / query 60 / action 60 / complaint 40 / multi_turn 40 / safety 40）。
- **锁版机制**：`manifest.json` 记 version=2.0、case_count=320、**sha256 固定**；变更必须升版本号并重算 sha256（P0 评审拍板 2026-09-19）。
- **用例格式**（JSONL）：`{"id","question","module":"cs","expected":{...},"metadata":{...}}`；v2 扩展字段含 `expected.intent / cs_route(hit|miss_non_cs|clarify_weak) / next_action(answer|clarify|refuse|handoff|propose|propose_with_gap) / risk_level / should_handoff`。
- **校验器**：`evaluation/datasets/cs/v2/validate_v2.py`（分布与 schema 校验）+ `evaluation/dataset/validator.py`。
- 数据加载约定：`evaluation/dataset/loader.py` 按 `{module}/cases.jsonl` 解析 canonical，Suite 文件只存 case_ids 引用；已弃用评测集文件名会被 loader 显式拒绝。

### 4.2 Runner 三模式（`evaluation/runners/cs.py`，注册名 `"cs"`）

| 模式 | 行为 |
|---|---|
| **offline sanity**（默认，无 LLM） | 只做数据集结构校验：id 唯一、target/intent/cs_route/next_action/risk_level 合法（合法性集合直接取自 graph_state 与 intents 单一事实源），不调图。**CI 无 Key 可跑** |
| **live**（`--live`） | 逐条 invoke CS Graph：thread_id 按 case 隔离（`eval-cs-{case.id}`）防状态串扰；判定两维——**路由正确性**（expert_history 访问节点与 expected.target 映射的 expert 对照）+ **应答有效性**（final_answer 非空 + must_contain 命中） |
| **full_path**（live + mode=full_path） | 走完整链路含 cs_prefilter（评测 cs_route 三态与 next_action）——P0 报告称「场景决策矩阵固化前先 quant 化」依赖此模式 |

### 4.3 运行入口（现状如实描述）

- **常驻 CI 门禁**：`backend/tests/evaluation/test_cs_runner_v2.py`（offline sanity + 锁版 320 条结构校验），随 pytest 全量跑。
- **CLI**：`python -m backend.evaluation [module] [options]`（`evaluation/cli.py`）。通用参数：`--live / --judge / --smoke / --tier smoke|core|hard|regression / --run-id（断点续跑）/ --regression / --promote-baseline / --workers`。
- ⚠️ **已知缺口（代码事实）**：CLI 位置参数 `module` 的 argparse choices 列表**尚未包含 `"cs"`**（cli.py:71-77 只有 all/planner/rag/sql/e2e/travel* 四个），而 runner 注册表里 `"cs"` 已注册（runners/cs.py:160 `register_runner("cs", _run_cs, needs_live=False)`）。即：**通过 CLI 跑 cs 模块会 argparse 报错**；现行稳定入口是 pytest 门禁（offline）与经 `run_all(module="cs")`/EvaluationService 的编程式调用（live）。这是 choices 列表滞后于 runner 注册的一个小失配，建议作为清理项。
- 产物落盘：`data/eval_runs/{run_id}/`（report.json + per_case 明细 + markdown），`--compare latest` 可与历史跑分对比。

### 4.4 历史基线（官方报告口径，供对照）

- P0 验收：评测集 20→300 校验器 PASS 300/300；意图映射 20/20；F-\* 故障注入 25/25 PASS；五剧本×10 经 APISIX 全链路基线（A/E/D 可用，B/C 缺陷已修复）。
- 质量门禁：客服域 pytest 932 条用例；评测集 CI 门禁常驻。
- ⚠️ 注意：**live 全量评测需要 LLM key 与 PG**；当前栈模型凭据以 DB 治理为唯一来源（.env 无模型行），离线 sanity 不受影响。

---

## 5. 已完成（有提交/验收证据）

1. **图结构与路由**：双入口域锁、预过滤顺序守护测试、显式转人工直通、人工接管期强制接管、md5 稳定灰度、实体感知改写（缺陷9）、拒答转追问（L2 兜底）。
2. **五专家全链 + supervisor 三层决策 + 防死循环**（重复检测/循环上限/低置信分级处理）。
3. **CSUnderstanding 统一理解层**：实体/槽位/信号，200 实体样本 ≥0.95、形近错别字 0 改写，纯规则零 LLM；已接 router_node。
4. **安全**：CSInputGuard 运行时门禁（**100 条攻击样本拦截率 100%**：38 注入+20 SQL+11 有害+18 越权+13 敏感，转人工/确认文本 0 误拦截）；租户/用户身份贯通进 RAG 命名空间；审计幂等落库。
5. **幂等与可靠性**（Phase2/3 系列 STOP 全冻结）：确认原子认领（20 并发 1 胜 19 败）、副作用 durable ledger、IN_DOUBT→VERIFYING→人工裁决 CLI、业务实体唯一守卫（051 partial unique）、DB 断网后 L1 认领隔离。
6. **坐席调度 P4-P9 全链**：入池幂等、单事务派单、reaper、outbox 投递、offers CAS、**WS 过 APISIX 100 次连接循环验收**、after_seq 回放契约。
7. **质检运维**：统一工单落库（批次C）、beat 每日 QA 报表 + 告警阈值（fallback 率/路由一致率/转人工率，批次D）、dispatch stats、维护扫描 beat 每 60s（确认过期/转人工超时/outbox 补偿三任务）。
8. **评测**：320 条锁版 + CI 门禁 + F-* 故障注入 25/25 + P0 五剧本×10 全链路验收 22/22。
9. **测试**：客服域 932 条用例。

## 6. 未完成（官方报告 + 代码证据核实）

**功能级：**
1. **真实执行器（Phase 6）整体未接**——退款/退货/换货/改地址/改密码全是模拟执行；工单只落库，处理流转是假的（confirmation_flow.py:126 明示 Phase 6 接入时 fail-closed 语义才成硬门禁）。
2. **业务网关契约缺口**：http 模式下订单**明细**端点缺失（order_service.py:145）；退款查重在 http 模式被跳过（网关无该端点）——**这两点是接真实执行器的前置阻塞**。
3. **understanding 半接线**：sentiment/urgency/risk/next_action 已产出无决策消费者；物流真实 API 只留协议位（现役 mock 轨迹 DEMO-1001~1003 硬编码）。
4. **官方 P2/P4/P5 工作包未开始**：场景决策矩阵固化（FAQ 确定性执行）、CSCaseSnapshot 版本化结构化记忆、灰度四阶段验收。

**可靠性验收缺口（P3 尾巴）：**
5. 真实 PG 停止/恢复故障演练；真实 Redis/Worker 强杀 20 次；**SSE 断线恢复协议**——`/chat/stream` SSE 帧无 id/游标/回放入口，前端断流只能进错误态（WS 侧已闭环，SSE 没做）；Redis+PG 双故障时事件补偿无法凭空生成，需双故障演练+告警闭环。

## 7. 瓶颈（性能向）

1. **每轮 LLM 调用可达 3~5 次**且全走同一 role=main 模型无小模型分流：Supervisor L3（低置信时）+ Query 复合分解（3s）+ Complaint 严重度（3s）+ RAG 改写+生成（实测 1~30s）；坐席辅助另算（RAG+LLM 10-50s，15s 超时）。
2. **complaint 的 LLM 兜底用 `config={"timeout"}`**（complaint_service.py:127）——仓库三处注释明确记载该参数**不生效**，而 complaint/handoff/query 三节点又没传 `run_expert_safely` 限时 → **投诉路径存在无界挂起窗口**（supervisor/query 都改用了线程级限时，唯独这里漏了）。
3. **每专家 per-call 新建 ThreadPoolExecutor**（有意为之隔离超时孤儿），高频下有创建销毁开销；孤儿线程仍会跑完 LLM/DB 调用只是结果被弃。
4. **检测缓存收益退化**：删向量通道后 detect_cached 省的只是一次正则重跑（~21µs，代码注释自述），缓存层成惯性代码。
5. **审计落库在请求线程内同步执行**（cs_graph_node → run_sync），有审计条目的轮次加一次 PG 往返。

## 8. 问题（质量/设计向）

1. **规则词表 5+ 处重复且口径不一**（域词表/fine 意图词表/投诉词表/query 复合预判词/signals/handoff 触发词）——config 自述须「成对维护防漂移」纯靠纪律。
2. **置信度语义靠补丁贴平**：coarse 计数/3、fine 计数/2 取平均，又给 fine 命中设 0.6 下限、default 设 0.6（均因「平均后打穿 supervisor 0.6 闸门」）——阈值互相耦合，事故驱动调参。
3. **指标失真**：`record_cs_rag_status` 的 miss 分支不可达（service 层永远返回非空 answer），fallback 率告警（2%）实际测不到该指标。
4. **吞异常面偏宽**：complaint 幂等检查失败仅 warning 继续建单（可能重复建单）、工单落库 fire-and-forget、`_get_latest_order_id` 裸 except: pass。
5. **硬编码尾巴**：追问示例订单号 "MO-1002" 与演示数据 "DEMO-*" 前缀不一致；`RETURN_WINDOW_DAYS` 定义了但资格检查从未使用（7 天窗口只出现在文案）。
6. **知识置信度运行在兜底值上**：META 注释遵循度不稳时 can_answer=True 兜底 0.65 直接放行 CAUTIOUS——0.85/0.60 两道门禁对这部分流量形同虚设。
7. **CS Router 缓存键=裸 query** 不区分租户，metadata 靠调用方事后追加，契约隐式。
8. **评测 CLI choices 滞后**：`"cs"` 已注册 runner 但 argparse 位置参数未列入（§4.3）。

## 9. 配置开关现状（根 .env 实测值 + 默认值）

| 开关 | 默认 | 实测 | 作用 |
|---|---|---|---|
| `CS_ENABLED` | false | **true** | 客服域总闸 |
| `CS_GRAPH_ENABLED` | true | — | 域图启用（关则降级） |
| `CS_ROLLOUT_PERCENT` | 100 | — | 灰度百分比（md5 稳定哈希） |
| `CS_DISPATCH_MODE` | — | **enforce** | 派单三态 off/shadow/enforce |
| `CS_BUSINESS_GATEWAY_MODE` | sandbox | **http** | 业务查询走 Java 网关（不降级假数据） |
| `CS_DEMO_MODE` | false | 未设 | 演示身份映射 99001 |
| `CS_STORE_STRICT_WRITES` | 生产 true | — | DB 写失败禁止降级内存态 |
| `CS_CHECKPOINTER_ENABLED` | false | — | LangGraph checkpoint（跨轮权威在业务表） |
| `CS_EXPERT_MAX_LOOPS` | 5 | — | 专家循环上限 |
| `CS_HANDOFF_TIMEOUT_SECONDS` | 120 | 120 | 人工接入总等待期 |
| `CS_CONFIRMATION_TTL_SECONDS` | 900 | — | 确认单有效期 |
| `CS_MAX_CONFIRMATION_RETRIES` | 3 | — | 确认追问上限 |
| `CS_SUPERVISOR_LLM_ENABLED` | true | — | L3 LLM 决策（800ms 超时） |
| `CS_REDIRECT_MAIN_LLM_ENABLED` | **false** | — | 锁域转出 LLM 仲裁（默认 OFF） |
| `CS_AGENT_ASSIST_ENABLED` | true | — | 坐席 AI 建议（并发 4/超时 15s） |
| `CS_EXPERT_TIMEOUT_S` | 60 | — | 仅 knowledge/action 两节点实际传入 |

## 10. 一句话总结

**骨架和安全已超额建成**——双入口路由、五专家调度、确认幂等（多道 DB 级防线+实机并发验收）、坐席派单（全局锁序+outbox 不丢事件）、实时通道、932 条测试；**手脚还是假的**——写操作模拟执行、业务明细端点缺失、物流轨迹 mock，规划稿 P2/P4/P5 三个工作包未动。最值得先动手的三件事：① 修 complaint 无界挂起（几行改动）；② 补网关明细/查重端点打通 Phase 6 真实执行器（安全框架已就绪只差执行体）；③ understanding 信号接进 supervisor（产出已有、零成本消费）。

---

## 附：关键文件索引

| 主题 | 文件 |
|---|---|
| 入口预过滤 | `backend/orchestration/graph/cs_prefilter.py`；守护测试 `tests/orchestration/graph/test_router_prefilter_order.py` |
| 域图适配器 | `backend/orchestration/graph/cs_graph_node.py` |
| 图构建/拓扑 | `backend/customer_service/graph_builder.py` |
| Supervisor | `backend/customer_service/supervisor.py` |
| Pending 处理 | `backend/customer_service/pending_handler.py` |
| 确认流程/存储 | `backend/customer_service/confirmation_flow.py`、`confirmation_store.py`、`confirmation.py`（状态机） |
| 五专家 | `backend/customer_service/experts/{base,knowledge,query,action,complaint,handoff}.py` |
| 内部路由/理解 | `backend/customer_service/router/*`、`understanding/*` |
| 派单 | `backend/customer_service/dispatch/{service,repository,reaper,presence,offers,outbox,event_relay,agent_busy}.py`；worker `backend/workers/cs_dispatcher.py` |
| 实时通道 | `backend/customer_service/realtime.py`（AgentHub）、`backend/app/api/routes/cs_agent_ws.py` |
| 配置 | `backend/config/customer_service.py`（约 30 个开关） |
| 评测 | `backend/evaluation/datasets/cs/{cases.jsonl,manifest.json,v2/}`、`backend/evaluation/runners/cs.py`、`backend/evaluation/cli.py` |
| 官方规划/验收 | `docs/archive/2026-09/2026-09-19-智能客服优化-已完成未完成计划报告.md`（及同日规划稿/P0 基线三件套） |

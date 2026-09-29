# 企业级 AI 客服域设计方案（Customer Service Domain Design）

> 版本：v1.1（2026-09-29 架构评审修订：新增用户体验层/Context Manager/统一案件 cs_case/客服运营闭环；Supervisor 定性强化；迁移路线改为三阶段） ｜ 日期：2026-09-29 ｜ 状态：设计稿（待评审）
> 定位：假设客服域从零开发，按企业生产系统标准设计目标架构；文末附「从现有项目迁移到该架构」章节。
> 技术栈基线：FastAPI + LangGraph ｜ PostgreSQL + Redis ｜ RAG（向量+BM25 混合检索）｜ APISIX 网关 ｜ Java 业务系统（订单/物流/售后）｜ 用户端 + 管理端 + 坐席工作台三前端

---

# 一、客服域定位

> **定位陈述**：客服域不是一个聊天机器人，而是一个面向电商售后场景的**人机协同服务系统**——通过确定性流程处理订单查询、售后操作和风险场景，通过 RAG 提供知识服务，通过 Human-in-the-loop 机制完成复杂问题升级，并通过质量闭环持续优化。

## 1.1 解决什么业务问题

电商客服诉求有三个特征：**高度重复**（70%+ 是订单查询、政策咨询等可枚举问题）、**含资金风险**（退款/退货是真实写操作，错一次就是资损）、**含情绪风险**（投诉处理不当会升级为舆情/监管事件）。因此客服域要解决的不是一个"聊天机器人"问题，而是一个**人机协同的风险受控服务系统**：

| 业务问题 | 客服域的答案 |
|---|---|
| 重复诉求消耗人力 | AI 自动解决可枚举诉求（知识问答 + 业务查询），目标 AI 解决率 60%+ |
| 写操作有资损风险 | 有副作用的操作走"资格检查 → Proposal → 用户确认 → 幂等执行 → 审计"确认链，AI 永不直接动资金 |
| 复杂/情绪问题 AI 兜不住 | 人机协同：AI 识别不了或高风险时转人工，转接过程不丢上下文，AI 在人工会话里只做辅助不抢答 |
| 服务质量不可度量 | 全链路 trace + 业务指标（AI 解决率/转人工率/CSAT）+ 评估集门禁 |

## 1.2 服务哪些用户

| 用户 | 使用什么 | 核心诉求 |
|---|---|---|
| C 端买家 | 用户端页面 / 客服抽屉 | 快速得到准确答案、安全完成退款等操作、随时能找到真人 |
| 人工坐席 | 坐席工作台 | 自动派单公平、接管时上下文完整、AI 辅助建议（不是替代） |
| 客服主管 | 管理端 | 队列监控、工单流转、质检报表、强制重派 |
| 平台运营/管理员 | 管理端 | 知识库运营、开关灰度、高危操作审批、看板 |

## 1.3 与主 Agent、其他业务域如何隔离

五层隔离，每层都是硬边界：

1. **流量隔离**：主图入口有一个廉价的 Intent Gateway（域分类前置）。客服域命中即短路，主图规划/检索链路完全不执行；反之主图流量不进客服域。客服窗口（用户显式进入）带 `domain_hint` 锁域，跳过判域直进，但**跳不过安全护栏与总闸**。
2. **编排隔离**：独立 LangGraph 子图，独立 state schema、独立 recursion_limit、独立 checkpointer 命名空间（`cs:{tenant}:{user}:{conv}`）。客服域图异常只降级自身话术，绝不崩主链路。
3. **数据隔离**：独立 schema（`cs_*`），客服会话/消息/工单不混入主平台对话记忆；用户端不二次写消息（后端统一写），避免客服条目污染主对话侧栏。
4. **依赖隔离**：客服域访问业务数据**只有一条路**——Tool → Business Gateway → Java 服务。禁止客服域直连业务库、禁止 Tool 之间互调。
5. **发布隔离**：独立总闸（`CS_ENABLED`）、独立灰度（按会话稳定哈希放量，保证同一会话体验一致）、独立降级路径（关闸 = 回主路由，关派单 ≠ 关回收）。

## 1.4 为什么需要独立 Domain Graph

- **意图空间封闭**：客服意图可枚举（约 20 个 fine intent），适合"意图路由 → 专家执行"的确定性编排；而主图是开放任务，需要规划式编排。编排范式根本不同，硬塞进一个图会让两边都复杂。
- **SLO 与成本模型不同**：客服要求秒级首响 + 低成本（规则优先、LLM 只在低置信兜底）；主图可以接受数十秒换质量。分开才能分别调优。
- **安全模型不同**：写操作确认链（幂等 + 认领 + 审计 + UNKNOWN 裁决）是客服域特有的重型机制，不该污染通用 Agent 的执行路径。
- **跨轮人机协同状态机**：AI↔人工切换是长周期状态（可能隔天恢复），需要独立的会话状态语义与持久化，主图的"单任务执行"模型装不下。
- **故障半径**：客服域单独灰度、单独回滚、单独降级。

## 1.5 用户请求完整链路

```
用户（用户端页面 / 客服抽屉）
  │  HTTPS + SSE（流式）
  ▼
┌─────────────────────────────────────────────────────────┐
│ APISIX Gateway                                          │
│  · JWT 验签（Bearer 优先）→ 注入 X-User-Id / X-Tenant-Id │
│  · 限流 / 熔断 / WebSocket 升级（坐席通道）              │
└─────────────────────────────────────────────────────────┘
  │  POST /chat/stream
  ▼
┌─────────────────────────────────────────────────────────┐
│ Intent Gateway（主图入口域路由，廉价前置）               │
│  规则命中 → CS / 旅游 / 选品 … 短路分发                 │
│  未命中 → 主 Agent 三层路由（rule → vector → LLM）       │
└─────────────────────────────────────────────────────────┘
  │ route_mode = customer_service（携带意图路由结果 cs_route）
  ▼
┌─────────────────────────────────────────────────────────┐
│ CS Domain Graph（LangGraph）                            │
│                                                         │
│  state_loader ──► pending_handler ──► Supervisor ◄─┐    │
│  （加载会话快照）（拦截待确认动作）      │          │    │
│                                    ┌───┴───┐      │    │
│                                    ▼       ▼      │    │
│                              Knowledge Query      │    │
│                              Action  Complaint    │    │
│                                   Handoff ───────┘    │
│                                        │              │
│                                        ▼              │
│                                    Reporter ──► END   │
└─────────────────────────────────────────────────────────┘
  （域图内部顺序：state_loader → context_manager → pending_handler
    → Supervisor ⇄ Experts → Reporter；Context Manager 见 §4.1）
  │ Tool 调用（统一契约 JSON Schema）
  ▼
┌─────────────────────────────────────────────────────────┐
│ Tool Layer（查询 / 知识 / 操作 / 人工 四类）             │
│  超时 · 重试 · 幂等键 · 审批门 · 审计                    │
└─────────────────────────────────────────────────────────┘
  │                                      │
  ▼ RAG 内部链路                          ▼
┌──────────────┐              ┌──────────────────────────┐
│ RAG Pipeline │              │ Business Gateway         │
│ 向量+BM25    │              │ 服务间认证 · 归属校验     │
│ Rerank       │              │ 幂等 · 审计 · 限额       │
│ 置信分级     │              └──────────────────────────┘
└──────────────┘                        │ REST / Kafka
                                        ▼
                              ┌──────────────────┐
                              │ Java 业务服务     │
                              │ → 业务数据库      │
                              └──────────────────┘
  ▲
  │ Reporter 产出 Markdown + SSE 帧（meta → delta → done）
  ▼
用户
  │（可选分支）
  ▼
人工客服系统：Handoff → Dispatch Worker → 坐席工作台（WS）
```

---

# 二、用户体验与产品设计

> 本章承载四层视角的最上层。设计顺序**自上而下**——先定义用户看到什么、业务流程走到哪，再落 Agent 与基础设施，不要反过来。

## 2.1 四层视角

```
用户体验层    （本章：客服入口 / 可信状态 / 快捷操作卡 / 体验红线）
      ↓ 约束
业务流程层    （§五 业务流程 · §九 人工介入策略与运营闭环）
      ↓ 承载
Agent 执行层  （§四 Agent 设计 · §六 Tool · §七 状态管理）
      ↓ 依赖
基础设施层    （§三 整体架构 · §八 异常可靠性 · §十 数据库 · §十一 安全 · §十二 监控）
```

判断标准（优先于任何技术选型）：用户什么时候感觉 AI 有用？用户什么时候不会被 AI 气死？人工接管时是否需要重新解释？客服主管是否能管理？

## 2.2 首屏客服入口（不直接进聊天）

联系客服首屏不直接打开对话框，而是固定路径入口：

```
联系客服
  猜你想问：
  [查询订单]   [物流进度]   [退款售后]
  [优惠活动]   [投诉建议]   [人工客服]
```

理由：客服 80% 的诉求是固定路径。快捷入口把意图识别前置为"用户自选"，同时减少四样东西——**LLM 调用成本、用户输入成本、意图分类错误率、首响延迟**。

- 点击入口 = 携带预填意图参数（intent + 参数模板）进入域图，路由直接命中，跳过冷启动分类；
- 「人工客服」入口直达 Handoff（显式直通语义，§4.7）；
- 入口清单由运营配置（§9.5），按未解决问题池与高频意图持续调整。

## 2.3 AI 回答的可信状态（来源标注到 UI）

每条 AI 回答必须能回答用户心里的两个问题：**这个信息哪来的？多新？**

```
┌ 业务查询卡 ──────────────────────┐   ┌ 知识回答卡 ─────────────────────┐
│ 订单状态查询                      │   │ 根据售后政策：7 天内支持退货     │
│ 来源：✓ 订单系统（实时查询）      │   │ 来源：售后规则 V3（更新 09-20）  │
│ 订单号：123456   状态：运输中     │   │ [查看完整政策] [不适用，转人工]  │
│ 更新时间：10:32                   │   └─────────────────────────────────┘
└──────────────────────────────────┘
```

- **业务查询**：必须标注数据来源系统 + 实时/缓存 + 更新时间——用户对"已经发货"这类话术的第一反应是"真的假的"；
- **知识回答**：引用具体政策文档与版本（RAG 引用机制已有，此处要求**扩展到 UI 层渲染**），低置信（CAUTIOUS）回答显示"仅供参考"徽标；
- **操作确认**：Proposal 确认卡（§4.5）本身是最重要的可信状态载体——金额/对象/后果一目了然。

## 2.4 快捷操作卡（结构化卡片，不是纯文本）

客服输出不全是文本：结构化数据用卡片投影，卡片自带下一步动作——这是电商客服体验的核心。

```
┌ 订单 ───────────────────────────┐      ┌ 退款申请 ─────────────┐
│ 商品：iPhone 保护壳              │      │ 金额：¥89             │
│ 状态：运输中                     │      │ 原因：商品质量问题     │
│ [查看物流] [申请退款] [联系人工]  │      │ [确认退款] [取消]     │
└─────────────────────────────────┘      └───────────────────────┘
```

- 卡片 = 结构化数据的 UI 投影：SSE done 帧携带结构化块（与 pending_action 同机制扩展），前端按类型渲染；
- 点击卡片按钮 = 发送一条携带参数的消息（不绕过意图路由与安全链，只是省掉打字）；
- 核心价值：把"用户组织语言描述诉求"的成本降为零，同时天然消除分类歧义。

## 2.5 体验红线（任何优化不得突破）

1. 转人工入口永远可达（首屏 + 任意对话轮次）；
2. AI 不确定时明说（拒答 + 建议），绝不编造；
3. 确认类操作必须用户主动点击/明确文字确认，卡片不可诱导误触；
4. 人工接管后用户不需要向坐席重复任何已提供的信息（§9.1.2⑨ 接单卡兜底）；
5. 系统异常不甩锅用户（静默降级 + 事后回访，§9.1.2⑥）。

---

# 三、整体架构设计

## 3.1 七层架构

```
┌────────────────────────────────────────────────────────────────────┐
│ L1 接入层                                                          │
│  APISIX：JWT 鉴权 · 身份头注入 · 限流 · SSE/WS · 审计日志采集       │
├────────────────────────────────────────────────────────────────────┤
│ L2 会话管理层（Conversation Service）                               │
│  会话/消息读写（写权唯一在服务端）· 跨轮上下文（槽位/最近订单）      │
│  待确认动作存取 · 输入中指示器 · 会话投影（供派单/工作台消费）       │
├────────────────────────────────────────────────────────────────────┤
│ L3 Agent 编排层（CS Domain Graph）                                  │
│  Context Manager · Supervisor（规则状态机优先）· 5 专家 · Reporter  │
│  状态快照加载 · 统一专家上下文 · 请求级锚（pin）· 全链路 trace      │
├────────────────────────────────────────────────────────────────────┤
│ L4 Tool 层（Tool Registry + 执行器）                                │
│  统一契约（JSON Schema + 版本化）· 超时/重试/熔断 · 幂等键          │
│  写操作审批门 · 调用审计 · 错误分类                                  │
├────────────────────────────────────────────────────────────────────┤
│ L5 业务服务层                                                       │
│  Business Gateway（认证/归属校验/聚合）⇄ Java 业务服务（订单/物流/  │
│  售后/支付）⇄ 业务库。客服域零直连业务库。                          │
├────────────────────────────────────────────────────────────────────┤
│ L6 数据存储层                                                       │
│  PostgreSQL：cs_* schema（会话/消息/工单/确认/审计，唯一事实源）     │
│  Redis：presence 心跳 · 置忙 · 限流 · 热点会话缓存                  │
│  向量库：客服知识库（cs_faq/policy/product/aftersales/complaint…）  │
├────────────────────────────────────────────────────────────────────┤
│ L7 人工客服系统                                                     │
│  Dispatch Worker（独立进程：回收→派单→事件投递，1s tick）           │
│  WS Hub（坐席实时通道 + 事件 outbox 重放）· 坐席工作台前端           │
└────────────────────────────────────────────────────────────────────┘
```

## 3.2 关键架构决策（ADR 摘要）

| # | 决策 | 理由 |
|---|---|---|
| ADR-1 | 跨轮状态权威在 PG 业务表，LangGraph checkpointer 只作可选加速 | 会话状态被三条链路（AI 图 / 派单 worker / 坐席工作台）共同读写，必须是进程外持久事实源；checkpointer 依赖会让图状态成为第二事实源 |
| ADR-2 | 确认状态 = 双层存储（进程内 L1 缓存 + PG），写序为"DB 成功才更新 L1" | 读多写少热点在内存，正确性在 DB；DB 失败时严格模式 fail-closed，禁止 cache-only 降级（内存/DB 分叉是真实事故模式） |
| ADR-3 | 派单绑定权威在 PG 事务，Redis 只回答"在线/置忙" | Redis 只做易失信号（心跳 45s TTL），绑定/容量/公平性由行锁 + SKIP LOCKED 保证；Redis 挂 = fail-closed 不派单，宁可慢不可错 |
| ADR-4 | 事件投递用 transactional outbox | 状态变更与通知事件同事务落库，提交后广播失败可重放；杜绝"状态已变、通知永失" |
| ADR-5 | 副作用执行走 durable ledger（幂等账本） | 写操作的多实例并发 / 超时重入 / 运维重试必须"最多生效一次"；ledger 与确认行、审计同事务 |
| ADR-6 | AI 侧零 LLM 参与确认链与派单 | 资金与人力的调度是确定性规则领地，LLM 只出现在理解与生成环节 |
| ADR-7 | 所有 AI 可调的读接口经 Gateway 强制资源归属校验 | AI 的参数视为不可信输入，归属校验放在服务端数据边界，而不是靠 prompt |

---

# 四、Agent 设计

客服域共 **1 个 Context Manager（组件，非 Agent）+ 1 个 Supervisor + 5 个专家 Agent + 1 个 Reporter**。划分原则：按**安全边界与数据流向**划分（只读查询 / 副作用操作 / 情绪风险 / 人力协同 / 知识生成），而不是按业务名词分类——这样每个 Agent 的权限、超时、降级策略可以独立收敛。**不要拆出退款 Agent / 物流 Agent / 订单 Agent / 支付 Agent**——那些是 Tool/Skill（§六），按业务名词拆 Agent 只会导致 Agent 数量爆炸且每个都长一样。

## 4.1 Context Manager（上下文管理器——组件，不是 Agent）

**位置**：`state_loader → context_manager → Supervisor → Expert`。它在意图路由结果之上，把散落在会话各处的上下文**统一解析一次**，产出所有专家共享的「专家执行上下文」。**禁止每个专家各自解析**——否则 Query/Action/Complaint 都要各写一遍"我要订单号"的解析代码，重复且必然漂移。

**负责解析并注入**（确定性规则，零 LLM）：

| 上下文项 | 来源 | 消费方 |
|---|---|---|
| 用户身份三元组 + 角色 | 身份链（§11.1） | 全部专家 / Tool 归属校验 |
| 当前订单上下文（recent_order_id + 本轮实体抽取） | 会话业务列 + understanding 实体正则 | Query / Action / Complaint |
| 历史投诉与情绪等级 | cs_case（活跃案件，§10.1）+ 会话情绪标记 | Complaint / Supervisor 升级判断 |
| 当前 pending 动作 | cs_confirmation | pending_handler / Action |
| 最近一次人工状态（何时转、为什么、结论） | cs_handoff 终态 + 处理摘要 | Handoff / Reporter（回 AI 时不装失忆） |

**实现约束**：

- 纯函数组件 + 请求级缓存（同轮内解析一次，全图共享），**零 LLM**；
- 是上下文的**汇聚点**而非第二套解析器：实体/槽位来自 understanding 层，业务上下文来自 conversation 业务列——解析逻辑只此一份；
- 解析结果注册为请求级 pin（防上下文裁剪误删），并写入 trace（`ctx_*` tags）供评估归因；
- 解析失败降级为"缺该上下文"继续执行（由专家的缺槽追问兜底），绝不阻塞主链路。

## 4.2 Supervisor Agent

> **定性：Supervisor 不是"会思考的 Agent"，而是一台带 LLM 兜底的规则状态机。**企业客服的 Supervisor 一旦过度 LLM 化，成本、延迟、不可控三个问题全来。判定优先级固定（见下），规则路径单次决策目标 <10ms；LLM 仅在低置信兜底位出现，且可通过开关一键关闭——**关闭后服务不降级**，只是低置信场景按规则收尾。

**职责**：专家调度与终止控制。不做业务逻辑、不碰 DB、不生成最终回复。

- **输入**：用户消息（规范化后）、意图路由结果 `cs_route`（domain/intent/confidence/route_path/risk_level，由域内 Router 产出）、会话状态快照（handoff_state / confirmation_state / pending_action）、专家执行历史。
- **输出**：`Command(goto=expert|reporter, decision{next_action, next_expert, layer, reason})`，同时输出决策审计字段（decision_layer 如实标注，供离线评估）。

**route 策略（三层，规则优先 → 状态判断 → 模型判断）**：

```
L1 硬规则（确定性，零延迟，覆盖 ~85% 流量）
 ├ 1a handoff 拦截：会话处于人工排队/已接管 → 强制派 Handoff（AI 零抢答）
 ├ 1b 循环预算：expert_loop_count ≥ 5 → 强制 finish
 ├ 1c 安全拦截：risk 命中越权/注入 → 拒答或转人工（护栏在图前已拦，此处兜底）
 └ 1d 低置信降级：confidence < 0.6 且无历史 → finish；
        例外：知识类低风险请求放行检索（知识库自带相关性校验，比泛化兜底更有用）
L2 状态组合（确定性，零延迟）
 ├ 2a confirmation pending → finish（等用户确认，pending_handler 已在图入口拦截，
 │     此处兜底防 pending 态被绕过）
 └ 2b 同一专家连续执行 2 次 → finish（防死循环）
L3 模型判断（仅"低置信 + 已有专家历史"时，目标 <5% 流量）
 └ 小模型 · 结构化输出（只允许输出专家枚举名）· 800ms 线程级超时
      · 失败确定性回退：intent → expert 映射表（单一事实源）
```

**固定判定优先级**（与实现顺序一致，禁止重排——顺序即语义）：

```
1. 人工状态      handoff 拦截（排队/接管中，AI 零抢答）
2. pending 确认   等用户确认（图入口 pending_handler 已拦截，此处兜底）
3. 风险状态       安全护栏 / 高危操作拦截
4. 循环与预算     防死循环守卫
5. 意图路由       route_path → 专家（强先验，绝大多数流量到此为止）
6. 低置信处理     降级 / 知识类放行规则
7. LLM 兜底      仅低置信 + 有历史（可全局关闭）
```

**是否调用 LLM**：仅第 7 层，且默认小模型（决策这种任务不需要主力模型）；prompt 固定模板，输出约束为枚举。

**如何避免循环（三重防护）**：最大轮数（5）+ 同专家重复检测（连续 2 次）+ 轮级 token/时间预算（超预算强制 finish 并转人工建议）。

**如何控制成本**：
1. 意图路由的 confidence 是强先验——规则层做实（关键词+正则+实体感知），让 85%+ 流量在 L1/L2 消化；
2. L3 用小模型 + 极短 prompt（用户消息截断 200 字 + 专家枚举）；
3. 决策层埋点（`record_supervisor_decision(layer, action)`）持续监控 L3 触发率，触发率上升 = 规则词表该扩了（这是词表运营的信号，不是调 prompt 的信号）。

## 4.3 Knowledge Agent（知识问答）

**负责**：FAQ、商品规则、售后政策、使用说明。只读，无副作用。

**RAG 链路**（复用平台统一 RAG 单例，按客服知识库分组 + 受众过滤，不建独立 RAG 栈）：

```
Query Rewrite（规则规范化：NFKC/零宽剥离；可选 LLM 改写，3s 超时失败跳过）
   ↓
Hybrid Retriever（向量 top-k + BM25 top-k，按 kb_ids 限定客服六库，
   检索侧强制 audience=customer 过滤）
   ↓
同文档扩展（召回片段所属文档的相邻/父子 chunk 补齐）
   ↓
Reranker（cross-encoder 重排，top-n ≤ 5）
   ↓
Context Filter / Evidence Gate（相关性阈值 + 证据非空判定；不满足 → 拒答）
   ↓
Answer（带引用生成；置信度来自检索分 + META 判定，兜底值必须保守）
   ↓
置信分级输出：
  ≥ 0.85        直接回答（附引用）
  0.60 ~ 0.85   回答 + "以上内容仅供参考"后缀（CAUTIOUS）
  < 0.60 / 无证据 拒答 → 附追问建议（clarify）→ 连续 2 轮拒答自动建议转人工
```

- **使用 Tool**：`rag.search`（唯一知识 Tool）、`kb.suggest`（拒答时推荐相近问题）。
- **超时**：检索 10s + 生成 20s，节点级 60s 兜底（线程级强制，不依赖 LLM client 的 timeout 参数——该参数实测不可靠）。
- **空结果处理**：结构化拒答（"未找到相关信息"）+ clarify 追问 + 未答问题登记（进入知识运营待补充清单，这是知识库冷启动的正反馈闭环）。
- **低置信处理**：CAUTIOUS 后缀 + 不进入"AI 解决"统计口径（避免虚高解决率）+ 拒答样本进评测集候选。
- **身份贯通**：匿名用户知识上下文落 `cs-anon:{session_id}` 会话级命名空间，绝不共享长期记忆（防跨用户污染）。

## 4.4 Query Agent（业务查询）

**负责**：订单、物流、支付、优惠、工单进度等只读查询。

**调用链（铁律：AI 禁止直接访问数据库）**：

```
AI（生成结构化参数：order_id / 时间范围 / 意图）
  ↓
Tool（参数校验 · 归属参数注入 · 30s 结果缓存 · 审计埋点）
  ↓
Business Gateway（服务间 token · 租户头 · 资源归属校验 · 聚合/限流）
  ↓
Java Service（业务逻辑 + 行级数据权限）
  ↓
业务数据库
```

设计要点：
- **归属校验在 Gateway/Java 侧强制**：`order_id` 是否属于当前 user/tenant 由服务端判定，AI 参数只是"候选输入"。查无此单（别人的订单）与查不了（系统故障）必须返回**不同的错误语义**——前者是业务空结果（AI 引导核对单号），后者是降级话术（AI 绝不编造）。
- **复合问题**（"帮我看看订单和退款到账没"）：规则预判命中多域 → LLM 意图分解（3s 超时，失败确定性降级为单意图逐个答）。
- **超时**：单 Tool 5s；整体查询节点 30s。
- **回指与补槽**：查到唯一订单后写会话业务上下文 `recent_order_id`（下一轮"那它到哪了"可继承）；从消息抽取订单号用统一实体正则（全大写归一、形近错别字零改写）。
- **缓存**：同 (user, order_id, intent) 30s TTL，防连点刷库；写操作路径零缓存。
- **失败降级**：Gateway 超时/5xx → 友好话术 + 可选转人工建议；**绝不降级回演示/假数据**。

## 4.5 Action Agent（业务操作，安全核心）

**负责**：退款、退货、换货、修改地址等副作用操作。**Agent 本身零 LLM、零直接执行**——它的产出是"提案"，执行在确认链。

**确认机制全链路**：

```
用户请求"申请退款"
  ↓
① 资格检查（Tool 只读：订单状态/金额/7 天窗口/是否已退过；查重失败 fail-closed 拒绝）
  ↓
② 生成 Proposal（动作类型 + 目标 + 金额 + 风险等级 + 语义指纹；落 confirmations 表，TTL 15min）
  ↓  （大额 ≥1000 元额外标记 requires_human_review）
③ 用户确认（确认卡片 / 文字关键词「确认」；TTL 内可「取消」）
  ↓
④ 原子认领（DB 条件 UPDATE pending→confirmed，并发只有一方成功；败方返回"已处理"）
  ↓
⑤ 幂等执行（durable ledger：key=(tenant,user,action,confirmation_id)，最多生效一次）
  ↓
⑥ 结果落定（success / failed / verifying）+ 审计同事务 + 用户通知
```

**状态机**：

```
                    ┌──────────┐
     建提案          │  PENDING  │  TTL 15min 超时 ──► EXPIRED
   ┌──────────────►  └────┬─────┘
   │                      │ 用户确认（原子认领）      │ 用户取消
   │ need_info 补槽        ▼                        ▼
   │                 ┌──────────┐              CANCELLED
   │  缺订单号：      │ CONFIRMED │
   │  持久化追问      └────┬─────┘
   │  （retry ≤ 3）        ▼
   │                 ┌──────────┐   执行成功
   │                 │ EXECUTING │────────────► SUCCESS
   │                 └────┬─────┘
   │          ┌───────────┴────────────┐
   │          ▼ 明确失败                ▼ 结果未知（超时/网络分裂）
   │      FAILED  ◄──重试耗尽      ┌──────────┐
   │                              │ VERIFYING │ 人工裁决后 → SUCCESS / FAILED
   └──────────────────────────────└──────────┘
      （VERIFYING 期间锁定该语义操作，禁止重复发起）
```

**幂等机制（三层防线，各自拦截不同竞态）**：

| 层 | 机制 | 拦截的竞态 |
|---|---|---|
| 提案层 | partial unique index：`(tenant, action_type, target_id, fingerprint)` 在 active 状态集 `(pending,confirmed,executing,verifying)` 上唯一 | 同一订单并发重复发起退款（两个窗口同时点） |
| 认领层 | DB 条件 UPDATE `pending→confirmed`，影响行数即胜负 | 同一用户并发重复点确认（20 并发 1 胜 19 败为验收标准） |
| 执行层 | durable ledger 幂等键 `cs.action:{confirmation_id}`，执行结果落 `action_record` | 多实例部署、执行超时重入、运维重试导致的双执行 |

**UNKNOWN（VERIFYING）状态处理**：执行超时/连接断裂导致结果未知时——确认行落 `VERIFYING`（**继续占住提案层唯一索引**），用户收到"结果未知、请勿重复提交"，人工通过裁决接口核实真实结果后收敛为 SUCCESS/FAILED。**绝不落 FAILED**（那会释放索引、打开二次执行窗口）。裁决路径必须有运维 CLI/API 与演练记录。

**重试机制**：读操作可重试（幂等天然安全）；执行操作只在 ledger 幂等键保护下由运维/恢复任务重试，AI 链路内**永不自动重试**写操作；确认追问 retry ≤ 3 后按过期处理。

**审计记录**：每次状态转移 append-only 写 `cs_audit_log`，与动作记录**同事务**；`action_record` 落库幂等（同 action_id 只写一次）；审计含 conversation_id 外键（会话归属），防"审计孤儿"。

**风险分级**：

| 级别 | 标准 | 流程 |
|---|---|---|
| 低 | ≤100 元且订单状态明确 | 确认后自动执行（可选配置） |
| 中 | 一般退款/改地址 | 确认链 |
| 高 | ≥1000 元 / 批量 / 账户类 | 确认链 + 人工复核标记（复核不通过由坐席在管理端驳回） |

## 4.6 Complaint Agent（投诉处理）

**负责**：投诉识别、情绪识别、风险判断、案件创建、自动升级。落库实体为统一案件表 **cs_case**（§10.1）——工单/投诉/售后收敛于此：**聊天 ≠ 问题**，一个会话通常就是一个案件，案件也可跨会话（回访、重新进线）。

**投诉等级与处理流程**：

| 等级 | 判定信号 | SLA | 处理流程 |
|---|---|---|---|
| **P0** | 监管/媒体信号（12315、消协、曝光、记者）、群体维权暗示 | 5 分钟人工响应 | 立即建案件 + **直接转人工**（跳过安抚）+ 通知主管 + 话题标记 |
| **P1** | 强烈不满（连用贬损词、重复投诉、威胁差评） | 30 分钟首次响应 | 建案件 + 安抚话术 + 转人工优先派单 |
| **P2** | 一般不满（"不太满意""太慢了"） | 24 小时 | 建案件 + 安抚话术 + AI 继续尝试解决，超时未解决升级 P1 |

- **情绪识别**：级联——规则词表强信号优先（零成本、确定性），规则 0 命中才 LLM 兜底评估（3s 超时；**必须线程级限时**，不能用 client timeout 参数）。情绪只影响路由与优先级，拦截归安全护栏。
- **风险判断**：越权信号（"查别人的订单"）、批量维权信号（"我们一群人都要退"）→ 升级 + 风险标记进案件。
- **防重入**：同 turn 幂等（一次投诉只建一单）+ 会话级活跃投诉检查（已有未关闭投诉案件不重建）。
- **案件内容**：情绪等级、触发原因、会话上下文摘要、涉及订单——保证坐席接手时零追问。

## 4.7 Handoff Agent（人工转接）

**负责**：一切"AI → 人"的切换执行。

**触发条件**：

| 类型 | 触发 | 方式 |
|---|---|---|
| 用户主动 | "转人工/找真人"（正则确定性命中）· 连续不满 · 重复追问 | 直通（conf=1.0，不经任何推断，也不落灰度对照组） |
| AI 无法回答 | 连续 2 轮拒答 / 低置信循环终止 / 兜底话术触发 | Supervisor 决策 |
| 高风险 | 安全护栏命中高危类 / 大额操作复核 / P0 投诉 | 规则强制 |
| 多轮失败 | 补槽/确认追问达上限（3 次） | 释放槽位 + 转人工（携带已收集的碎片信息） |
| 系统异常 | Tool 熔断 / LLM 全挂 / 执行结果未知（VERIFYING） | 降级话术 + 人工/裁决队列（不询问用户，静默介入） |
| 人工接管期 | 会话已处于排队/接管态，用户任何消息 | 强制接管（防域检测漏判把用户晾在主图） |

> Handoff Agent 是人工介入的**统一执行入口**，但触发源不只在图内——全系统共 9 类人工介入点（含人工抽检、坐席辅助 Copilot 两类非接管形态），全景与各点详设见 §9.1。

**状态机**：

```
AI_ACTIVE ──触发──► HANDOFF_REQUESTED ──建单入池──► WAITING_HUMAN
                                                        │ dispatcher 派单
                                                        ▼
              CLOSED ◄──超时总期限/用户放弃──      AGENT_OFFERED
                 │                                    │ 坐席接单
                 │ 恢复 AI 服务 + 通知用户              ▼
                 └──────────────────────────────  HUMAN_ACTIVE
                                                        │ 问题解决
                                                        ▼
                                                     CLOSED ──(可选)──► AI_ACTIVE
```

- **WAITING_HUMAN 总等待期 120s**：超时关单 + 恢复 AI + 给用户"留言兜底"话术（坐席会主动联系），绝不无限悬挂。
- **AGENT_OFFERED 30s 超时**：坐席未接 → 释放回队列重派（attempt ≤ 3），本单拒过/超时的坐席排除。
- **HUMAN_ACTIVE 期间**：用户消息全部转达人工（坐席可见），AI 侧只做两件事——坐席辅助建议 + 消息落库；**绝不输出给用户**。
- **CLOSED 恢复 AI**：转接原因、处理结果摘要写回会话上下文，AI 恢复服务时先致歉再继续，不装失忆。

---

# 五、业务流程设计

## 场景 1：用户查询订单

```
用户：帮我查一下订单
  ↓ Intent Gateway：命中客服域 → 域内路由 TRANSACTION / t_order_status（conf 0.8）
  ↓ Supervisor L1：route_path → Query Expert
  ↓ Query Agent：无订单号 → 读会话上下文 recent_order_id
      ├ 有（本轮或历史轮查过）→ 直接用
      └ 无 → Tool order.list(user) → Gateway → Java
             ├ 唯一订单 → 直接展示 + 写 recent_order_id
             └ 多订单 → 列表卡片让用户选（不猜"最新一单"当默认执行对象）
  ↓ Tool：参数校验 → 30s 缓存查 → Gateway（归属校验）→ Java → 业务库
  ↓ Reporter：订单快捷操作卡（状态/金额/物流摘要 + [查看物流][申请退款][联系人工]，§2.4）
  ↓ SSE done 帧 → 用户
跨轮：用户"那它发货了没" → 实体感知改写继承 order_id → 精确查物流
```

## 场景 2：退款申请（含失败恢复）

```
用户：DEMO-1006 申请退款
  ↓ 路由 AFTER_SALES / as_refund → Action Agent
① 资格检查 Tool（只读）：订单存在且归属当前用户 ✓ · 状态"已签收" ✓ · 7 天窗口内 ✓
   · 查重：无进行中/已成功的同语义退款 ✓（查重查询失败 → fail-closed 拒绝，防双退）
② 风险分级：金额 89 元 → 中风险，标准确认链
③ Proposal 落库（TTL 15min，语义指纹 = refund+order+金额）→ SSE done 帧带 pending_action
④ 前端确认卡：「申请退款 ¥89，是否确认？」
   ├ 用户点「确认」/ 回复「确认」
   │   ↓ pending_handler 拦截 → confirmation_flow
   │   ↓ ④ 原子认领（条件 UPDATE pending→confirmed；重复点 = duplicate 话术）
   │   ↓ ⑤ durable ledger 幂等执行（Java 侧真实退款）
   │   ├ 成功 → SUCCESS + 审计 + 「退款已受理，预计 1-3 个工作日到账」
   │   ├ 明确失败（Java 返回业务拒绝）→ FAILED + 可读原因（"超过退款窗口"）
   │   └ 结果未知（超时/断连）→ VERIFYING + "请勿重复提交，人工核实中"
   │        → 运维裁决 → SUCCESS/FAILED
   └ 用户「取消」→ CANCELLED + 审计
   └ 用户答非所问 → 追问（retry ≤3 → 按过期处理）
```

**体验设计（用户旅程）**——工程链路每一环都要翻译成用户看得见的体验：

```
用户：我要退款
AI：好的，帮你申请退款。
    正在查询订单：DEMO-1006（自动带上最近订单，不让用户背单号）
    符合退款条件 ✓
    商品：iPhone 保护壳
    退款金额：¥89
    预计到账：1-3 个工作日
    [确认退款]  [取消]
```

原则：**能查到的不问**——订单号（会话上下文/最近订单）、金额、资格结果全部主动补全；**只问真正缺的**——退款原因给常见选项让用户点选，而不是让用户自由输入订单号/原因/金额三段信息。理想路径下用户全程最多两次点击（选原因、确认）。

失败恢复路径：
  · 执行层失败但 ledger 有 running 记录 → 恢复任务按 ledger 状态续跑/裁决
  · VERIFYING 积压 → 运维 CLI 批量核查 + 工单化
```

## 场景 3：投诉

```
用户：你们这服务太差了，我要投诉！
  ↓ 路由 COMPLAINT / c_complaint → Complaint Agent
  ↓ 情绪识别：规则命中「太差了」「投诉」→ 2 命中 = P1
  ↓ 防重入检查：本会话无活跃投诉案件 ✓
  ↓ Tool case.create（幂等键 = conversation+turn）：案件落库（cs_case）
      （等级 P1 / 触发原因 / 情绪标记 / 会话摘要 / 涉及订单自动附带）
  ↓ 安抚话术（模板，非 LLM——安抚要稳定，不要模型发挥）
  ↓ P1 → 自动转人工：Handoff 状态机 AI_ACTIVE → WAITING_HUMAN
  ↓ Dispatcher：优先派给有 complaint 技能的在线坐席（required_skill 匹配）
  ↓ 坐席工作台弹出来电卡片：案件 + 完整会话上下文 + AI 建议
升级分支：
  · 用户提到「12315」「曝光」→ P0：跳过安抚直接转人工 + 主管通知 + SLA 5min
  · 会话再爆强信号 → 情绪等级只升不降（escalate monotonic）
```

## 场景 4：复杂多轮（缺槽补全）

```
Turn 1  用户：我要退货
  ↓ 路由 as_return → Action Agent
  ↓ 副作用动作缺 order_id → 生成 need_info 型 pending（持久化到 confirmations 表，
    status=need_info）→ 结构化追问"请提供要退货的订单号"
  ↓ （关键：追问状态跨轮持久化，不依赖进程内存与 checkpointer）

Turn 2  用户：MO-3C052B3A
  ↓ pending_handler 在图入口拦截（先于 Supervisor）
    ├ 检测到 need_info pending → 转发 Action Agent 优先补槽
    ├ 实体正则抽到订单号（含字母防误吞日期/电话分段）→ 大写归一
    └ 补槽成功 → 原地资格检查 → 生成正式 Proposal → 确认卡
  ↓ （补不到 → retry 追问；3 次上限 → 释放，本轮按普通消息处理）
  ── 若 Turn 2 用户说"算了" → 取消 pending，语义不残留

Context 保存设计：
  · pending_action       → confirmations 表（跨轮、跨进程、重启安全）
  · recent_order_id      → 会话业务上下文（conversation 业务列，请求级 pin 注册防裁剪）
  · 对话原文             → cs_message（seq 单调，分块摘要供长会话）
  · 槽位校验规则         → REQUIRED_SLOTS 表驱动（intent → 必填槽位），不是散在 prompt
```

---

# 六、Tool 设计

## 6.1 Tool Registry 规范

- **命名**：`<域>.<动作>`，全域唯一；capability 元数据（描述/参数 schema/示例）只写在一份清单（capabilities.yaml 类单源），启动期绑定 + fail-fast——代码与元数据分离，防止"注册了没接线"。
- **契约**：JSON Schema 严格模式；只向后兼容演进（新增可选字段）；破坏性变更 = 新 Tool + 旧 Tool 废弃期。
- **返回**：统一 `Result{ok, data, error{code, message, retryable, user_message}}`；"查不到"（业务空）与"查不了"（系统故障）**必须分开**。
- **写操作 Tool 三硬规**：幂等键必填 · 过审批门（`ensure_approved`，高危动作先建审批单）· 与审计同事务。
- **注册**：谁定义谁注册（定义文件底部自注册），启动期一致性校验（注册表 vs 清单 diff = 启动失败）。

## 6.2 Tool 清单

### 查询类（只读，登录用户即可，归属校验在 Gateway）

| Tool | 输入 | 输出 | 超时 | 异常处理 |
|---|---|---|---|---|
| `order.query` | order_id（或 recent 标记） | 订单头+明细+状态 | 5s | 404→业务空话术；5xx→降级话术 |
| `order.list` | 分页/状态过滤 | 订单摘要列表 | 5s | 空→引导提供单号 |
| `logistics.query` | order_id | 物流轨迹（聚合承运商） | 8s | 轨迹源失败→降级订单状态推导 |
| `payment.query` | order_id | 支付状态/退款进度 | 5s | 同 order.query |
| `promotion.query` | user_id, order_id? | 优惠券/权益状态 | 5s | 空→业务空话术 |
| `case.query` | user_id, case_id? | 案件状态/进度 | 5s | — |

### 知识类（只读）

| Tool | 输入 | 输出 | 超时 | 异常处理 |
|---|---|---|---|---|
| `rag.search` | query, kb_ids, top_k | 片段+得分+引用 | 10s | 空→触发拒答链 |
| `kb.suggest` | query | 相近问题 top-3 | 3s | 静默空 |

### 操作类（副作用，全部需幂等键 + 审批门 + 确认链前置）

| Tool | 输入 | 输出 | 超时 | 异常处理 |
|---|---|---|---|---|
| `refund.create` | order_id, amount, reason, idempotency_key | 退款单号 | 30s（异步受理+回调） | 结果未知→VERIFYING |
| `return.create` | order_id, items, reason, idempotency_key | 售后单号 | 30s | 同上 |
| `exchange.create` | order_id, item, target_sku, idempotency_key | 换货单号 | 30s | 同上 |
| `address.update` | order_id, new_address, idempotency_key | 更新结果 | 15s | 发货后拒绝（业务规则） |
| `case.create` | case_type, priority, context, idempotency_key | 案件号 | 5s | 落库失败→重试队列 |
| `order.cancel` | order_id, idempotency_key | 取消结果 | 15s | 仅特定状态可取消 |

### 人工协同类

| Tool | 输入 | 输出 | 超时 | 异常处理 |
|---|---|---|---|---|
| `handoff.create` | conversation_id, reason, priority, required_skill | 工单号+排队位 | 5s | 幂等（活动工单复用）；冲突→复用 |
| `handoff.cancel` | handoff_id | 结果 | 5s | 仅排队态可取消 |
| `assist.suggest` | conversation_id | AI 回复建议 top-3 | 15s | 失败静默（绝不阻塞坐席消息） |

> 权限速查：查询类 = 登录用户（资源归属服务端校验）；知识类 = 匿名可用（受众过滤）；操作类 = 登录 + 确认链 + 高危另需复核；协同类 = 坐席角色（RBAC）或 AI 系统身份。

---

# 七、状态管理设计

三层生命周期 + 一条铁律（跨进程可见的状态必须落 PG，内存只是缓存）。

## L1 请求级上下文（生命周期 = 一次 HTTP 请求 / 一次图执行）

| 保存 | 不保存 |
|---|---|
| 规范化用户消息、意图路由结果、专家执行历史、supervisor 决策链、本轮 trace tags、请求级实体 pin（本轮涉及的订单号——防上下文裁剪误删） | 任何需要下一轮还在的东西；大段对话原文（放 L2） |

实现：LangGraph state + ContextVar（trace/租户）。图结束即弃，checkpointer 开启时也只作恢复加速而非事实源。

## L2 会话级上下文（生命周期 = 会话，跨轮、跨进程）

| 保存 | 存哪 | 说明 |
|---|---|---|
| 会话元数据、消息流 | cs_conversation / cs_message | 写权唯一在服务端；seq 会话内单调（WS 回放游标） |
| 待确认动作（含 need_info） | cs_confirmation | 跨轮补槽、确认、过期都在这 |
| 转人工状态机 | cs_handoff | 排队/接管/关闭 |
| 业务回指槽位（recent_order_id、最近工单） | cs_conversation 业务列 | 请求级 pin 注册防裁剪 |
| 长会话摘要 | cs_conversation.summary | 超阈值后台异步摘要，滞后门控 |

## L3 用户级记忆（生命周期 = 用户，跨会话）

| 保存 | 不保存 |
|---|---|
| 服务偏好（偏好文字/隐私顾虑标记）、历史工单摘要（用于"上次的问题"回指）、投诉黑名单标记、语言偏好 | 支付凭证/密码等任何凭证类；明文手机号/身份证（脱敏后存摘要）；对话原文全量（只存摘要+要点向量）；其他用户的任何信息 |

**防跨用户污染（四道闸）**：
1. 身份三元组 `(tenant_id, user_id, conversation_id)` 全链路传递，任何存储读写强制带全三元组条件；
2. 匿名用户（未登录）一律 `cs-anon:{session_id}` 会话级命名空间，**永不写 L3**；
3. 记忆读取 SQL 强制 `WHERE tenant_id=? AND user_id=?`（应用层禁止拼接豁免）；缓存 key 必含三元组哈希；
4. L3 写入走独立评审（什么值得记是运营决策），默认白名单机制——不在白名单的不记。

---

# 八、异常和可靠性设计

## 8.1 超时预算（线程级强制，非 LLM client 参数）

| 环节 | 预算 | 超时行为 |
|---|---|---|
| 意图路由（规则） | ~0ms（正则） | — |
| 意图路由（LLM 仲裁，默认关） | 2s | 失败按规则结果 |
| Supervisor L3 决策 | 800ms | 确定性回退映射表 |
| RAG 全链 | 30s（检索 10s+生成 20s） | 节点 60s 兜底强杀→拒答链 |
| 业务查询单 Tool | 5s | 降级话术 |
| 复合问题 LLM 分解 | 3s | 降级单意图 |
| 操作执行 | 60s | 结果未知→VERIFYING |
| 坐席辅助建议 | 15s | 静默放弃 |
| 整轮预算 | 90s | SSE 保持心跳帧，超预算 finish+转人工建议 |

## 8.2 Tool 异常分类矩阵

| 异常类型 | 判定 | retry | fallback | 人工 |
|---|---|---|---|---|
| 网络超时/连接失败 | TimeoutError/ConnError | 读：1 次指数退避；写：**不自动 retry**（幂等键下由恢复任务重试） | 降级话术 + 可选转人工 | 连续失败熔断告警 |
| 参数错误 | Schema 校验失败 | ❌（重试无意义） | 结构化追问补槽 | — |
| 权限错误 | 401/403 | ❌ | "无法访问该信息" + 转人工建议 | 权限配置告警 |
| 业务异常 | Java 返回业务拒绝（如超退款窗口） | ❌ | **可读业务原因**直出（这是正常路径不是故障） | — |
| 资源不存在 | 404 语义 | ❌ | 引导核对单号（与权限错误区分话术） | — |
| 未知异常 | 兜底分类 | ❌ | 通用降级话术 + error code 进 trace | 告警 + 工单化 |

配套：Tool 级熔断（滑动窗口错误率 >50% 熔断 30s，熔断期直接走 fallback，不发无效请求）。

## 8.3 LLM 异常

| 异常 | 处理 |
|---|---|
| 超时 | **线程级强制限时**（client timeout 参数不可靠）；supervisor 800ms / 生成 20s；超时走确定性降级 |
| 限流（429） | 指数退避 1 次 → 降级（supervisor 回规则映射；RAG 返回 CAUTIOUS 拒答；辅助建议静默放弃）|
| 额度不足/配额熔断 | 全局降级开关：客服域切换小模型 → 模板话术兜底；绝不 500 给用户 |
| 返回格式异常 | 结构化解析失败 → 重试 1 次（加强格式指令）→ 确定性降级 |
| 内容安全异常 | 输出护栏拦截 → 替换安全话术 + 审计 |

降级阶梯总原则：**LLM 全挂 = 客服域降级为规则应答 + 转人工，服务不中断**；任何降级都要打指标（fallback 告警阈值 2%）。

---

# 九、人工客服系统与 Human-in-the-loop 介入策略

## 9.1 人工介入策略全景（9 个介入点）

### 9.1.0 设计原则

企业客服系统里，人工介入（Human-in-the-loop）**不是一个点（"转人工按钮"），而是一套分层接管策略**。三条设计原则：

1. **人工是状态机的正式节点，不是异常出口**。转接后可 CLOSED 回 AI，全程状态可追踪、SLA 可度量；
2. **每个介入点必须回答四件事**：谁检测（哪个 Agent/组件）、何时触发（什么阈值）、多快响应（SLA）、带什么上下文给人工（坐席零追问接手）；
3. **介入 ≠ 接管**：9 个介入点中，1~7 是"控制权转移"，8 是"事后质检"，9 是"辅助不接管"——AI Copilot 在人工会话中持续工作，但永不直接触达用户。

### 9.1.1 九个介入点统一视图

| # | 介入点 | 检测者（组件） | 触发条件 | 响应 SLA | 状态去向 |
|---|---|---|---|---|---|
| 1 | 用户显式转人工 | Handoff Agent（正则直通） | 关键词（转人工/真人客服/找客服）· 连续不满 · 重复追问 | 即时 | AI_ACTIVE → WAITING_HUMAN |
| 2 | AI 低置信自动升级 | Knowledge Agent + Supervisor | confidence < 0.6 · 证据冲突 · 连续拒答 | 同轮末尾 | 拒答+建议转人工 / 直接转人工 |
| 3 | 高风险业务强制人工 | Action Agent 风险分级 + 风险信号枚举 | 大额退款 · 账户盗用 · 支付异常 · 法律/舆情 | 前置拦截 | 确认链+复核标记 / 直接人工审核 |
| 4 | 情绪恶化自动升级 | Complaint Agent（情绪级联检测） | NORMAL→NEGATIVE→ANGRY→CRITICAL（只升不降） | 同轮 | P1/P2 工单 + 转人工（CRITICAL 立即） |
| 5 | 多轮失败升级 | Action Agent 槽位状态机 | 补槽/确认追问失败 ≥ 3 次 | 同轮 | 释放 pending + 转人工（带线索） |
| 6 | Agent 自身异常介入 | Tool 执行器熔断 + LLM 降级链 | Tool 连续失败 · LLM 全挂 | 即时 | 降级话术 + 人工/运维队列 |
| 7 | 执行结果未知（UNKNOWN） | confirmation 状态机 | EXECUTING 超时/网络分裂 → VERIFYING | 实时进队，裁决 SLA 30min | 人工裁决队列 → SUCCESS/FAILED |
| 8 | 人工抽检（主动质检） | 质检管道（Quality 评分 + 人工复核） | 每日分层抽样（默认 1000 会话） | T+1 | 知识库/话术优化/评测集回流 |
| 9 | 坐席辅助（Copilot，不接管） | agent_assist | HUMAN_ACTIVE 期间事件触发 | 异步 <15s | 坐席工作台建议卡 |

```
                    ┌─────────────────────────────────────────┐
                    │        人工介入触发源（9 类）             │
                    └─────────────────────────────────────────┘
 ① 用户显式 ─────────┐
 ② AI 低置信 ────────┤
 ③ 高风险业务 ───────┼──► Handoff Agent ──► Dispatcher ──► 坐席工作台
 ④ 情绪恶化 ─────────┤        （统一入口：建转接工单 + 会话摘要随行）
 ⑤ 多轮失败 ─────────┘
 ⑥ 系统异常 ──► 降级话术（先保服务）──► 运维/人工队列
 ⑦ 执行未知 ──► VERIFYING 锁定 ──► 人工裁决队列（查支付/退款系统核实）
 ⑧ 人工抽检 ──► 质检评分 ──► 人工复核队列 ──► 知识/话术/评测集三去向
 ⑨ 坐席接入后 ◄────────────── AI Copilot（摘要卡 + 推荐回复，辅助不接管）
```

### 9.1.2 各介入点详设

**① 用户显式转人工（最容易，但要覆盖隐性信号）**

- 显性触发：正则确定性命中（conf=1.0 直通，不经推断、不落灰度对照组）；
- 隐性触发：**连续不满意**（同轮内负面反馈词 ≥2 次）、**重复追问**（Supervisor L2 同专家连续执行检测 + 会话级追问计数）；
- 流程：Supervisor → Handoff Agent → 建转接工单（幂等：活动工单复用）→ Dispatcher → 坐席；
- 状态机：`AI_ACTIVE → HANDOFF_REQUESTED → WAITING_HUMAN → (AGENT_OFFERED) → HUMAN_ACTIVE → CLOSED`。

**② AI 低置信自动转人工（核心原则：不是所有问题都该硬答）**

升级阶梯（避免两个极端：瞎猜 和 一低就转）：

```
首轮 confidence < 0.6
  ├ 知识类低风险 → 放行检索（知识库自带相关性校验，比泛化兜底有用）
  ├ 动作/查询类  → 不执行，先追问澄清（一次）
  └ 证据冲突（多来源答案矛盾）→ 直接拒答，不选边
第二轮仍低置信 / 连续 2 轮拒答
  → 拒答话术 + 主动建议转人工（用户确认式：'是否为您转接人工？'）
动作类低置信永不执行（宁转人工，不赌资金）
低置信轮次不计入 AI 解决率（防指标虚高）
```

典型场景：知识库无答案、多答案冲突、问题过于模糊、业务接口无返回且无替代信息源。

**③ 高风险业务强制人工介入**

风险场景枚举（**不是 AI 自动决策的领地**）：

| 场景 | 处置 |
|---|---|
| 大额退款（阈值租户可配：≥1000 元确认链+人工复核标记，≥5000 元强制人工审核） | 确认链 + requires_human_review / 直接人工 |
| 账户被盗 / 支付异常 | risk=P0，跳过 AI 流程直接转人工 + 安全团队工单 |
| 投诉平台 / 法律问题 / 媒体舆情 | risk=P0，5 分钟人工响应，主管通知 |
| 批量维权信号 | risk=P0 + 风控标记 |

流程：风险检测（规则枚举 + 金额阈值 + 投诉信号）→ `risk_level=P0` → Handoff（带风险标记）→ 人工。AI 在此类会话中只做信息收集与安抚，不做任何承诺与操作。

**④ 情绪恶化自动人工（继续 AI 对话可能激化矛盾）**

情绪状态机与会话绑定、跨轮持久化、**只升不降**：

```
NORMAL ──负面词──► NEGATIVE ──强烈不满──► ANGRY ──威胁/监管词──► CRITICAL
   │                 │                     │                      │
 继续 AI          P2 工单+安抚            P1 工单+优先转人工      P0：立即转人工
                 （AI 继续）            （AI 降为安抚+收集）    （AI 停止解决问题，
                                                                只做安抚与信息记录）
```

检测级联：规则词表强信号优先（零成本确定性），规则 0 命中才 LLM 兜底（线程级 3s 限时）。等级写回会话（坐席工作台可见情绪标记），转接工单自动携带情绪轨迹。

**⑤ 多轮失败自动人工（Agent 已无法推进）**

```
用户：我要退款
AI：请提供订单号
用户：就是那个订单          ← 补槽失败（无订单号实体）
AI：请提供订单号（retry 2）
用户：……
slot_fill_retry ≥ 3
  → 释放 pending（不是按"过期"冷处理）
  → 主动转人工："已为您转接人工客服，坐席可以帮您定位订单"
  → 转接上下文携带：用户已提供的模糊线索（"那个订单"）、本轮尝试记录
```

同理适用于确认追问超限（proposal 被反复答非所问）。设计原则：**多轮失败后不再让用户从头来过**——AI 收集到的碎片信息全部随工单转交。

**⑥ Agent 自身异常人工介入（生产必需）**

- **Tool 失败**：订单服务 timeout×3 → 触发熔断（§8.2）→ 本会话该能力降级为兜底话术 → 连续失败自动建运维工单 → 用户侧"系统繁忙，已为您转人工/稍后回访"。**禁止 AI 在异常后编造结果**。
- **LLM 异常**：超时（线程级限时）→ 重试 1 次 → 备用小模型 → 模板话术兜底 → 转人工建议（§8.3 降级阶梯）。
- 判定规则：**系统异常类介入不询问用户**（用户没义务知道后端挂了），静默转人工或降级，事后可回访。

**⑦ Action 执行未知状态人工介入（企业最关注）**

退款已提交但结果未知——**绝不能报"失败"**（可能已成功，报失败会诱导用户重复操作→双退）：

```
用户确认 → EXECUTING → 网络断开/超时
  → 状态落 VERIFYING（占住唯一索引，同语义操作禁止再次发起）
  → 用户话术："操作已提交，系统正在核实最终结果，请勿重复提交"
  → 进入人工裁决队列（SLA 30 分钟）
  → 人工核实：查支付系统/退款单真实状态
  → 裁决收敛：SUCCESS / FAILED（全程审计，支持运维 CLI 批量处理）
```

配套：裁决队列积压告警（>10 条或 >30min）；对账报表（VERIFYING 发起量 vs 裁决收敛量日清日结）。

**⑧ 人工抽检（主动介入：不是等失败，是主动找问题）**

```
每日分层抽样（默认 1000 会话，可配）：
  随机层 70%（全量无偏估计）
  风险加权层 30%（差评会话 / P0·P1 投诉 / 退款类 / 新上线知识话题 必抽）
  ↓
Quality 评分（AI 预评，三维度）：回答正确性 / 合规性 / 可优化性（知识缺口）
  ↓
低分 + 高风险会话 → 人工复核队列（质检员）
  ↓
复核结果三去向：
  ① 知识库补充（拒答/答错 → 运营 feed，闭个环）
  ② 话术/流程优化（安抚不到位/确认卡文案问题）
  ③ 评测集回流（bad case 一键入集，成为回归门禁用例）
```

指标：AI 预评与人工复核一致率（<90% = 预评模型需重训）；抽检发现缺陷按严重度进缺陷台账。

**⑨ 坐席辅助模式（Copilot：人工不是从 0 开始）**

人工接入后 AI **不退场、不抢答**，切换为辅助形态：

| Copilot 能力 | 内容 | 时机 |
|---|---|---|
| **接单卡（第一眼可解决）** | 结构固定为「用户：张三 · 问题：退款失败 · 订单：12345 · **AI 已完成**：✓查询订单 ✓判断退款资格 ✓创建退款申请（失败）· **当前需要人工**：确认支付状态」——不是会话流水摘要，而是"已完成步骤 + 待办动作"清单，坐席零追问直接处理 | 接单瞬间自动生成 |
| 推荐回复 | RAG 话术 + 订单上下文推荐，top-3 | 用户每条新消息后异步（<15s） |
| 知识卡 | 命中的政策/案件/物流信息 | 随推荐回复附带 |
| 情绪提示 | 情绪等级与升级预警 | 实时 |

硬边界：建议**异步生成、失败静默**（绝不阻塞坐席消息链路）；AI 建议只能"采纳后由坐席发送"，**永不以坐席身份直接触达用户**；Copilot 输出不进入对话记录（避免污染消息流与审计语义）。

### 9.1.3 小结：对外汇报口径（五类分层）

> 评审/汇报可用的一段话：我们的客服系统不是只有一个"转人工按钮"，而是一套 **Human-in-the-loop 闭环**，人工介入分五类——
>
> **第一类，用户主动**：明确要求真人客服时确定性直通，不经历任何推断；
> **第二类，AI 能力不足**：知识库未命中、置信度低于阈值、多轮无法补齐必要参数时，Agent 主动升级而不是硬答；
> **第三类，业务风险**：大额退款、账户安全、投诉升级、法律舆情，这些流程不走 AI 自动决策，直接进入人工审核；
> **第四类，系统异常**：业务接口连续失败、执行结果未知（UNKNOWN），系统降级保服务的同时进入人工处理/裁决队列，且结果未知时绝不报失败；
> **第五类，人工辅助**：坐席接入后 AI 转为 Copilot——提供用户画像、会话总结、推荐处理方案，辅助人工决策而不是替代。
>
> **一句话总结：人工介入不是一个 Agent，而是由风险检测、状态机、Handoff Agent、Dispatcher 共同组成的 Human-in-the-loop 机制——人工不是异常出口，而是客服域状态机中的一个正式节点。**

## 9.2 坐席状态

```
OFFLINE ──上线──► ONLINE ──接单满载──► BUSY ──释放──► ONLINE
   ▲                │  │                 │
   │                │  └──挂起──► AWAY ──┘（AWAY 不参与派单）
   └──下线/心跳断────┴─── 结单后 WRAPPING_UP（结案整理，可设自动回 ONLINE）
```

- 心跳：WS 连接 + Redis 心跳 key（45s TTL）；WS 断开 60s 未重连 → OFFLINE。
- 容量：每坐席 `max_concurrent`（默认 5）并发会话上限；派单按"在线 ∧ 未满载 ∧ 非置忙"过滤。
- **Redis 只回答在线/置忙，fail-closed**：Redis 不可用时本轮不派单（宁可排队，不可把单派给已下线坐席）。

## 9.3 自动派单（独立 worker，1s tick）

```
每 tick 三段（顺序刻意）：
① reaper 回收：offer 30s 超时 → 回队列重派（attempt≤3）；
   attempt 耗尽或超 120s 总期限 → 关单 + 恢复 AI + 留言兜底通知
② dispatch 派单（单 PG 事务）：
   队列头（priority DESC, created_at ASC 严格排序）
   → 技能匹配（required_skill == agent.skill，默认 general）
   → 在线 ∧ 未满载 ∧ 非置忙 ∧ 非本单冷却（拒过/超时过的坐席本单排除）
   → 最少负载 + 轮询公平（last_assigned_at 排序）
   → 写 agent_offered + assignment + outbox 事件（同事务）
   → 提交后广播（失败不回滚，outbox 重放兜底）
③ relay 投递：outbox pending 事件按 seq 重投（幂等 event_id）
并发正确性：全局锁序 conversations→handoffs→agents，全部 SKIP LOCKED；
   多副本无共享内存、无 Redis 锁，正确性全在 PG 事务 + 部分唯一索引。
```

## 9.4 派单失败处理

| 场景 | 处理 |
|---|---|
| 无在线坐席 | 工单留队列 + 用户侧显示预计等待；超 120s 总期限 → 留言兜底恢复 AI |
| 全部满载 | 排队；坐席释放容量后下一 tick 自动派 |
| Redis 不可用 | fail-closed 本轮不派单 + 告警（保绑定正确性优先于时效） |
| offer 全部被拒/超时 | attempt 耗尽 → 关单 + 恢复 AI + 通知用户"将有人工跟进" + 工单转待处理池 |
| 主管强制重派 | released 语义，允许指定回同一坐席（人工决策优先于算法冷却） |
| dispatcher 进程崩溃 | 另一副本下一 tick 接管（无状态，权威在 PG）；心跳中断告警 30s 触发 |

## 9.5 客服运营闭环（为什么解决不了？）

AI 上线只是开始，**运营闭环决定解决率能不能持续涨**。两个机制：

### 9.5.1 未解决问题池

```
今日：退款政策咨询 300 次
  ├─ AI 答复 250（归因：知识命中 180 / 业务查询 70）
  └─ AI 拒答 50
       └─ 原因聚类：知识库缺失 32 / 能力未覆盖 15 / 流程缺陷 3
            ├─ 知识库缺失 → 自动生成知识运营待办（含用户原始问法样本）
            ├─ 能力未覆盖 → 产品待办（评估是否新增专家/Tool）
            └─ 流程缺陷   → 缺陷台账（P1 起修）
```

- 拒答/降级/转人工的会话按原因**自动聚类入池**（复用 trace 的 decision_layer 与拒答埋点，不新增采集链路）；
- 池子带趋势告警：同一原因连续 3 天上升自动升级为运营工单；
- 首屏快捷入口（§2.2）的配置直接取自本池的高频意图——入口、知识库、评测集三个消费方共享同一份数据。

### 9.5.2 Bad Case 回流（复用既有评测体系，不另起炉灶）

```
失败会话（质检低分 / 用户差评 / 未解决池 Top）
  ↓ 人工标记（问题类型：知识错 / 工具错 / 流程错 / 模型错）
  ↓ 转结构化用例，锁版进评测集（cases.jsonl）
  ↓ Prompt / RAG / 规则词表优化
  ↓ 重新测试（评估集回归 + 灰度桶 A/B）
  ↓ 达标后合入基线，用例常驻 CI 门禁
```

要点：**平台的评估体系（锁版数据集 + 校验器 + CI 门禁）就是回流的目的地**——运营闭环的产出直接变成回归用例，防止"修好了又坏回去"。bad case 一键入集的能力挂在 trace 上（§12.3）。

---

# 十、数据库设计

独立 schema `cs`，全部表强制 `tenant_id` + `id` + `created_at/updated_at`，时间 `timestamptz`，金额 `numeric(12,2)`，状态字段 CHECK 约束，结构变更只走迁移脚本。

## 10.1 核心表

```sql
-- 会话表
cs_conversation (
  conversation_id  uuid PK,
  tenant_id        varchar not null,
  user_id          varchar not null,          -- 匿名时为 null，用 anon_session_id
  anon_session_id  varchar,                   -- 匿名会话命名空间
  channel          varchar,                   -- web/app/mini
  status           varchar check in (open, resolved, closed),
  handling_mode    varchar check in (ai, waiting_human, human),
  assigned_agent_id varchar,
  recent_order_id  varchar,                   -- 业务回指槽位
  summary          text,                      -- 长会话摘要（异步生成）
  satisfaction     smallint,                  -- 1-5 评分
  last_activity_at timestamptz,
  created_at/updated_at timestamptz
);  -- idx (tenant_id, user_id, status), (last_activity_at)

-- 消息表（按月分区）
cs_message (
  id              bigserial,
  conversation_id uuid not null,
  tenant_id       varchar not null,
  seq_no          bigint not null,           -- 会话内单调，WS 回放游标
  role            varchar check in (user, ai, agent, system),
  expert          varchar,                   -- 产出该消息的专家（AI 消息）
  content         text not null,
  content_type    varchar default 'markdown',
  metadata        jsonb,                     -- 引用/置信度/动作卡片等
  event_id        varchar unique,            -- 幂等去重
  created_at      timestamptz
);  -- PK(conversation_id, seq_no)；idx (conversation_id, created_at)

-- 案件表（客服运营的统一问题实体：工单/投诉/售后收敛于此）
-- 设计要点：聊天 ≠ 问题——一个会话通常只对应一个案件（10 轮聊天 = 1 个退款案件）；
-- 案件可跨会话（回访、重新进线）。与 cs_handoff 的边界：handoff 回答"谁来处理"
-- （接管协作单），case 回答"问题本身解决没有"（业务实体）。
cs_case (
  case_id       uuid PK,
  tenant_id     varchar not null,
  conversation_id uuid not null,
  user_id       varchar not null,
  case_type     varchar,                     -- refund / return / exchange / complaint / inquiry
  priority      varchar check in (P0, P1, P2),
  status        varchar check in (open, waiting_user, processing, resolved, closed),
  title         varchar,
  context       jsonb,                       -- 触发原因/情绪轨迹/关联订单
  owner_agent_id varchar,                    -- 当前责任人（AI 处理阶段为空）
  related_confirmation_id uuid,              -- 关联确认单（操作类案件）
  resolution    text,                        -- 处理结论（结案必填）
  sla_deadline_at   timestamptz,             -- 按优先级计算
  first_responded_at timestamptz,
  created_at/updated_at timestamptz
);  -- idx (tenant_id, status, priority), (user_id, created_at)

-- 转人工工单（派单队列）
cs_handoff (
  handoff_id     uuid PK,
  conversation_id uuid not null,
  user_id, tenant_id  varchar not null,
  state          varchar check in (waiting_human, agent_offered, human_active, closed),
  trigger_type   varchar,                    -- explicit / ai_suggest / risk / complaint
  priority       int default 50,
  required_skill varchar default 'general',
  attempt_count  int default 0,
  assignment_version int default 0,          -- offer 版本（CAS）
  total_deadline_at  timestamptz,            -- 总等待期
  offered_at / offer_expires_at timestamptz,
  assigned_agent_id  varchar,
  closed_reason      varchar,
  created_at/updated_at timestamptz
);  -- 部分唯一索引：每会话最多一个 active 工单 WHERE state != 'closed'

-- 派单流水
cs_assignment (
  id bigserial PK,
  handoff_id uuid, conversation_id uuid, agent_id varchar,
  tenant_id varchar,
  state varchar check in (offered, accepted, expired, declined, released),
  attempt_no int, offer_version int,
  offered_at / accepted_at / unassigned_at timestamptz,
  assigned_by varchar                           -- dispatcher / supervisor / 主管ID
);

-- 确认单（操作确认链核心）
cs_confirmation (
  confirmation_id uuid PK,
  conversation_id uuid not null,               -- FK，先有会话行再有确认行
  user_id, tenant_id varchar not null,
  action_type   varchar,                       -- refund/return/exchange/address...
  target_type, target_id varchar,              -- order/refund 单据
  semantic_fingerprint varchar,                -- 语义指纹（参与唯一索引）
  state varchar check in (pending, confirmed, executing, success, failed,
                          verifying, cancelled, expired),
  proposal      jsonb,                         -- 金额/原因/风险等级/展示文案
  retry_count   int default 0,
  expires_at    timestamptz,
  created_at/updated_at timestamptz
);  -- 部分唯一索引：(tenant,action_type,target_id,semantic_fingerprint)
    --   WHERE state IN ('pending','confirmed','executing','verifying')

-- 动作执行记录（幂等账本结果）
cs_action_record (
  action_id        uuid PK,
  confirmation_id  uuid not null,
  idempotency_key  varchar unique not null,    -- cs_action:{confirmation_id}
  action_type      varchar,
  status           varchar check in (running, success, failed, uncertain, compensated),
  result           jsonb,
  executed_at      timestamptz
);

-- 审计日志（append-only，无 update/delete 权限）
cs_audit_log (
  id bigserial PK,
  tenant_id varchar,
  conversation_id uuid,
  actor_type  varchar check in (user, ai, agent, system, admin),
  actor_id    varchar,
  action      varchar,        -- query.read / refund.execute / handoff.claim / ...
  target_type, target_id varchar,
  result      varchar check in (success, failure, denied, in_doubt),
  detail      jsonb,
  created_at  timestamptz
);  -- idx (tenant_id, created_at), (actor_id, created_at)

-- 坐席表
cs_agent (
  agent_id uuid PK,
  auth_user_id varchar not null,               -- 关联认证体系
  tenant_id varchar,
  skills text[] default '{general}',
  status varchar check in (offline, online, busy, away, wrapping_up),
  max_concurrent int default 5,
  last_assigned_at timestamptz,
  created_at/updated_at timestamptz
);

-- 事件 outbox
cs_event (
  id bigserial PK,                             -- 会话内游标（after_seq 回放）
  tenant_id, conversation_id varchar,
  event_id   varchar unique,                   -- 幂等
  type       varchar,                          -- conversation.offered / claimed / ...
  payload    jsonb,
  status     varchar check in (pending, sent) default 'pending',
  created_at / sent_at timestamptz
);

-- 用户长期记忆（L3）
cs_user_memory (
  id bigserial PK,
  tenant_id, user_id varchar not null,
  kind varchar,                                -- preference / case_summary / flag
  content text,
  embedding vector(1024),                      -- 可选向量检索
  created_at/updated_at timestamptz
);  -- unique (tenant_id, user_id, kind, content_hash)
```

## 10.2 数据生命周期

| 数据 | 保留策略 |
|---|---|
| cs_message | 热 6 个月，归档至对象存储（按月分区滚动） |
| cs_audit_log | 3 年（合规），append-only |
| cs_confirmation/action_record | 2 年（资损对账依据） |
| cs_event | sent 状态 30 天清理 |
| 检查点/checkpoint | 7 天 TTL 守护清理 |

---

# 十一、安全设计

## 11.1 身份链（全链路传递，不可跳过）

```
登录 → JWT（claims: user_id, tenant_id, roles, session_id）
  → APISIX 验签（Bearer 优先）→ 剥敏感头后注入可信身份头 X-User-Id / X-Tenant-Id
  → FastAPI request_context（ContextVar，请求级隔离）
  → 图 state（tenant/user/conversation 三元组进 state，随节点流转）
  → Tool 调用（内部服务 token + 租户头透传）
  → Java 侧二次校验（不信任网关之外的一切来源）
WebSocket：坐席走一次性 ticket 换发（WS scope 不走标准中间件，鉴权必须在握手显式做）
```

铁律：**业务代码禁止直接 os.getenv 取身份**；身份只能来自 request_context 链路；任何"内部接口"都必须过同一身份链（无匿名旁路）。

## 11.2 权限控制

| 风险 | 控制点 |
|---|---|
| 越权查询（查别人订单） | Gateway/Java 侧资源归属校验：`resource.owner == principal`；AI 参数视为不可信 |
| 越权操作（替别人退款） | 操作类 Tool 校验目标资源归属 + 确认链绑定 (user, session)；跨用户 target 直接 denied + 审计 |
| 坐席越权看会话 | 坐席只能访问 assigned 给自己的会话（assignment 实时校验），主管角色可全量 |
| 权限提升 | RBAC 四角色（user/agent/supervisor/admin）服务端判定；前端 AuthGate 只是体验层不是安全边界 |
| 提示注入 | 输入护栏（注入/越权指令模板拦截）+ 工具参数 Schema 严格校验 + 输出护栏（不回显系统 prompt/内部路径） |
| 匿名滥用 | 匿名限流独立配额 + 会话级命名空间 + 禁止写 L3 |

## 11.3 数据审计

- **三类必审事件**：敏感查询（支付/他人尝试）、全部写操作（含 denied/failed/in_doubt 终态）、人工接管全周期（入池/派单/接单/关闭）。
- **实现约束**：审计与业务变更**同事务**（不允许"先成功后补审计"）；append-only（DB 角色无 update/delete）；AI 动作与审计条目幂等绑定（重放不产生第二条）。
- **可查询性**：管理端按 conversation/actor/时间检索；审计进入 QA 日报（异常模式：同一用户高频 denied → 风控信号）。

---

# 十二、监控和指标

## 12.1 业务指标（北极星加护栏）

| 指标 | 定义 | 目标/告警 |
|---|---|---|
| AI 解决率 | 会话结束未转人工 ∧ 无 24h 内重复进线 ∧ 非 CAUTIOUS 拒答 | 基线后持续提升；突降 >5pt 告警 |
| 转人工率 | 三桶拆分：用户显式 / AI 建议 / 规则强制 | 总率基线 ±10% 告警；桶间漂移定位路由问题 |
| 首响时间 | 用户消息 → 首个可见 token | P95 < 3s |
| AI 独立解决时长 | 会话开始 → 结束（纯 AI 会话） | P50 < 30s |
| CSAT | 会话结束评分 | ≥4.5；差评自动进质检抽样 |
| 确认转化率 | proposal → 确认成功 / proposal 总数 | 异常低 = 确认卡文案或资格判定有问题 |
| 拒答率 | 知识域拒答轮次占比 | >15% 告警 = 知识库缺口（feed 知识运营） |
| 质检抽检 | 每日分层抽样 AI 预评 + 人工复核；预评一致率 | 一致率 <90% 告警 = 预评模型需重训（§9.1.2⑧） |
| 介入点分布 | 9 类人工介入点各自的触发量与占比 | 显式占比过高 = AI 能力缺口；异常类（⑥⑦）>0 即需归因 |
| 未解决问题池 | 拒答/失败会话原因聚类 Top-N 与趋势（§9.5.1） | 同一原因连续 3 天上升 → 运营工单化 |

## 12.2 技术指标

| 指标 | 说明 | 告警 |
|---|---|---|
| Agent 耗时 | 各专家/Supervisor 耗时直方图（分位） | P99 超 SLO 告警 |
| Tool 失败率/超时率 | 按 Tool 维度 | >5% 告警；熔断事件即告警 |
| LLM 指标 | token 消耗/成本、限流率、超时率、按域分摊 | 成本日环比 +30% 告警 |
| 派单健康 | 排队等待时长、offer 接受率、reaper 回收量 | 等待 P95 >30s 告警 |
| dispatcher 心跳 | 30s TTL | 心跳中断即告警 |
| outbox lag | pending 事件积压时长 | >30s 告警 |
| SSE/WS 健康 | 断流率、重连率、回放成功率 | 断流率 >1% 告警 |
| fallback 率 | 降级话术占比 | >2% 告警 |

## 12.3 Trace 设计

- 每 user turn 一条 trace，`conversation_id` 为贯穿主键；`session_id = thread_id`（任务与 会话 trace 可关联）。
- 关键 tags：`cs_expert_final`（最终专家）、`cs_expert_visited`（访问序列）、`cs_handoff_state`、`cs_variant`（灰度组别）、`decision_layer`。
- 关键 metadata：意图路由明细、确认动作 ID、派单耗时分解。
- 用途：路由一致率评估（tags.cs_target vs cs_expert_final 对照）、转人工归因、评测集回流（bad case 一键入集）。

---

# 十三、实施路线

> 原则：**每阶段独立可验收、可回滚、有价值交付**；评估集先行（Phase 1 就建，之后每阶段扩充）；写操作链路在接入真实资金前必须完成故障演练。

## Phase 1：基础客服（知识问答 + 骨架）— 4~6 周

- **目标**：AI 能安全地回答政策/FAQ，答不了就体面地拒答/转人工建议。
- **模块**：会话管理层（conversation/message 落库）· CS Graph 骨架（loader/context_manager/supervisor/reporter）· Knowledge Agent + RAG 链路 · 安全护栏（输入/输出）· 身份链 · trace/指标基线 · 评估集 v1（≥100 条）。
- **验收标准**：标注集（500 条人工标注）FAQ 准确率 ≥80%；拒答率 10~15%（宁缺勿错）；攻击样本拦截率 100%（≥100 条注入/越权样本）；首响 P95 <5s；域图故障不影响主链路（混沌验证）。

## Phase 2：业务查询 — 3~4 周

- **目标**：AI 能查真实订单/物流/工单，多轮回指可用。
- **模块**：Business Gateway（认证/归属校验/缓存）· Query Agent + 查询类 Tool · 实体抽取/槽位/回指 · 复合问题分解。
- **验收标准**：查询准确率 ≥95%（含归属反向用例：查他人订单必须 denied）；Gateway P99 <2s；多轮回指成功率 ≥90%；复合问题分解成功率 ≥85%；Gateway 故障演练（断网 5min，话术正确、无脏数据）。

## Phase 3：业务操作（确认链）— 4~6 周

- **目标**：退款/退货/改地址在确认链保护下真实执行。
- **模块**：Action Agent + 操作类 Tool · 确认链全链路（proposal/原子认领/durable ledger/VERIFYING 裁决）· 审批门 · 审计 · 运维 CLI。
- **验收标准**：20 并发确认 1 胜 19 败；同语义并发提案唯一索引决胜；ledger 重放不双执行（混沌重试验证）；UNKNOWN 恢复演练闭环（杀进程→裁决→收敛）；审计完整率 100%（每个动作可溯源到会话与用户）；风控联签（大额复核路径可用）。

## Phase 4：人工客服 — 4~6 周

- **目标**：人机协同闭环：排队、派单、接管、回 AI。
- **模块**：Handoff Agent + dispatch worker（reaper/dispatch/relay）· WS Hub + outbox · 坐席工作台 · 案件系统（cs_case）· 质检报表 v1。
- **验收标准**：派单 P95 <5s；100 并发接单 1 成功 99 个 409；WS 经网关 100 次断线重连 0 丢消息（outbox 回放验证）；AI 零抢答（接管期全量用例）；总等待期超时兜底可用（留言恢复 AI）；dispatcher 双副本故障切换 <2s。

## Phase 5：智能优化（持续）— 按季度迭代

- **目标**：从"能用"到"好用"：提解决率、降成本、赋能坐席。
- **模块**：情绪/风险模型接入决策（P0/P1/P2 SLA 自动化）· 坐席 AI 辅助（建议回复/摘要/情绪提示）· 知识库运营闭环（拒答 → 补知识 → 评测）· 主动服务（物流异常主动通知）· 质检 AI 化 · 成本优化（小模型分流/缓存策略）。
- **验收标准**：AI 解决率相对 Phase 4 基线 +10pt；坐席平均处理时长 -20%；LLM 成本/会话 -30%；每项优化有 A/B（灰度桶）数据支撑。

---

# 十四、从现有项目迁移到该架构

> 结论先行：现有实现的核心模式（确认链三防线、派单锁序、outbox、prefilter 双入口、run_expert_safely）与目标架构**高度一致**，迁移不是重写，而是「契约收口 + 局部重构 + 清偿欠账」。**API 不变、前端不变、数据表不大改**，在现有进程内做结构演进，分三阶段，每阶段结束都是可上线的完整形态。

## 14.1 现状 → 目标映射（处置清单）

### 直接保留（已达标，冻结不动）

| 现有资产 | 对应目标架构 | 说明 |
|---|---|---|
| prefilter 双入口（域锁 + 全局 + 显式转人工直通 + 接管期强制接管） | §1.5 / §4.7 | 语义正确且经事故验证；守护测试保留 |
| 确认链三防线（051 partial unique / 原子认领 / durable ledger） | §4.5 | Phase3 STOP D/E 冻结语义原样保留 |
| VERIFYING + 人工裁决 CLI | §4.5 | 已有双环境测试矩阵 |
| dispatch 锁序 + SKIP LOCKED + reaper + outbox + presence fail-closed | §9.2/§9.3 | 系统中最健壮的部分，P4-P9 全链验收过 |
| run_expert_safely 超时壳（线程级限时） | §8.1 | 保留并推广到全部专家 |
| 评估集 320 条锁版 + 932 条测试 + 攻击门禁 100 条 | §13 验收基线 | 直接作为迁移回归门 |
| CSUnderstanding 实体/槽位层 | §4.4/场景4 | 补齐决策消费即可 |

### 重构（语义保留，实现收敛）

| 现状问题 | 目标做法 | 工作量 |
|---|---|---|
| 规则词表 5+ 处重复、无热更新 | 收敛为单一词表源（capabilities 清单同级），管理端可热更 + 变更审计 | 中 |
| 置信度语义靠补丁贴平（0.6 下限硬贴） | 重做置信度模型：coarse/fine 分数语义化 + 校准集定标，去掉互相耦合的下限补丁 | 中 |
| complaint LLM 兜底用无效 timeout 参数（无界挂起） | 换 sync_call_with_timeout + 补 complaint/handoff/query 节点限时（**可立即修，几行**） | 小 |
| understanding 信号无决策消费者 | 情绪/风险接入 Supervisor L1/L2（P0 投诉直通转人工、风险拦截），落 SLA 分级 | 中 |
| 专家节点样板代码 5 份重复 | 抽 base 类：统一 state 读写/审计/超时/指标管道 | 中 |
| 审计落库在请求线程同步执行 | 改异步旁路（outbox 化），同事务保证不变 | 小 |
| 评测 CLI choices 未列 cs | 补全 + 接入 CI 门禁常驻 | 小 |

### 废弃 / 替换（欠账清偿）

| 现状 | 目标 | 阻塞项 |
|---|---|---|
| `simulate_execute` 模拟执行（Phase 6 欠账） | Java 真实执行器接入 | **前置：Gateway 订单明细端点、http 模式退款查重端点**（现有网关契约缺口） |
| 物流轨迹 mock（DEMO-1001 硬编码） | Gateway 聚合真实承运商（logistics.query Tool） | 承运商 API 选型 |
| 追问示例单号 MO-1002/DEMO-* 不一致 | 统一演示数据 seed + 文案 | 小 |
| RETURN_WINDOW_DAYS 定义未使用 | 资格检查真实消费窗口常量 | 小 |
| SSE 无断线恢复协议 | done 帧补 seq + `after_seq` 回放（复用 WS 侧已有契约） | 中 |
| knowledge 置信度兜底值放行 | META 兜底收紧为 REFUSE + 兜底值监控 | 小 |

## 14.2 迁移路线（三阶段）

### Phase 1（最小上线，2~3 周）：旧图上做三件事，对外契约零变更

保持 `/chat/stream`、SSE 帧、现有表结构完全不变，在旧 CS Graph 内：

1. **增加 Context Manager**（§4.1）：收敛 action/query/complaint 三处重复的订单号/身份解析，作为纯函数组件插在 state_loader 之后——行为等价重构，以 932 条测试 + 320 评估集为回归门；
2. **Supervisor 重构**（§4.2）：判定优先级固化（人工状态→pending→风险→循环→路由→LLM 兜底），补 complaint/handoff/query 节点限时（无界挂起修复，几行改动），置信度重做（从评测集抽 200 条定标，去掉下限补丁）——灰度方式：新旧决策层按会话哈希双跑对比，route 一致率 ≥95% 才切流（回滚开关 `CS_DECISION_V2=false` 一键回旧）；
3. **统一 Expert 接口**：5 专家节点收敛到 base 管道（state 读写/审计/超时/指标样板抽象），understanding 信号接入决策（情绪/风险消费），为 Phase 2 目录拆分铺路。

同批清偿零风险欠账：评测 CLI choices 补 cs、MO/DEMO 文案统一、审计落库异步化、词表去重（不改行为）。

### Phase 2（结构拆分，2~3 周）：目录与模块边界

```
customer_service/
├── domain/        # 域图构建、state 契约、路由映射
├── context/       # Context Manager、understanding、context_resolver
├── experts/       # knowledge.py / query.py / action.py / complaint.py / handoff.py
├── handoff/       # 转人工状态机 + dispatch（现 dispatch/ 整体迁入）
├── case/          # 统一案件（cs_case）：创建/流转/查询，工单/投诉/售后收编
├── quality/       # 质检抽检、QA 日报、bad case 回流
└── tools/         # 客服域 Tool 薄封装（业务实现仍在 services/，只做契约收口）
```

边界规则：experts 之间禁止互相 import；**case 是唯一案件事实源**（cs_ticket 并入 cs_case）；handoff 只管接管、不管案件结论。本阶段对外 API 与存量表结构仍不变（cs_case 以并存+视图方式过渡）。

### Phase 3（运营层，3~4 周）：把闭环建起来

- **客服运营后台**：未解决问题池（§9.5.1）、案件工作台、词表/快捷入口热更；
- **质检**：分层抽样 + AI 预评 + 人工复核队列（§9.1.2⑧），预评一致率进监控；
- **Copilot 增强**：接单卡（已完成清单 + 待人工动作，§9.1.2⑨）、推荐回复扩展；
- **知识运营闭环**：拒答聚类 → 知识运营待办 → 评测集回流（§9.5.2）；
- **状态与会话收尾**：SSE 断线恢复协议（复用 after_seq 契约）、长会话摘要后台化、L3 记忆白名单、双 PG 遗留清理（5433 唯一权威）。

**真实执行器**（模拟执行欠账）与资金路径不塞进本三阶段：Tool 契约收口、Gateway 明细/查重端点补齐、真实执行器影子双跑（真实调用不落账 → 对账一致 → 放量）按 14.3 纪律独立排期推进。

## 14.3 迁移纪律

1. **契约冻结**：迁移期间确认链 / dispatch / identity-header 三契约逐字冻结，任何变更走版本化；
2. **每批次独立可回滚**：行为开关 + 灰度桶，双跑对比评测（CS 320 条 + 线上影子流量）达标才切流；
3. **资金路径零信任**：真实执行器上线前完成 UNKNOWN 演练 + 对账报表 + 运维 Runbook，缺一不放量；
4. **并行会话协作纪律**：迁移涉及多模块，严格 pathspec 提交、开工前 git status 核对（项目既有纪律）。

---

## 附：本文与《客服域现状评估》的关系

- 现状评估（`docs/reports/2026-09-29-客服域现状评估-确认链竞态-dispatcher事务-评测指南.md`）回答"现在是什么样"；
- 本方案回答"目标应该是什么样 + 怎么过去"。两份文档配合使用：评估报告是迁移章节的依据，本方案的 Phase 1-5 验收标准可直接引用评估报告中的既有资产作为基线。

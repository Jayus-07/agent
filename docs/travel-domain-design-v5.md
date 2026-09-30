# 旅游规划域设计书 v5（自包含定稿 · 架构 + 治理 + 产品面）

> **文档定位**：本文档是旅游规划域的**现行唯一设计书**。它合并承接
> `travel-domain-agent-runtime-design.md`（v3，7-Agent 架构与 Phase 执行卡）与
> `travel-domain-agent-runtime-design-v4.md`（v4，企业级治理层冻结），并**新增 v1–v4 全部缺失的「产品面」层**。
> v3/v4 转为历史留档，不再单独更新；执行一律以本文为准。
>
> **冻结声明（重要）**：v3/v4 已冻结的架构决策与契约**一条不改**——7 Agent 边界、域图节点拓扑、
> Evidence 五字段模型、Human Approval 统一模型、ToolSpec 四级 side_effect、ProviderRouter 选择链、
> §9 全部指标口径，全部原样承接。本文是**结构重组 + 补一层**，不是推翻重写。
>
> **四优先级不变**：稳定性 > Agent 数量；确定性 > 模型自由生成；数据真实性 > 内容丰富；不重复实现既有生产资产。
>
> **硬约束不变**：validator / repair / provider 七态契约 / booking 状态机 / checkpoint 跨轮契约 / 测试基线
> **零删改核心逻辑**，只允许加法式扩展。
>
> **基线**：`docs/travel-domain-current-audit.md`（文件/行号事实源）+ 本文 §18「真基线差距」（2026-09-30 实测）。

---

## 0. 本次重构做了什么（一页看懂）

| 动作 | 说明 |
|---|---|
| **合并** | v3（架构+执行卡）与 v4（治理层）合并为一份自包含文档，终结「v3+v4 组合读」的文档债 |
| **补层** | 新增第一部分「产品面」——v1–v4 四份设计稿全部是后端架构与治理，**没有一份讲「用户看到什么」** |
| **对照** | 新增 §16「参考项目 TripStar 采纳清单」：6 项采纳（附落点与阶段）、7 项明确不采纳（附理由） |
| **增阶** | 建议新增 **Phase 9 产品面收口**；Phase 5–8 原计划不变，仅内部注入产品面对齐项 |
| **纠正** | §9 如实记录真基线（图拓扑仍是旧 10 节点、Agent 类已建但图节点未切换），避免照设计稿误判进度 |

**读者路线**：

| 你是谁 | 读哪几节 |
|---|---|
| 产品/验收方 | §1–§8（产品面）→ §16（采纳清单）→ §21（验收标准） |
| 施工方 | §9–§15（架构）→ §18（差距）→ §19（路线） |
| 外部评审 | §0 → §1.2 → §16 → §18 |

---

# 第一部分 · 产品面（v5 新增）

## 1. 产品定义

### 1.1 一句话

给个人旅行者（含自由行、家庭、情侣）的**行程排布助手**：用户说清去哪、几天、什么偏好，
系统给出一份**可执行、可编辑、可导出、每句话有出处**的逐日行程，并在行程完成后继续回答关于它的问题。

**不做的事**（边界，与 v3/v4 一致）：不做内容社区、不做攻略长文、不做图片墙、不做社交。

### 1.2 体验四标准（把工程纪律翻译成用户能感知的话）

工程侧的四优先级（稳/诚/可控/好养）必须落到用户能感知的四个体验上，否则纪律等于自嗨：

| 体验标准 | 用户感知 | 工程支撑 |
|---|---|---|
| **过程可见** | 「我知道它在干嘛，转圈不是卡死」 | 阶段进度事件（§3） |
| **结果可读** | 「一眼看懂每天干什么、花多少钱、路线顺不顺」 | 多视图组织（§4） |
| **追问可答** | 「我能接着问『第二天能不能换』」 | 伴游问答 + 跨轮上下文（§5） |
| **承诺可信** | 「它说确定的就是确定的，说待核实的就明说」 | Evidence 三档渲染（§13） |

**第四项是本项目的差异化护城河**：TripStar 一类产品做得比我们花哨，但没有一家敢把
「这条信息的来源与核实时点」摆给用户看。这是 Phase 4 投入的成果，必须在产品面上兑现，否则白做。

### 1.3 三条用户入口（2026-09-30 实测）

| 入口 | 路径 | 形态 | 实测状态 |
|---|---|---|---|
| **表单页** | `/travel` | 结构化表单 → 结果页 | ⚠️ **站内导航无入口**（`navConfig.tsx` 只有 `/agent`），用户必须手输网址 |
| **对话** | `/agent` | 自然语言 → 五道闸门分流 | ✅ 可用（"福州 3 天行程"） |
| **伴游问答** | 待建（§5） | 行程生成后围绕该行程追问 | ❌ 未实现（Phase 6） |

**入口缺口是当前最高性价比的一处修复**：页面功能完整但没有导航项，等于没做。

---

## 2. 用户可见链路总览

```
                    ┌──────────── 入口层 ────────────┐
   /travel 表单 ──┐                                ┌── /agent 对话
                  ├──→ 规划请求 ──→ 域图执行 ──→ 结果呈现 ──→ 伴游问答（追问）
   导航入口 ──────┘        │                        │
                           │                        ├─ 概览视图（地图+预算+要点）
                     进度事件流（§3）                ├─ 每日视图（逐日时间轴）
                     「正在检索景点…」               ├─ 依据视图（来源+时效+待核实）★本项目独有
                                                    └─ 导出（ICS / 文本）
```

三个关键设计决定：

1. **进度走 SSE，不走轮询**。TripStar 用 `task_id` + 每 3 秒轮询解决 504（它自己 README 写了这是为了绕开同步等待）。本项目**已有 SSE v2 基础设施**（`event_schema.py:31` 定义 `meta/status/log/delta/done/error`，前端有 `sse-parser.ts`），比轮询先进一档。**采纳它的「过程可见」语义，不采纳它的轮询实现**。
2. **结果分视图而非单页堆叠**（§4）。现状是单页从上到下堆：预算 → 地图 → 逐日 → 警告 → 折叠文本。
3. **依据视图是新增的第三个视图**，把 Evidence 三档直接摆给用户。这是 TripStar 没有、我们独有的。

---

## 3. 规划过程可见性（采纳 TripStar #1）

### 3.1 问题

`/travel` 目前是同步 `POST /api/travel/plan`，`loading` 只有一行字
「规划中（真实路况与天气计算，约数秒）…」（`frontend/src/app/travel/page.tsx:424`）。
而域图整次规划有 **30s deadline**（v4 §9.3），期间用户面对的是无信息转圈。

### 3.2 设计：把已有 stage 事实外发成进度事件

**关键前提：阶段事实已经存在，不需要新建。** `supervisor.py` 的 `TravelStage` 枚举就是执行阶段，
`node_span.py` 已经在做节点级 trace。本项只是**多一条对外通道**，零规划逻辑改动。

**阶段 → 用户文案映射**（对齐 TripStar 的四步呈现，但比它细）：

| stage | 用户看到 | 对应节点 |
|---|---|---|
| requirement | 正在理解你的需求… | `travel_slot_filler` |
| research | 正在检索景点… | `travel_poi_expert` |
| planning+optimization | 正在排路线与时刻… | `travel_transit_expert` |
| optimization（天气） | 正在核对天气… | `travel_weather_expert` |
| optimization（预算） | 正在核算费用… | `travel_budget_expert` |
| validate / repair | 正在校验行程…（超出预期时会显示"重新调整中"） | `travel_validator` / `travel_repair` |
| report | 正在生成行程单… | `travel_reporter` |

**落点**：

| 层 | 文件 | 改动 |
|---|---|---|
| 后端 | `backend/app/api/routes/travel.py` | 新增流式端点（如 `POST /api/travel/plan?stream=1`），产出 `status` 事件；非流式路径保持原样（向后兼容） |
| 后端 | `backend/travel/supervisor.py` 或复用 `node_span.py` | stage 推进时 emit `status` 事件（**只加发送，不改 decide()**） |
| 前端 | `frontend/src/app/travel/page.tsx` | 消费 `parseSSEStream`，渲染阶段进度条 |
| 前端 | 新增 `components/travel/PlanProgress.tsx` | 进度条组件 |

**纪律**：`status` 事件只描述**已发生的事实**（"正在检索景点"= 该节点已在执行），
不做预估百分比。**不给假进度条**——这与本项目「不编造」的底线一致。

---

## 4. 结果多视图组织（采纳 TripStar #2 + 本项目独有视图）

### 4.1 现状

`/travel` 单页堆叠：标题+版本 → 导出/反馈按钮 → 预算五分项 → 静态地图 → 逐日时间轴 →
「需要你确认」警告 → 折叠文本行程单。

### 4.2 目标：三视图

| 视图 | 内容 | 来源 |
|---|---|---|
| **概览** | 地图打点（起-景-终连线）+ 预算面板 + 行程要点（天数/城市/预估总时长） | 现状已有元素，重组 |
| **每日行程** | 逐日时间轴（现状 `DayTimeline` 组件）+ 每日通勤/费用小计 | 现状已有 |
| **依据与提醒** ★ | 数据可信分档清单（可信/可能变化/待核实）+ 预约提醒 + 校验警告 + 版本变更记录 | **新增**，消费 Evidence（§13） |

### 4.3 「依据视图」为什么是重点

TripStar 有「知识图谱」视图——本质是把行程 JSON 转成节点/边的**可视化包装**，没有推理价值（§16.2 不采纳）。
本项目的第三个视图换成**依据视图**，解决的是真实问题：

- 用户问「这个门票价格准吗」→ 视图直接回答：来源 `腾讯位置服务`、核实时点 `今天 10:23`、**营业时间与票价为示例值，出发前请核实**
- 这直接把 Phase 4 的 Evidence 成果变成用户价值，而不是只躺在后端

**落点**：`frontend/src/app/travel/page.tsx` 结果区改为 tab 容器；新增
`components/travel/EvidencePanel.tsx`；后端需在 plan 响应里透出 `evidences`（`graph_state.py` 已声明该字段）。

---

## 5. 伴游问答（采纳 TripStar #3，Phase 6 形态细化）

### 5.1 参考形态

TripStar：行程生成后左下角**悬浮 AI 问答窗**，拥有完整行程上下文，并给出**预设问题引导**
（"需要安检？""适合老人？""餐饮推荐"）。

这个形态对本项目适用，且本项目 Phase 6 的 `Travel Assistant Agent`（v3 §3.1 ⑦）就是它的后端。

### 5.2 本项目增强的三点

| 维度 | TripStar | 本项目（Phase 6 契约不变） |
|---|---|---|
| 回答依据 | 行程 JSON + LLM 自由回答 | **只准引用 state / Evidence 中的事实**，无数据如实说"没有这个信息"（v3 §3.1 ⑦ 已冻结） |
| 引用标注 | 无 | 回答附 `fact_id` 引用（v4 §9.1 评测口径，hallucination 零容忍） |
| 预设问题 | 通用问题 | **只给"证据可答"的问题**——"第 2 天几点开始""鼓浪屿门票多少""那天下雨吗""这天要走路多久"；**不给**"附近哪家餐厅好吃"这类无数据支撑的问题 |

**落点**：`backend/travel/agents/assistant_agent.py`（Phase 6 新建）+ 前端
`components/travel/TripAssistant.tsx`（悬浮窗）。

---

## 6. 预约与时效提醒（采纳 TripStar #4，替换数据来源）

### 6.1 参考做法与问题

TripStar 从**小红书游记**里识别"需提前预约的景点"（故宫、陕历博），标进卡片防止白跑。
这个需求是真实的（国内景区预约制确实是高频坑点），但**它的来源不能照抄**（§16.2：爬虫三宗罪）。

### 6.2 本项目做法：走知识层，不走爬虫

预约规则进**知识库（RAG）条目**，由 Research Agent 的 `knowledge_notes` 承接（v3 已有该职责）：

- 每条预约提示必须带**出处**与**复查时点**，作为 Evidence（`source_type=RAG`，confidence 0.7）
- 渲染在「依据与提醒」视图，而不是混进行程卡片正文
- **没有收录的景区就不显示**——不猜、不编（"故宫可能要预约"这种模糊提醒等于没提醒）

**落点**：知识库内容建设（数据侧，属 §20 待办）+ `services/knowledge_notes.py` 增加预约类条目抽取（Phase 6）。
**注意**：这条的**瓶颈在知识库里有没有内容，不在代码**——必须如实标注，不能假装实现。

---

## 7. 偏好记忆增强（采纳 TripStar #5，低优先级）

### 7.1 参考做法

TripStar：分权重偏好库 + 遗忘机制 + TOP-K 召回，行程成功后自动提取稳定偏好入库。

### 7.2 本项目现状与增量

现状已有 `travel/memory/preferences.py`（含负偏好 avoid）。增量建议：

- **权重**：偏好按出现频次+最近使用加权，避免一次性的偏好永久生效
- **衰减**：超过 N 个月未命中的偏好降权（模仿遗忘）
- **TOP-K 注入**：只把权重前 K 条注入 Requirement Agent，避免 prompt 膨胀

**优先级：低。** 现状的偏好存取已经可用，本项是体验精修，建议放 Phase 8 之后评估，不占 Phase 5–9 的预算。

---

## 8. 导出与分享

| 能力 | 现状 | 说明 |
|---|---|---|
| ICS 日历导出 | ✅ 已有（D1 修复后支持中文） | 保留 |
| 文本行程单 | ✅ 已有（结果区折叠区） | 保留，用于"复制发同行人" |
| 地图链接 | 契约位（`map.link`，v3 §4） | Phase 6 接线 |
| PDF 导出 | 暂缓（需渲染依赖评审） | 不变 |
| 图片导出 | **不采纳**（TripStar 有） | 长图导出对"排行程"核心决策无增益，且引入渲染依赖 |

---

# 第二部分 · 架构面（承 v3/v4，自包含重述）

> 本节是 v3/v4 的**压缩自包含重述**，供不翻阅历史文档的读者使用。**契约以 v3/v4 原文为准，本文如有出入以原文为准。**

## 9. 域图拓扑：真基线 vs 目标

### 9.1 真基线（2026-09-30 实测，非设计稿）

**现状仍是旧 10 节点**（`backend/travel/graph_builder.py` 实测）：

```
START → travel_slot_filler → travel_supervisor
          ├─ travel_poi_expert ─┐
          ├─ travel_transit_expert
          ├─ travel_weather_expert   ├→ travel_supervisor（循环）
          ├─ travel_budget_expert    │
          ├─ travel_risk_expert      │
          └─ travel_validator ───────┘
             travel_repair → travel_supervisor
travel_supervisor → travel_reporter → END
```

**重要事实：`backend/travel/agents/` 下 4 个 Agent 类已建并已接线，但接线方式是"专家节点内部委托"，不是"图节点替换"。**

| 事实 | 证据 |
|---|---|
| `agents/requirement_agent.py` / `research_agent.py` / `planning_agent.py` / `optimization_agent.py` 已存在 | `find backend/travel -name "*_agent.py"` |
| 它们被旧专家节点内部调用 | `experts/poi.py:20-21` import `PlanningAgent`/`ResearchAgent`；`experts/transit.py:29` import `OptimizationAgent` |
| 图节点名仍是 `travel_poi_expert` 等旧名 | `graph_builder.py` 的 `add_node` 调用 |

**结论**：Phase 3 走的是「**专家壳内包 Agent**」的渐进路线（保图拓扑零变动），
**图节点名切换（10 节点 → v3 §5.1 的节点表）尚未发生**。这是设计稿与真基线的**唯一重大差异**，
必须在 §18 登记，避免后续误判"7-Agent 架构已落地"。

### 9.2 目标拓扑（承 v3 §5，冻结）

节点表与拓扑见 v3 §5.1/§5.2，**冻结不变**。要点：

- Agent 层 7 个：① Supervisor ② Requirement ③ Research ④ Planning ⑤ Optimization ⑥ Commerce ⑦ Assistant
- validator / repair / reporter 是**门禁与渲染基础设施节点**，不算 Agent、不改名不搬逻辑
- 图节点总数 **10 → 10**（五专家收敛为 research/planning/optimization，新增 assistant/commerce）
- `budget/risk` 的 Send 并行是**后续增强位**，本阶段不做（`graph_builder.py` 头注释已说明理由：数据依赖严格，并行收益有限）

### 9.3 切换时机（v5 建议）

**建议：图节点名切换与 Phase 5 合并，或独立成 Phase 8 前的一个小批次。**

理由：现在"Agent 类已建 + 图节点未切"是一个**过渡态**，长期挂着会让
「遥测 `expert_history` / Grafana / 评测 runner」三处口径长期分叉（v3 R1 风险）。
切换成本很低（`graph_builder.py` 十几行 + `agent_history` 双写），**留到 Phase 8 大扫除会掺进死代码清理，风险反而更高**。

---

## 10. 7 Agent 职责边界（承 v3 §3，冻结）

| # | Agent | 职责 | LLM 策略 | 超时/降级 |
|---|---|---|---|---|
| ① | Travel Supervisor | 意图判定（PLAN/MODIFY/QUERY/BOOK/CANCEL）、stage 推进、deadline 检查 | **无**（规则化） | 整图 30s，超限强制 REPORT |
| ② | Requirement | 自然语言 → TripBrief，13 槽 + adults/children + 节奏 + 偏好；缺失追问；偏好预填 | 规则层兜底 + LLM 结构化（flag off，≤1 次/run，零重试） | 10s；LLM 失败→规则+追问 |
| ③ | Research | 城市归一/推荐；候选池（≤60）；天气；知识/风险摘录；**产出附加 Evidence** | **无** | 15s；空池如实说明 |
| ④ | Planning | must_go 三态解析；日程骨架分配 | **无** | 10s |
| ⑤ | Optimization | 地理排序（最近邻，2-opt 为增强位）；时刻排程；坏天气换点（必去永不换）；四分项预算；版本盖章 | **无** | 15s；失败不穿透 |
| ⑥ | Commerce | 交易唯一入口：比价/深链 + 下单/取消/确认；Hotel/Flight/Ticket = kind 参数 | **无** | fail-closed，UNKNOWN→IN_DOUBT |
| ⑦ | Travel Assistant | 行程问答；**只引事实，无数据明说** | 模板优先；LLM 润色 flag off | 8s |

**统一契约**（`core/agent_base.py`，已建 86 行）：子 Agent 互相禁止调用；数据只走共享 state；
`Supervisor → Agent → Tool/Service → Provider` 单向。

**记忆（横向，非 Agent）**：`travel/memory/` = preferences + checkpoint 执行态 + 会话摘要。软失败纪律：读写失败绝不挡主流程。

---

## 11. 门禁层与横向能力（承 v3，冻结）

- **`travel_validator`**：六轴（原样）+ 3 项 warning（`POI_UNVERIFIED` / `SOURCE_STALE` / `PREFERENCE_VIOLATION`，Phase 4 已加）。
  **零 LLM 零 IO** 红线不变；warning 走既有 -0.05/条计分。
- **`travel_repair`**：零改动，≤2 轮 + 防震荡（`repair_stalled`）。
- **`travel_reporter`**：渲染层；v5 增量 = 按 Evidence 三档渲染（§13.3）。

---

## 12. Tool 清单（承 v3 §4，冻结 + v5 标注）

| Tool | 状态 | v5 备注 |
|---|---|---|
| `travel.search_poi` | 已有冻结 | — |
| `travel.poi.detail` | 已有（Phase 4） | — |
| `travel.weather.query` | 已有 | — |
| `travel.calculate_route` | 已有冻结 | — |
| `route.optimizer` | 接口已立（2-opt 增强位） | 金标不达标再立项 |
| `travel.calculate_budget` | 已有冻结 | — |
| `travel.retrieve_knowledge` | 已有 | §6 预约提醒的取数入口 |
| `travel.memory.search/save` | 已有 | §7 权重增强的落点 |
| `calendar.export`（ICS） | 已有（D1 修复后） | — |
| `map.link` | **契约位** | Phase 6 接线 |
| `travel.currency.exchange` / `travel.train.search` / `travel.ticket.*` / event / notice | **契约位 only** | `implemented=False` 如实披露；BLOCKED 台账 |
| **`travel.progress`（v5 新增建议）** | 新增 | §3 阶段进度事件出口（见 §19.3 落点） |

**Tool 治理**（`tools/spec.py`，已建 220 行）：四级 side_effect（READ/COMPUTE/WRITE/TRANSACTION）+
Agent→Tool 白名单 + TRANSACTION 只经 Commerce。`travel.progress` 属 **READ** 级。

---

## 13. 数据可信模型（承 v4 §4，自包含重述）

### 13.1 Evidence 五字段（`core/contracts.py`，模型唯一归属）

```json
{
  "fact_id": "poi_0031",
  "value": { },
  "source": "tencent_lbs | qweather | seed | rag:doc-xxx | estimator:v1",
  "source_type": "LIVE | RAG | CACHE | SEED | ESTIMATE",
  "confidence": 0.92,
  "verified_at": "2026-09-30T10:00:00+08:00",
  "expire_at": "2026-09-30T10:30:00+08:00"
}
```

**confidence 基线**：LIVE=0.95 / CACHE=0.85（stale 降 0.6）/ RAG=0.7 / SEED=0.5 / ESTIMATE=0.5；
**缺 `verified_at` 的事实 confidence 上限 0.5**。

### 13.2 落地状态（Phase 4 已完成）

`core/evidence_utils.py`（89 行，转换薄层）+ 三个组装点
（`poi_service.build_candidate_evidences` / `weather_service.fetch_forecast_evidence` /
`risk_service.build_knowledge_evidence`）+ 26 例契约测试。
**Provider 层零改动**（Evidence 在域层组装）。

### 13.3 消费规则（唯一口径，reporter 与 assistant 共用）

| 档位 | 条件 | 文案口径 |
|---|---|---|
| **可信** | LIVE 且未过期 | 正常陈述 |
| **可能变化** | CACHE / stale | "可能有变化，出行前请核实" |
| **需要确认** | SEED / ESTIMATE / confidence<0.6 | 显式标注"示例值/估算值" |

**v5 增量**：这套口径必须**渲染到「依据视图」**（§4.3），不能只停在后端。
「诚」是产品承诺，不是内部实现细节。

### 13.4 一个必须记住的坑（防施工踩）

`poi_service` 按「source 是否以 seed 开头」分派置信度：seed → 0.5；**否则一律 0.95 LIVE 并带 `verified_at`**。
所以做 POI 合并（腾讯 + seed）时，**合并后的 source 绝不能不带 seed 标记**，否则腾讯的**占位营业时间**
会被 0.95 的信用担保——等于拿权威坐标的信誉给编造的营业时间背书，直接把「诚」拆了。

---

## 14. 交易与审批（承 v4 §5/§6，自包含重述）

- **ToolSpec 四级**：READ / COMPUTE / WRITE / TRANSACTION；`requires_confirmation=True` 的 Tool 必须先有审批单。
- **Human Approval 统一结构**（`pending_approval` in TravelSessionState）：
  类型 `PLAN_CHANGE | MUST_GO_CONFLICT | BUDGET_EXCEED | BOOKING | REFUND`，含 options + expires_at。
- **BOOKING 复用现有确认链**（cfp 绑定 / TTL / 金额互检，冻结不动），本模型只是它的统一外壳。
- **裁决顺序**：`approval_id` 优先 → 现有 `cancel > avoid_patch > 补槽 > new_run`。
- **D2 修复**（Phase 5）：`booking_intent` 挂起槽并入 `pending_approval`，**一处机制两用**。

---

## 15. 可靠性设计（承 v4 §8/§9，自包含重述）

**ProviderRouter 链**（Phase 4 已建 `providers/travel/live/router.py`，四链声明 + 装配 parity 9 例测试通过）：

```
weather:  [tencent, qweather]  → stale cache → 七态失败
maps:     [tencent_live, local_estimate]
poi:      [seed]（唯一源；城市级 live 源 = 追加链头，契约位已留）
price:    [fake_commerce]（真实供应商 BLOCKED）
```

规则：**primary → fallback → stale cache（stale-if-error）→ 七态失败**；每次降级记 Evidence
（source_type 降级、confidence 同步降）。**Agent 不选 Provider**，只声明需要什么 capability。

**预算**：一次规划 LLM ≤1–2 次（Requirement ≤1 + Assistant ≤1）；整图 **30s deadline**；
单 Tool 3–5s；单 Agent 10–15s；**审批等待不计入 30s**（WAITING_CONFIRMATION 是跨轮状态）。

**TTL（Phase 4 调整后）**：POI/place 86400s(24h) / route 1800s(30min) / weather 600s(10min)。

**为什么本项目在可靠性上已优于参考项目**：TripStar 是「Google 失败降高德」的两级简单回退；
本项目是**七态失败 + Freshness + stale-if-error + 每级降级记证据 + 配额 + 缓存 TTL**的完整链。
这一层是既有资产，**不要因为看到别人界面好看就动摇**。

---

# 第三部分 · 参考项目对照（v5 新增）

> 参考对象：**旅途星辰 TripStar**（`github.com/1sdv/TripStar`，ModelScope 空间 18.7k 运行量）。
> Vue 3 + FastAPI + HelloAgents 框架，多 Agent 协作（总控/景点/天气/酒店），
> 异步轮询解 504，小红书数据源，高德 + Google 双地图，知识图谱可视化。

## 16. 采纳清单

### 16.1 采纳（6 项）

| # | 采纳项 | 它的做法 | 本项目落法 | 阶段 |
|---|---|---|---|---|
| 1 | **过程可见** | `task_id` + 每 3s 轮询 + 4 步进度条 | **SSE `status` 事件**（复用现有基础设施，比轮询先进） | Phase 9 |
| 2 | **结果多视图** | 行程概览/景点地图/每日行程/知识图谱 4 tab | 3 视图：概览 / 每日 / **依据**（第三视图换掉它的知识图谱） | Phase 9 |
| 3 | **伴游问答浮窗** | 左下浮窗 + 行程上下文 + 预设问题 | Phase 6 已有后端；补浮窗形态 + **只给证据可答的问题** + 引用标注 | Phase 6 |
| 4 | **预约与时效提醒** | 从游记识别"需提前预约的景点" | 走**知识库条目**（带出处与复查时点），不走爬虫 | Phase 6（依赖知识库内容） |
| 5 | **偏好记忆分权重** | 权重 + 遗忘 + TOP-K 召回 | preferences 加权衰减 + TOP-K 注入 | 低优先，Phase 9 后评估 |
| 6 | **参数表单形态** | 目的地/日期/偏好/特殊需求，天数自动算 | 本项目已有，仅补**日期区间→天数自动计算**（现状需手填天数） | Phase 9 |

### 16.2 不采纳（7 项，含理由）

| # | 不采纳项 | 它的做法 | 拒绝理由 |
|---|---|---|---|
| 1 | **LLM 一次性生成整个行程 JSON** | Planner Prompt 让 LLM 输出含行程/预算的嵌套 JSON，再靠 `_parse_response()` **五级容错**（清字符→修引号→补括号→暴力提取→求 LLM 再修）修回来，README 自己标为**「高危操作」** | **这是它架构里最脆弱的地方**。把行程正确性押在模型输出稳定性上，还自带"修复器"兜底——恰好证明这条路不成立。本项目走**确定性规则排程、LLM 只在需求理解用 ≤1 次**，这是核心资产，**学它等于退步** |
| 2 | **小红书爬虫数据源** | Cookie 直连原生签名 + SSR 备用抓取游记 | **三宗罪**：① 合规风险（违反平台条款、抓取用户 UGC）② 稳定性（签名一变即挂，需长期对抗）③ 数据无可核实时点（"游玩时长"从游记提纯，无从验证）。本项目要上生产，碰不得 |
| 3 | **知识图谱可视化** | 把行程 JSON 转成节点/边（城市-天数-景点-预算）渲染 | **可视化包装，无推理价值**。节点和边全是从行程 JSON 直接映射的，人看懂行程单就够了。投入产出比最低的一项；本项目的对应位置换成**依据视图**（有真实价值） |
| 4 | **景点配图抓取** | 小红书搜图 / Unsplash | 图片对"排行程"的核心决策**无增益**，却引入外部依赖 + 版权风险。本项目当前无图，保持无图 |
| 5 | **Google Maps 双引擎** | Google 主、高德备，国内回退 | 境内产品无必要。且**它的降级模式本项目已有且更强**（ProviderRouter 七态 + stale cache） |
| 6 | **异步轮询**（实现层） | 前端每 3s 发 `GET /status` | **采纳语义、不采纳实现**：本项目用 SSE 推送，无需轮询。<br>但保留一条：**SSE 断线重连时需要能查到当前状态**，故状态需可查询（这条在本项目已有：`get_persistence_status()` + checkpoint） |
| 7 | **多语言 i18n** | Vue I18n + 提示词与图谱底层多语言 | 当前语境无必要，引入会显著增加提示词与测试面 |

### 16.3 待评估（不进本轮，登记备查）

| 项 | 说明 |
|---|---|
| **多城市行程** | TripStar 支持多城市 + 城际移动日 + 城际交通预算。本项目单城市。**这是真实能力缺口**，但复杂度高（城际交通数据、跨城排程、多城天气对齐），且当前 POI 仅 3 城。**建议独立立项**，不塞进 Phase 5–9 |
| **历史计划列表** | TripStar 用磁盘持久化 task state 支持历史计划。本项目需跨会话持久账本（v4 已登记"本期不建"，trip_id 主键已备，立项零返工） |

## 17. 立场（一句话）

> **学它的「用户看得见的部分」，守我们自己的「数据怎么来的部分」。**
>
> TripStar 的价值在**产品呈现与流程编排的表层**——进度可见、多视图、浮窗问答，这些是 18.7k 用户验证过的形态，值得学。
> 但它的**内核（LLM 生成一切 + 爬虫取数 + 无出处内容）恰恰是本项目已经超越的部分**。
> 把它当产品参考是对的，当技术参考会让项目退步。

**两处必须旗帜鲜明地反着做**（避免以后有人问"为什么人家有我们没有"）：

1. **行程不由 LLM 生成**——本项目是确定性排程，模型只理解需求。别人靠五级容错修 JSON，我们不给自己挖这个坑。
2. **数据必有出处**——别人爬小红书拿"游玩时长"，我们标 `SEED/0.5/示例值`。看起来我们内容少，但**我们敢让用户看依据**。

---

# 第四部分 · 落地

## 18. 真基线差距（2026-09-30 实测）

| # | 设计目标 | 真基线 | 差距性质 |
|---|---|---|---|
| 1 | 图节点 = v3 §5.1 节点表 | 仍是旧 10 节点（5 专家壳） | **过渡态**（Agent 类已建、图名未切）。见 §9.3 |
| 2 | 7 Agent 边界 | Agent 类已建并接线（专家节点内委托） | ✅ 基本达成，仅图节点名待切 |
| 3 | 治理层文件 | ✅ 全建（plan_lifecycle/plan_diff/evidence_utils/agent_base/spec） | ✅ |
| 4 | Evidence 组装 | ✅ Phase 4 完成（3 组装点 + 26 例测试） | ✅ |
| 5 | ProviderRouter | ✅ Phase 4 完成（4 链 + 9 例测试）；**Provider 物理归位延后 Phase 8** | ✅（归位属目录整理） |
| 6 | **产品面（进度/多视图/伴游/依据）** | **全部未实现** | ❌ **最大缺口**（v1–v4 都没设计过） |
| 7 | 表单页导航入口 | 无入口 | ❌ 一行菜单的小洞 |
| 8 | 表单日期区间→天数 | 需手填天数 | ❌ 小洞 |
| 9 | `/travel` 接口鉴权（D7） | 未登录可调用 | ❌ 安全洞（页面有登录墙，接口没有） |
| 10 | D2 预订两跳断 | 未修 | 待 Phase 5 |
| 11 | 评测集 | travel/travel-booking/travel-commerce/travel-provider 四套已有；**research freshness/coverage 延后**；assistant_qa 未建 | 部分 |
| 12 | 城市级 POI | 仅 3 城（seed） | 独立线（POI 全国化，另一份审计报告） |

## 19. 施工路线

### 19.1 已完成（Phase 0.5 / 1 / 2 / 3 / 4）

| Phase | 内容 | 状态 |
|---|---|---|
| 0.5 | ICS RFC 6266 修复 + `TRAVEL_USE_LIVE_MAP` 还原 | ⚠️ `.env` 未还原（`TRAVEL_USE_LIVE_MAP=false`），需核 |
| 1 | 建边界（core/services/tools/execution 骨架 + 治理层文件 + 守护测试） | ✅ |
| 2 | Requirement Agent 迁移（slot_filler → agents/，零 LLM） | ✅ |
| 3 | Research/Planning/Optimization service 化 + Agent 包裹 | ✅（图节点名未切，见 §9.3） |
| 4 | Provider 归位裁决 + ProviderRouter + Evidence 数据可信面 | ✅（物理归位延后 Phase 8） |

### 19.2 Phase 5–8 原计划（不变，仅注入产品面对齐项）

| Phase | 原内容 | v5 注入 |
|---|---|---|
| **5 Commerce 统一 + D2 修复** | commerce 子图入口收敛、BOOK 意图收编、`booking_intent` 挂起槽 | **（建议）图节点名切换合并到此卡**（§9.3）；无产品面改动 |
| **6 Travel Assistant + 报告面** | assistant_agent（QUERY）、`map.link` 导出 | **+伴游浮窗形态**（§5）+**预设问题清单**；**+预约提醒知识抽取**（§6，依赖知识库内容） |
| **7 Payment/Recovery 收口** | admin API 生产断头（webhook/对账/人工裁决）、PRICE_CHANGED 消费 | 不变 |
| **8 E2E 收口** | 删 shim、死代码清理、全量回归、文档口径对齐 | **+ Provider 物理归位复核**（Phase 4 延后项）+ **v3/v4 标记历史归档** |

### 19.3 建议新增：Phase 9 产品面收口

**为什么单列一个阶段**：Phase 5–8 是后端交易与问答收口，产品面（进度条/视图重组/导航入口）
是**另一类活**（前端为主 + 一条 SSE 通道），混进去会让"每步可单独回滚"变难。

| 序 | 内容 | 落点 | 可回滚 |
|---|---|---|---|
| 9-A | **导航入口** + **日期区间自动算天数** | `frontend/src/components/layout/navConfig.tsx`；`app/travel/page.tsx` | ✅ 独立 |
| 9-B | **进度事件**（`status` 事件 + 前端进度条） | `app/api/routes/travel.py`；`travel/supervisor.py`；新增 `components/travel/PlanProgress.tsx` | ✅ 独立 |
| 9-C | **结果三视图重组**（概览/每日/依据） | `app/travel/page.tsx`；新增 `components/travel/EvidencePanel.tsx` | ✅ 独立 |
| 9-D | **`/travel` 接口鉴权**（D7） | `app/api/routes/travel.py`（对齐 `resolve_identity`） | ✅ 独立 |
| 9-E | 文案口径修正（"真实路况/天气"→"景点与票价为参考值"） | `app/travel/page.tsx` 页首 | ✅ 独立 |

**验收**：进度事件真实反映节点（无假百分比）；依据视图正确渲染三档（可信/可能变化/待确认）；
未登录调用 `/api/travel/plan` 返回 401；现有 35 个 travel 测试文件全绿 + 前端构建通过。

### 19.4 优先级建议

```
Phase 5（修 D2 断片 + 图名切换）      ← 主流程，优先
   ↓
Phase 9-A/D/E（三处小洞，半天）        ← 性价比最高，可插队
   ↓
Phase 6（伴游问答 + 预约提醒）
   ↓
Phase 9-B/C（进度 + 多视图）
   ↓
Phase 7 / 8
```

**说明**：9-A/D/E 是"一行菜单 + 一句话 + 一个鉴权装饰器"级别的小改动，却直接决定功能能不能被看见、
接口会不会被白用。**建议插在 Phase 5 前后**，不必等 Phase 9。

## 20. 等你处理（外部依赖，BLOCKED 台账）

| 项 | 缺什么 | 卡在哪 | 办好后接到哪 |
|---|---|---|---|
| 城市级 POI live 源 | 无需采购（腾讯 key 已验证可用），需**接线 + 类目过滤** | 种子库仅 3 城；`TencentPOIProvider` 已写好未挂链 | `providers/travel/live/hybrid_poi.py`（另一份 POI 全国化审计报告有详细方案） |
| 营业时间 / 票价 | 腾讯接口**无此字段**（实测）；高德/百度有但覆盖率不保证、无核实时点 | 无权威免费源 | 保持 `unverified` 如实标注，或走知识库 |
| 真实酒店/机票供应商 | 合同与 API 凭据 | 个人开发者只有试用 key | `commerce`/`booking` provider adapter 位（已留） |
| 票务 / 活动 / 公告数据源 | 同上 | 同上 | `providers/events`、`providers/notice` 契约位（`implemented=False`） |
| 预约规则知识库 | **内容**（不是代码） | 知识库无预约类条目 | §6 预约提醒 |
| 腾讯 LBS key 授权档位 | 免费额度 vs 商用授权（**事故风险，非省钱问题**） | 需登控制台核实 | 商用授权：腾讯 5 万/年（高级版 7 万） |

## 21. 决策点（待拍板）

| # | 决策 | 建议 |
|---|---|---|
| **V1** | 设计书处理方式 | **① 新建 v5 自包含定稿，v3/v4 转历史留档（推荐）**；② 仍维持 v3+v4 组合读；③ 只改 v4 不做合并 |
| **V2** | Phase 9 是否新增 | **① 新增 Phase 9 产品面收口（推荐）**；② 拆碎注入 Phase 5–8；③ 全部推后 |
| **V3** | 图节点名切换时机 | **① 与 Phase 5 合并（推荐）**；② 独立小批次插队；③ 留 Phase 8 |
| **V4** | 产品面小洞（导航入口/鉴权/文案） | **① 即刻插入，不等 Phase（推荐）**；② 随 Phase 9 |
| **V5** | 多城市行程 | **① 独立立项备查，不进本轮（推荐）**；② 纳入 Phase 9；③ 明确不做 |

## 22. 验收标准（v5 汇总）

| 维度 | 标准 |
|---|---|
| **稳** | 全量 travel + orchestration 测试绿（基线不回退）；34 条金标阈值不放松；超时注入测试证明降级不挂死 |
| **诚** | 每条对外事实有 Evidence；无源能力 `implemented=False` 如实披露；依据视图三档渲染正确；assistant 引用率 100%、hallucination=0 |
| **可控** | 动钱必经确认门；必去冲突必进审批单；不擅自改用户指定的必去项 |
| **好养** | 分层清晰（Agent→Service→Provider 单向）；新数据源只需改链账 + adapter；LLM 调用数 ≤1–2 次/规划有断言 |
| **产品** | 规划过程可见；结果三视图；追问可答且带依据；入口可达（导航项存在）；未登录接口拒绝 |

---

## 附录 A · 冻结清单（承 v3/v4，零删改）

validator 六轴 / repair 防震荡 / provider 七态 + 共享基建 / booking 八态状态机 + 幂等账本 /
checkpoint 跨轮契约 / 版本链 stamp_version / 测试基线 + 34 金标 / qweather 备用源 /
生命周期 10 态迁移表 / PlanChangeRecord / Evidence 五字段 + confidence 基线 /
ToolSpec 四级 / HumanApprovalRequest / ProviderRouter 选择链 / §9 全部指标口径。

## 附录 B · 遗留登记（G4 台账）

① `travel_plans` 持久账本立项时机 ② TRAVELING/COMPLETED/ARCHIVED 激活（依赖行程时钟）
③ 真实供应商四件套 ④ PDF 依赖评审 ⑤ OR-Tools（2-opt 金标不达标再立项）
⑥ **v5 新增**：多城市行程立项 ⑦ **v5 新增**：偏好权重衰减与 TOP-K
⑧ **v5 新增**：Provider 物理归位（Phase 4 延后至 Phase 8 复核）

## 附录 C · 版本沿革

| 版本 | 日期 | 角色 | 状态 |
|---|---|---|---|
| `travel-domain-current-audit.md` | 2026-09-20 | Phase 0 现状审计（文件/行号事实源） | 现行引用 |
| `TRAVEL_ARCHITECTURE_AUDIT.md` | 2026-09-20 | 最早架构审计 | 历史底稿 |
| `travel-domain-production-design.md` | 2026-09-29 | v1 设计稿 | 被 v3 取代 |
| `travel-domain-refactor-full.md` | 2026-09-29 | 自包含单文件评审稿（审计+设计合并） | 历史留档 |
| `travel-domain-design-v2-mapping-plan.md` | 2026-09-29 | v2 映射与差距分析 | 差异裁决仍有效 |
| `travel-domain-agent-runtime-design.md` | 2026-09-29 | v3（7-Agent + Phase 执行卡） | **被 v5 合并承接** |
| `travel-domain-agent-runtime-design-v4.md` | 2026-09-29 | v4 冻结稿（治理层） | **被 v5 合并承接** |
| **`travel-domain-design-v5.md`（本文）** | **2026-09-30** | **自包含定稿（架构+治理+产品面）** | ⭐ **现行生效** |

# TravelResume-STOPF0-审计与设计（2026-09-23）

> STOP F0：只读架构审计 + Contract 冻结。全部结论基于当前 HEAD（852e6c5 后续为并行会话提交）真实代码，不按旧设计文档假设。
> 结论：**STOP_F0_AUDIT_PASS=true / STOP_F0_PASS=true**

---

## 一、审计结论（F0-1 ~ F0-7）

### F0-1 Travel graph 结构：独立域图（非主图节点、非 workflow）

- 入口链：主图 `router_node` → `route_mode="travel"` → `travel_graph_node`（`backend/orchestration/graph/travel_graph_node.py`，适配器）→ `get_travel_graph().invoke()`（`backend/travel/graph_builder.py:123` 单例）。
- 拓扑（`graph_builder.py:78-84`）：`START → travel_slot_filler → travel_supervisor →（Command(goto) 动态路由）{poi/transit/weather/budget/risk 五专家、travel_validator、travel_repair} → supervisor → travel_reporter → END`。
- 状态：`TravelGraphState`（`graph_state.py:46`，TypedDict total=False，全 JSON 可序列化；brief/itinerary/validation 存 dict，Pydantic 模型经 `load_*/save_*` 边界转换）。
- 输出：`final_answer` + `travel_context`（回传主图快照，`build_travel_context`）。
- 跨轮契约已存在：`new_travel_graph_input()` 只放本轮输入（`graph_state.py:116`）；`brief_fingerprint` 变化 → `planning_reset()` 清产物重排（`slot_filler.py:643-654`）。

### F0-2 Slot schema（`travel/models/brief.py`）

| 类别 | 槽位 | 说明 |
|---|---|---|
| required（缺失即追问） | `destination`、`days` | `REQUIRED_SLOTS`（brief.py:18）；`missing_slots()` 是单一事实源（slot_filler 与 supervisor 共用） |
| optional | `start_date`、`budget_cny`、`preferences`、`must_go`、`avoid`、`pace`、`diet`、`lodging`、`transport`、`origin` | 缺失时专家按默认推进并在 warnings 标注 |
| 默认值 | `party_size=1`（explicit/guess 来源标记）、`pace=moderate` | guess 来源写 note 提示用户可纠正（slot_filler.py:613-617） |
| derived | 无持久化 `end_date` | `start_date + days` 运行时计算（ConversationContext 头部注释明确禁止双写） |

抽取为**纯规则零 LLM**（`slot_filler.py`：destination 只认数据集真实城市名录、预算必须带货币单位、日期区间遮蔽防误读天数）。`lodging/transport/diet` 当前为记录性槽位（不参与排程与指纹——P0 数据源无酒店/交通供给数据）。

### F0-3 缺槽行为：软追问（非 interrupt）

`supervisor.py:133-137`：`brief_missing` 非空 → `TravelDecision(REPORT, "必填槽位缺失…转为追问")` → 跳过全部专家 → reporter 输出 `clarifications` 追问文案（`build_clarification`，slot_filler.py:468）。即：**结束本轮图 + 输出追问 + 等下一轮重新进图**，靠 checkpointer 的 thread 状态在下一轮 `merge_brief` 续槽。

另有真 interrupt 通道：`TRAVEL_USER_DECISION_INTERRUPT`（config/travel.py，默认关）开启时，必去项冲突（`decision_required`）走 LangGraph `interrupt()` 暂停域图，恢复走 `travel_context.resume_decision → Command(resume=...)`（travel_graph_node.py:37-42）。

### F0-4 thread 生命周期：conversation 级复用

`travel_graph_node.py:197`：`thread_id = conversation_id or f"travel-{uuid4().hex}"` —— **同一 conversation 跨轮复用同一 thread**（域图 checkpointer 开启时跨轮补槽成立）。主图 chat 每轮新 thread（域图不受影响）。

⚠️ 缺陷实锤：CS 域图同样用裸 `conversation_id`（`cs_graph_node.py:248`），两域共用 PostgresSaver 同一组 checkpoint 表 → 同一会话先客服后旅游会**共享 checkpoint namespace 互相覆盖**。→ F3 修复 travel 侧前缀化。

### F0-5 ConversationContext（`orchestration/context/conversation_context.py`）

已有：`(tenant_id, user_id, conversation_id)` 三元组主键、进程内 TTL(1800s)+LRU store、Travel 摘要槽位（destination/days/budget_cny/party_size/preferences/must_go/avoid…）、`sync_travel_brief_to_context()`（P2.2，Graph→Context **单向**同步，travel_graph_node 执行后调用）、路由上下文（`active_domain`/`last_intent`/`pending_question`——**纯字符串**）、`mark_turn()`。

Selection Funnel 已复用同一 store（E1 先例）。**无结构化 travel pending**（无 requested_slots/run_id/question_id）。

### F0-6 Checkpointer：默认关，postgres 优先

`config/travel.py`：`TRAVEL_CHECKPOINTER_ENABLED` 默认 **false**（.env 决定实际值）；backend=postgres（PostgresSaver，与主图/CS 共表 + TTL 清理守护单例）→ 失败降级 MemorySaver（degraded）；三级状态可见（healthy/degraded/disabled）+ `TRAVEL_REQUIRE_PERSISTENCE` 强持久化策略（降级拒绝复用跨轮产物，slot_filler.py:628-641）。durable 性：postgres=durable，memory=进程内。

### F0-7 Resume：真 LangGraph resume 通道已存在（仅 decision_required 场景）

`Command(resume=...)` + `__interrupt__` 透传（travel_graph_node.py:37-56）。缺槽追问走的是「重进图」模式而非 resume——对该场景这是**合理现状**（每轮从 slot_filler 重新抽取合并，checkpointer 保留 brief），真正的断点在路由层（见下）。

---

## 二、核心缺口（本轮要解决的）

| # | 缺口 | 证据 |
|---|---|---|
| N1 | **补槽短答案路由断点**：「8万日元」0 信号词 0 城市 → `is_travel_request=False`（travel_prefilter.py:54）→ ContinuationResolver 也不命中（信号表只覆盖「改成3天/太赶了」类指令，`continuation_resolver.py:31-44`，纯槽位值不在其中）→ travel 上下文丢失，追问没人接住 | F0-3/F0-7 的跨轮机制全部依赖「下一轮还能进 travel 域」 |
| N2 | **pending 无结构化状态**：`pending_question` 是追问文案字符串，无 requested_slots/question_id/run_id，无法指导下一轮的槽位解析 | conversation_context.py:88-91 |
| N3 | **slot 无三态**：INFERRED（guess）只有 note 提示，无状态标记 | brief.py / slot_filler.py |
| N4 | **无 travel_run_id**：thread_id=conversation_id 隐式承担，无法表达 NEW_RUN 边界与观测归因 | F0-4 |
| N5 | **无 graceful reconstruction**：checkpoint 丢失时 ConversationContext 摘要槽位没有 Context→Graph 回灌通道（同步是单向的） | F0-5 |
| N6 | **thread namespace 共享**：travel 与 CS 共用裸 conversation_id | F0-4 |

**有意维持的现状（非缺口）**：
- 缺槽不 interrupt 化：软追问是 Phase 3 契约、评测基线依赖它；跨轮已由 checkpointer+thread 复用支持；真 gap 是 N1。
- PATCH=受影响字段进指纹 + 确定性全量重排（百毫秒级，P0 数据源下无实质差异）：任务书 §12 允许的第一版策略，不伪造增量能力。`brief_changed_fields` 已是 dirty_fields 的事实载体。

---

## 三、Contract 冻结

### 1. TravelConversationContext（挂在既有 ConversationContext 上，复用 store）

```python
# 新增字段（ConversationContext dataclass）
travel_run_seq: int            # 当前会话内 run 序号（0=无活跃 run）
travel_run_id: str             # "trv_{conversation短哈希}_{seq:03d}"
travel_stage: str              # "" | "slot" | "planned" | "completed" | "cancelled"
travel_pending: dict | None    # TravelPendingQuestion 快照（结构化，见下）
```

- 只存结构化事实/确认信息/摘要，禁止存完整 prompt、LLM messages、DB session、request object（任务书 §4）。
- 写点：`travel_graph_node` 执行后（扩展 `sync_travel_brief_to_context` → 新增 `sync_travel_run_to_context`）。
- 读点：路由层 TravelPendingResolver、观测。
- reset：`clear_travel_run()`（取消/新 run/completed 时清 pending 与 run 标识，保留摘要槽位）。
- 多 worker 限制（登记）：进程内 store 丢失 → resolver miss → 落回正常路由（不 500）；域图自身 checkpoint（postgres）仍在，用户换完整说法仍可续。与 funnel E1 同一决策先例。

### 2. TravelRun

```text
travel_run_id = f"trv_{sha1(conversation_id)[:8]}_{seq:03d}"
```

- 首次 travel 请求（无活跃 run）→ seq+1 建 run。
- CONTINUE/PATCH/REPLAN **保持同 run_id**（普通补槽/局部修改/约束变化不换 run）。
- NEW_RUN（显式「重新规划/不去X了」信号，规则判定）→ seq+1，旧 run 只留摘要。
- run_id 进 ConversationContext + trace tags + reporter 行程单脚注（可归因）。
- thread 映射（F3）：`thread_id = f"travel:{conversation_id}"` —— namespace 前缀与 CS 隔离；run 维度不进 thread（同 thread 复用 + planning_reset 保证 NEW_RUN 不污染，登记 Deferred）。

### 3. TravelPendingQuestion（结构化 pending）

```python
{
    "question_id": "tq_{uuid8}",      # 一次追问的身份
    "run_id": travel_run_id,
    "requested_slots": ["budget_cny", "lodging"],   # ⊆ 可解析槽位白名单
    "reason": "missing_required" | "optional_followup",
    "created_at": ts,
}
```

产生点：slot_filler 判定 `brief_missing` 非空时（经 adapter 写入 ConversationContext）。白名单第一版：`destination/days/start_date/party_size/budget_cny/preferences/must_go/avoid/pace/lodging`。

### 4. TravelResumeCommand（四态判定，纯规则零 LLM）

入口顺序（router_node 内，位于 ContinuationResolver **之前**）：

```text
User Input
  → TravelPendingResolver（active_domain=travel 且 travel_pending 存在时）
      ├─ NEW_RUN 信号（重新规划/不去X了 + 新城市）→ 换 run，短路回 travel
      ├─ slot 解析命中 ≥1 个 requested_slots（复用 slot_filler 抽取纯函数）
      │    → CONTINUE/PATCH/REPLAN：短路回 travel（user_message 原样进域图，
      │      slot_filler merge_brief 新值覆盖旧值；指纹变化自动触发重排=REPLAN）
      └─ 未命中 → None（ContinuationResolver → 正常路由）
  → ContinuationResolver（既有，不改语义）
  → Coarse Domain Router
```

- CONTINUE：回答追问（「8万日元」）→ 同 run 续槽。
- PATCH：局部修改（「第二天不要海游馆」→ avoid 变 → 指纹变 → 重排）→ 同 run。
- REPLAN：关键约束变化（预算/天数/目的地变 → 指纹变 → planning_reset 重排）→ 同 run。
- NEW_RUN：显式重开信号 → 新 run_id。
- 判定优先级：规则 → （未来）结构化 classifier → LLM fallback（本轮只做规则）。
- 语义保证（任务书 §11）：merge_brief 新值覆盖旧值（slot_filler.py:407-445 已有），唯一 current value；INFERRED 槽位（party_size guess）以 note 透明化 + confirmed_slots 白名单由 REQUIRED_SLOTS+显式表达构成（状态三态最小落地：`TravelBrief.source_marks` 记录 explicit/guess 来源，不扩 Pydantic 大改）。

### 5. Graceful Reconstruction（F3）

- adapter 在 checkpointer 开启时 `graph.get_state(config)` 探测 thread 状态：空 → `resume_mode=reconstruct` + 从 ConversationContext 摘要槽位构造 `reconstruct_brief` 传入域图（slot_filler 在无 checkpoint brief 时以它为 previous 基底，再合并本轮消息）。
- 有 checkpoint → `resume_mode=checkpoint`（现状自然成立）。
- 日志/trace 必须区分两种 resume_mode（任务书 §15）；reconstruct 永不 500。

### 6. 职责边界（任务书 §14 冻结）

- Checkpoint =「图执行到哪了」（精确位置/interrupt 状态）。
- ConversationContext =「这个会话在规划什么」（已确认事实/active run/pending/摘要）。
- 二者不得互相替代；身份永远由当前请求入口提供（不从 checkpoint/context 恢复授权）。

---

## 四、实施映射（F1-F4）

| 阶段 | 内容 | 主要文件 |
|---|---|---|
| F1 | ConversationContext 增 travel_run/pending 结构化字段 + 读写/merge/reset | `orchestration/context/conversation_context.py` |
| F2 | TravelPendingResolver + 路由挂载 + pending 写回（slot_filler→adapter→context）+ lodging 抽取规则 | `orchestration/context/travel_pending_resolver.py`（新）、`graph/router_node.py`、`graph/travel_graph_node.py`、`travel/slot_filler.py` |
| F3 | thread namespace 前缀 + checkpoint 探测 + reconstruct 回灌 | `graph/travel_graph_node.py`、`travel/slot_filler.py`、`travel/graph_state.py` |
| F4 | trace 字段/结构化事件 + T1-T15 回归 | `graph/travel_graph_node.py`、`tests/travel/`、`tests/orchestration/context/` |

禁触清单（其他会话 WIP）：`tasks/task_executor.py`、`tasks/admission/`、`tests/conftest.py`、task fencing、queue topology、`context_budget/`、SQL runtime authorization。

## 五、测试矩阵 → Gate 映射

T1-T15（任务书 §22）映射 G1-G15：T1/T2→G3/G4/G5，T3→G11，T4/T5/T6→G12，T7→G5/§11，T8/T9→G6，T10→G7，T11→G8，T12→G9，T13/G15→G10，T14→G11，T15→G13。

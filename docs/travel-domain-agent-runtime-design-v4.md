# 旅游域 Agent Runtime 企业级目标设计 v4（最终冻结稿）

> **状态更新（2026-09-30）**：**现行唯一设计书 = `docs/travel-domain-design-v5.md`**（v5 自包含定稿：v3 架构 + v4 治理层 + **新增产品面**，并含 TripStar 参考对比）。本文转为**历史留档**，不再单独更新；v3/v4 已冻结的架构决策与契约在 v5 中原样承接，**一条未改**。执行以 v5 为准。
>
> 状态：**v4 冻结稿**（2026-09-29）。基线链：`docs/travel-domain-current-audit.md`（审计，文件/行号事实源）→ `docs/travel-domain-agent-runtime-design.md`（v3，7-Agent 架构与 Phase 执行卡，**继续有效**）→ 本文档（v4，在其上冻结企业级治理层）。
> v4 新增内容（用户 2026-09-29 拍板的 8 项补充）：①TravelPlan 生命周期 ②Plan Version 管理 ③Evidence 事实可信模型 ④Tool Governance ⑤Human Approval 统一模型 ⑥Agent Evaluation ⑦Cost/Latency Budget ⑧Provider Selection Strategy。
> 四优先级不变：**稳定性 > Agent 数量；确定性 > 模型自由生成；数据真实性 > 内容丰富；不重复实现既有生产资产。**
> 硬约束不变：validator / repair / provider 七态契约 / booking 状态机 / checkpoint 跨轮契约 / 544 测试基线零删改核心逻辑；git mv + shim + 加法式扩展；禁止推倒重写。
> **冻结声明**：v4 §3-§6、§8、§9 的契约（状态机/证据字段/工具元数据/审批结构/指标口径）一经评审通过即冻结；后续变更走版本化，不再隐式演进。

---

## 1. Domain Architecture（7 Agent，承 v3，冻结）

v3 §1 架构图继续有效并冻结：Agent 层 7 个（① Travel Supervisor / ② Requirement / ③ Research / ④ Planning / ⑤ Optimization / ⑥ Commerce / ⑦ Travel Assistant）；五专家能力 = `services/` 内部实现；门禁层 travel_validator / travel_repair / travel_reporter 非 Agent 节点；图节点 10→10；图拓扑与 stage 重映射见 v3 §5。

**v4 唯一架构增量**：Supervisor 在每次 stage 推进时**同步维护 TravelPlan 生命周期状态**（§3.1）——这是新增职责，不改变调度机制。

## 2. Agent Responsibility（承 v3，冻结）

v3 §3 职责边界表冻结（7 Agent 的职责/输入输出/工具权限/LLM 策略/超时降级）。v4 增量三处，均加法式：

| Agent | v4 增量职责 | 落点 |
|---|---|---|
| ① Supervisor | stage 推进时写 `plan_status`（生命周期）+ 派发/裁决 `pending_approval` | §3.1 / §6 |
| ③ Research | 为全部产出**附加 Evidence**（候选池/天气/知识） | §4 |
| ⑦ Assistant | 回答必须**引用 Evidence fact_id**（citation 纪律，评测口径见 §9） | §4 / §9 |

## 3. Runtime State Model（新增）

### 3.1 TravelPlan 生命周期（补充 1）

**动机**：企业系统必须以**状态**而非对话推断回答"现在能不能改第三天酒店"。生命周期挂在 plan 实体上，与图 stage（单次运行内的执行位置）是两个正交概念：stage 管本轮执行到哪，plan_status 管这个计划处于什么阶段。

**状态机（10 态，全部采纳用户定义，不增不减）**：

```
DRAFT → COLLECTING_REQUIREMENTS → RESEARCHING → PLANNING → OPTIMIZING
      → WAITING_CONFIRMATION → CONFIRMED → TRAVELING → COMPLETED → ARCHIVED
```

**落地设计（复用 checkpoint，新表 0 张）**：

- 纯规则状态机模块 `travel/core/plan_lifecycle.py`：合法迁移表（下表）+ `transition(current, event) -> (next, reason)` 单一实现，非法迁移 fail-fast（不允许跳状态）。
- TravelSessionState 新增字段（flag `TRAVEL_SESSION_STATE_V2` 组内）：`trip_id`（v3 已定，uuid7）、`plan_status`、`status_reason`、`status_history`（尾部截断保留最近 20 条 `{from, to, at, event, reason}`，控制 checkpoint 体积）、`confirmed_version: int|None`。
- 与 stage 的关系：COLLECTING_REQUIREMENTS/RESEARCHING/PLANNING/OPTIMIZING 四态由 supervisor stage 推进**顺带写入**（每个 `Command(goto)` 处一次字段赋值），零新机制。

**迁移表（确定性，无 LLM）**：

| from → to | 触发事件 | 说明 |
|---|---|---|
| DRAFT → COLLECTING_REQUIREMENTS | 首条旅游消息，trip_id 分配 | NEW_RUN 时建 trip |
| COLLECTING_REQUIREMENTS → RESEARCHING | required 槽齐（destination/days），无需追问 | brief 完成即走 |
| COLLECTING_REQUIREMENTS → COLLECTING_REQUIREMENTS | 追问轮 | 自环，不计违规 |
| RESEARCHING → PLANNING → OPTIMIZING | stage 推进 | supervisor 顺带写 |
| OPTIMIZING → WAITING_CONFIRMATION | quality gate PASS/WARNING 交付，或 §6 审批挂起 | 交付≠确认 |
| WAITING_CONFIRMATION → CONFIRMED | 用户明确接受（"就这个方案/确认"规则匹配；歧义→clarify） | 记 `confirmed_version` |
| WAITING_CONFIRMATION → RESEARCHING/PLANNING/OPTIMIZING | 用户修改（action=MODIFY）：brief 槽变→RESEARCHING；仅行程级调整→PLANNING；仅排程/换点类→OPTIMIZING | 新 plan_version，§3.2 |
| CONFIRMED → WAITING_CONFIRMATION | 对已确认计划修改：出新版本后**重新进入待确认** | 已确认版本不覆盖，confirmed_version 仍指旧版 |
| CONFIRMED → TRAVELING → COMPLETED → ARCHIVED | **保留态，本期不激活** | 无行程时钟基础设施；枚举与迁移表先冻结，激活属后续立项（同 BLOCKED 纪律，不许假装） |

**CANCEL 语义澄清**：现有 `CANCEL_TRAVEL_RUN`（取消当前运行）不是生命周期终态——plan 与 status_history 保留，可续聊可改。若未来需要"放弃此计划"再议 ABANDONED 态（本期不设计）。

**"修改第三天酒店"全链路**：CONFIRMED（confirmed_version=3）→ "修改第三天酒店" → supervisor action=MODIFY → lifecycle CONFIRMED→PLANNING → lodging 槽变更出 plan v4（change record 记 `modified:[day3.lodging]`）→ OPTIMIZING→WAITING_CONFIRMATION → 用户"确认" → CONFIRMED（confirmed_version=4）。全程状态驱动，无需从对话猜。

### 3.2 Plan Version 管理（补充 2）

**现状资产确认**：版本链已存在——`models/itinerary.py::stamp_version` 每次修改生成新版本不覆盖旧版；`brief_fingerprint / brief_change_reason / brief_changed_fields` 已有。v4 把它升格为**明确的版本管理契约**：

1. **不覆盖更新**：任何修改产出新 `plan_version`，旧版本在 checkpoint 版本链内全量保留（现状机制，冻结）。
2. **变更记录结构化**（新增 `core/plan_diff.py`，纯函数）：

```json
{
  "version": 4,
  "parent_version": 3,
  "change": {
    "added": [], "removed": ["poi_disney"],
    "modified": [{"day": 3, "field": "lodging", "from": "...", "to": "..."}],
    "brief_fields": ["lodging"]
  },
  "change_reason": "user_request",
  "quality": "PASS"
}
```

   change 由新旧版本**确定性 diff 派生**（POI 集合差 + brief 变更字段），禁止手写（G2）。`history` 列表（v3 已定）存版本摘要+change 摘要。
3. **三个企业能力**：
   - **回滚** = 以旧版本内容生成新版本（`change.type="rollback"`），永不删除/改写历史版本；
   - **对比** = `plan_diff(old_version, new_version)` 纯函数，reporter/assistant 可渲染两版差异；
   - **审计** = checkpoint 内 `status_history` + `history` 版本摘要。**边界如实声明**：checkpoint TTL 7 天，跨会话/超期审计与"列出我的历史行程"需要持久账本（`travel_plans` 类单表）——**本期不建**（维持新表 0 张），trip_id 已是现成主键，立项时零返工（G4 台账登记此决策）。

## 4. Data Trust Model（新增，补充 3）

**动机**：旅游事实（POI/酒店/价格/天气/活动）来源混杂，Report 与 Assistant 必须能自动区分"可信 / 可能变化 / 需要确认"，且**不允许每个 Agent 自定义口径**。

**统一 Evidence 模型**（`core/contracts.py::Evidence`，所有事实字段经它包装进 state）：

```json
{
  "fact_id": "poi_0031",
  "value": { },
  "source": "tencent_lbs | qweather | seed | rag:doc-xxx | estimator:v1",
  "source_type": "LIVE | RAG | CACHE | SEED | ESTIMATE",
  "confidence": 0.92,
  "verified_at": "2026-09-29T10:00:00+08:00",
  "expire_at": "2026-09-29T10:30:00+08:00"
}
```

**与冻结资产的接缝（只包不改）**：

| 数据 | Evidence 来源映射 | 说明 |
|---|---|---|
| Provider 结果（place/route/weather/price） | status(七态) × Freshness(live/cached/stale/unknown) → LIVE/CACHE + verified_at/expire_at（现有 TTL 即 expire_at） | **Provider 层零改动**，Evidence 在域层组装 |
| 种子 POI | source_type=**SEED**（坐标/营业时间/票价自声明示例值） | 对用户口径的诚实扩展（用户四类 + SEED）；confidence 默认 0.5，**不冒充时效**（expire_at=None） |
| RAG 知识/风险摘录 | RAG + 来源文档 id | risk 专家已有 knowledge_refs，迁入 Evidence 形态 |
| 预算四分项 | ESTIMATE（estimator 版本入 source） | "只算不判"语义不变 |
| booking quote | **不动**——052 不可变快照本身就是证据级事实（source_type=LIVE，报价时点固定） | 冻结层零接触 |

**confidence 基线**（默认值表，可配置）：LIVE=0.95 / CACHE=0.85（stale 降 0.6）/ RAG=0.7 / SEED=0.5 / ESTIMATE=0.5。缺 verified_at 的事实 confidence 上限 0.5。

**消费规则（唯一口径，reporter 与 assistant 共用，写入 contracts）**：
- `可信`：LIVE 且未过期 → 正常陈述；
- `可能变化`：CACHE/stale → 文案加"可能有变化，出行前请核实"；
- `需要确认`：SEED/ESTIMATE/confidence<0.6 → 文案显式标注"示例/估算值"。
- **validator 联动**：`SOURCE_STALE` warning 消费 state 内 Evidence 字段（零 IO 红线不变）；Assistant 引用无 Evidence 的事实 = 评测 hallucination（§9）。

## 5. Tool Governance（新增，补充 4）

**动机**：Agent 选 Tool 不能只看名字——必须声明副作用、是否收费、是否需确认、能力范围。

**Tool 规格声明**（`travel/tools/spec.py` 静态表；旅游 Domain Tool 不注册主图 capability，此表与 `capabilities.yaml` 不重复不冲突——后者管主图 Skill 面，G2 边界清晰）：

```python
ToolSpec(name="travel.hotel.book", capability=["quote", "booking"],
         side_effect="TRANSACTION", requires_confirmation=True,
         fee_bearing=True, allowed_agents=["commerce"])
```

| side_effect 级 | 含义 | 现有工具归类 | 执行门 |
|---|---|---|---|
| READ | 检索/读取 | search_poi / poi.detail / weather.query / retrieve_knowledge / memory.search / map.link | 无 |
| COMPUTE | 纯计算 | calculate_route / route.optimizer / calculate_budget / plan_diff / calendar.export | 无 |
| WRITE | 域内落库（软失败） | memory.save / feedback | 软失败纪律（失败不挡主流程） |
| TRANSACTION | 外部副作用/资金相关 | hotel/flight booking·cancel（经 Commerce Agent） | **booking 确认门（cfp 绑定/TTL/金额互检，冻结）**；admin 侧对账/人工裁决走 `security/tool_approval.ensure_approved()`（现有审批门，Phase 7 接线） |

**三条治理规则**：
1. **Agent→Tool 白名单**：每个 Agent 的允许工具清单写死在 `core/agent_base.py` 契约（v3 §3 权限列的代码化）+ 守护测试静态断言（Agent 模块 import 面不得越出白名单）。Supervisor 永远不能直接调 POI/Booking 工具。
2. **TRANSACTION 只经 Commerce Agent**：任何其他 Agent/Service import booking 执行面 = 违规（守护测试覆盖）。
3. **requires_confirmation=True 的 Tool 必须先有 §6 审批单**才能执行——机制上就是现有确认链，规格化后纳入评测（B1-B20 已含）。

## 6. Human Approval Framework（新增，补充 5）

**动机**：必去景点当天关闭、预算超标这类决策**不能自动改**；booking 已有确认链，规划侧缺少同类机制，两套并存容易散。

**统一审批结构**（TravelSessionState 新增 `pending_approval`，同 flag 组）：

```json
{
  "approval_id": "apr_...",
  "type": "PLAN_CHANGE | MUST_GO_CONFLICT | BUDGET_EXCEED | BOOKING | REFUND",
  "reason": "must_go unavailable: 三坊七巷当日闭馆",
  "options": [
    {"id": "A", "label": "替换为同类人文景点", "detail": "..."},
    {"id": "B", "label": "该日留白并顺延", "detail": "..."}
  ],
  "created_at": "...", "expires_at": "..."
}
```

**机制映射（全部复用现有件，不新建图机制）**：

| 审批类型 | 触发 | 现有承接 | 生命周期联动 |
|---|---|---|---|
| MUST_GO_CONFLICT | validator `decision_required`（现状机制，interrupt 默认关） | 升级为构造审批单入 state；weather 自动换点仍只作用于**非必去**（纪律不变） | OPTIMIZING→WAITING_CONFIRMATION |
| PLAN_CHANGE / BUDGET_EXCEED | budget 超 brief 预算 / 修改涉及必去项 | supervisor 规则判定构造 | 同上 |
| BOOKING | 下单确认 | **现有 booking 确认链原样**（cfp/TTL/金额互检），本模型只是它的统一外壳 | BOOKING 审批不占用 plan lifecycle（交易域状态机自治） |
| REFUND | 退款类 | 登记类型，无真实供应商不激活（BLOCKED 纪律） | — |

- **裁决通道**：用户下一轮消息 → `TravelPendingResolver` 扩展一档（approval_id 优先于现有 cancel>avoid_patch>补槽>new_run 顺序，规则化匹配选项 id 或语义映射到 options）；超时（expires_at 过期）→ 审批单作废并如实披露，不默认选 A。
- **与 D2 修复的关系**：v3 的 `booking_intent` 挂起槽并入本模型——`pending_approval(type=BOOKING)` 与 `booking_intent` 是同一挂起语义的两个侧面，Phase 5 一起落，避免两套 pending。

## 7. Memory Architecture（承 v3，冻结）

`travel/memory/` 横向模块（无图节点）：preferences（长期偏好+负偏好+扩展类别）、checkpoint（执行态+版本链+生命周期+审批单）、ConversationContext（会话摘要）。消费方：Requirement（预填/保存）/ Assistant（召回）/ Commerce（lodging 可选）。软失败纪律不变；预填在指纹计算之前（事故教训）。v4 增量：`travel.memory.search` 返回的偏好条目带 Evidence 形态（source_type=SEED/RAG 同口径），Assistant 引用偏好时同受 §4 消费规则约束。

## 8. Provider Architecture（新增，补充 8）

**现状确认**：天气链已实现 primary→fallback（`FallbackWeatherProvider`：腾讯主→和风备，双败保留主因）；七态+Freshness+stale-if-error 全在。v4 把"选择策略"从单点实现**上升到架构层**，不重写现有实现。

**ProviderRouter**（`travel/providers/runtime/router.py`，Phase 4 引入）：

```
selection: capability → 有序 provider 链（capabilities.py 能力账声明）
weather:  [tencent, qweather] → stale cache → 七态失败(unknown/UNAVAILABLE)
map:      [tencent_live, local_estimate]  （local_estimate=degraded 兜底，现状即此链）
poi:      [seed] （唯一源；未来城市级 live 源=追加链头，契约位已留）
price:    [fake_commerce] （真实供应商 BLOCKED）
```

- 规则：**primary → fallback → stale cache（stale-if-error 600s）→ 七态失败**；每次降级记 Evidence（source_type 降级 LIVE→CACHE，confidence 同步降）——链路选择自动反映到 §4 信任口径。
- **纪律**：Router 只存在于 Provider 层；Agent/Tool 永远拿到统一 `ProviderResult`，**不知道也不允许知道**背后是哪个 adapter（Agent 不选 Provider——这是与"Agent 选 Tool"的边界：Agent 声明需要什么 capability，Router 决定谁供给）。
- 迁移方式：weather 作为 Router 首个消费者做 parity（双败保留主因语义逐字保留，qweather_backup 15 用例必须绿），map/poi/price 链先声明后接管。

## 9. Evaluation 与 Cost/Latency Budget（新增，补充 6+7）

### 9.1 业务指标（`travel/evaluation/`，指标口径冻结）

| Agent | 指标 | 数据集/来源 | 目标 |
|---|---|---|---|
| Requirement | slot accuracy / clarification rate | `datasets/travel/slot/cases.jsonl`（Phase 2 建，≥50 例） | 槽位 ≥95%、人数 ≥99%、否定 ≥98%；clarification rate 记录基线后冻结（追问答比答错便宜，不压低它） |
| Research | source coverage / freshness | 新增 research 评测集（Phase 4）：候选池中带 LIVE/CACHE Evidence 占比、stale 占比 | 基线落盘后冻结；无源能力如实计 0 不造假 |
| Planning/Optimization | constraint satisfaction / distance score / budget deviation | **现有 34 条金标**（`test_quality_golden.py` 阈值已冻结：valid_poi=1.0、must_go_coverage=1.0、avoid_violation=0、budget_silent_over=0、单日在途≤150min 等） | 不放松，v4 只新增展示口径：budget_deviation=(估算-预算)/预算 进评测报告 |
| Assistant | citation correctness / hallucination rate | 新增 assistant_qa 评测集（Phase 6，≥30 例） | 每条事实性回答必须含 Evidence fact_id 引用；**hallucination=0 容忍**（无证据断言即违规） |
| Commerce | booking success / recovery success | **现有 B1-B20 + 生产门 T1-T12=0**（不回退） | 维持冻结阈值 |

### 9.2 LLM 预算（一次完整规划）

| Agent | LLM 调用预算 | 说明 |
|---|---|---|
| Supervisor / Research / Planning / Optimization / Report | **0** | 全确定性（v3 已定，冻结） |
| Requirement | **≤1**（flag off 时=0） | LLM 仅补规则层缺失/低置信槽；**run 内硬上限 1 次**（state 计数器 `llm_calls_used`，超限直接走规则+追问）；零重试 |
| Assistant | ≤1 / 每轮 QUERY（flag off 时=0） | 模板组装优先 |

**总预算：一次规划 LLM ≤1~2 次。** 执行保障：`quality_metrics.py` 加 `travel_llm_calls_total` 计数器（按 agent 维度），CI 金标跑断言计数不超预算——**防止以后 Agent 越加越慢**（慢化在评测期就红灯，而不是上线后）。

### 9.3 时延预算（承 v3 §8，冻结汇总）

| 层 | 预算 | 超时行为 |
|---|---|---|
| 单 Tool/Provider | 3-5s（place 3 / route 4 / weather 5 / knowledge 4） | 七态 TIMEOUT → 降级 |
| 单 Agent | 10s（Research/Optimization 15s） | degraded + 披露，链路继续 |
| 整次规划 | **30s deadline** | 强制 REPORT（已有成果+披露）= safe_report |
| 治理新增 | 审批等待**不计入** 30s（WAITING_CONFIRMATION 是跨轮状态，deadline 只管单轮执行） | — |

## 10. Migration Plan（承 v3 执行卡 + v4 增量注入）

v3 §6 的 Phase 0.5-8 卡片结构、纪律、回归门全部继续有效。v4 八项补充按"不新增 Phase、注入既有 Phase"原则分配：

| Phase（承 v3） | v4 增量注入 |
|---|---|
| 0.5 可先行 | 不变（D1 ICS + TRAVEL_USE_LIVE_MAP 还原） |
| 1 建边界 | +`core/plan_lifecycle.py`（状态机+迁移表+守护测试）；+`core/plan_diff.py`；+`Evidence` 契约与 confidence 基线表；+`travel/tools/spec.py` ToolSpec 声明与 Agent→Tool 白名单守护测试；+`pending_approval`/`plan_status` 等 state 字段定义（flag 关） |
| 2 Requirement | +LLM run 内硬上限计数器（`llm_calls_used`）；+`travel.memory.search/save` 返回 Evidence 形态偏好 |
| 3 三 Agent 包裹 | +supervisor stage 推进顺带写 `plan_status`（四态自环与推进）；+Research 产出附加 Evidence（候选池/天气/知识三处）；+validator `SOURCE_STALE` 消费 Evidence；+必去冲突→`pending_approval(MUST_GO_CONFLICT)`→WAITING_CONFIRMATION 路径；+`travel_llm_calls_total` 计数器 |
| 4 Provider 归位 | +ProviderRouter（weather 首个消费者，parity=qweather 15 用例全绿）；+Evidence 的 verified_at/expire_at 接 Provider TTL；+research freshness/coverage 评测集 |
| 5 Commerce 统一 | +BOOKING 审批统一外壳（现有确认链不动）；+booking_intent 并入 pending_approval 模型（修 D2，一处机制两用） |
| 6 Assistant | +assistant_qa 评测集（citation correctness / hallucination=0）；+reporter 按 Evidence 三档渲染（可信/可能变化/需要确认） |
| 7 Payment/Recovery | 不变（admin API 断头 + PRICE_CHANGED 消费） |
| 8 E2E 收口 | +全指标基线冻结（slot/research/assistant/commerce 四套+金标）；+LLM 预算断言进 CI；+AGENTS.md/规范口径更新（生命周期与治理层入档） |

**明确不做（v4 追加）**：TRAVELING/COMPLETED/ARCHIVED 本期不激活（无时钟基础设施，登记保留态）；跨会话持久 plan 账本（travel_plans 表）本期不建（trip_id 主键已备，立项零返工）；ABANDONED 态不设计；PDF 导出维持暂缓。

---

## 附：v4 冻结清单与遗留登记

**冻结资产（执行期零删改，只允许加法）**：validator 六轴 / repair 防震荡 / provider 七态+共享基建 / booking 八态状态机+幂等账本 / checkpoint 跨轮契约 / 版本链 stamp_version / 544 测试基线+34 金标 / qweather 备用源。
**v4 新冻结契约**：生命周期 10 态迁移表 / PlanChangeRecord 结构与 diff 派生 / Evidence 五字段+confidence 基线 / ToolSpec 四级 side_effect / HumanApprovalRequest 结构与裁决顺序 / ProviderRouter 选择链 / §9 全部指标口径。
**遗留登记（G4 台账）**：① travel_plans 持久账本立项时机（trigger="列出我的行程"产品需求或审计超 7 天）② TRAVELING/COMPLETED/ARCHIVED 激活（依赖行程时钟）③ 真实供应商四件套（城市级 POI 源/event/notice/酒店机票）④ PDF 依赖评审 ⑤ OR-Tools（若 2-opt 金标不达标再立项）。

# 旅游域 Agent Runtime：原计划与实施情况对照（外部评估材料）

> **用途**：自包含评审材料，可脱离代码仓库独立阅读，供外部评审方（GPT）做独立评估。文中文件路径/代码摘录指向真实仓库 `backend/` 下代码，可逐条核对。
> **基线与提交链**：设计基线 commit `03a8eb3`（2026-09-29）；实施提交 `87b39fd`（D1 修复）→ `63710d5`（设计链 6 文档）→ `3077c76`（Phase 1）。评估时点 HEAD = `3077c76`。
> **配套文档**（已入库，本文摘其要点并自足）：`docs/travel-domain-current-audit.md`（现状审计）／`docs/travel-domain-agent-runtime-design.md`（v3 架构）／`docs/travel-domain-agent-runtime-design-v4.md`（v4 冻结稿）。
> **评估请求**：重点看 §5 对照矩阵、§6 偏差披露、§7 约束自评是否诚实，§9 八个待确认问题请逐条给意见。

---

## §1 系统背景（self-contained）

- 平台：电商 RAG + Multi-Agent 平台。Backend = FastAPI + LangGraph；DB = PostgreSQL 双库（业务库 5433 容器）；网关 APISIX 唯一入口；主图 `/chat/stream` SSE 流式，主图 9 核心节点冻结。
- 旅游域现状（重构前）：LangGraph 域图 10 节点（slot_filler → supervisor → 五专家 poi/transit/weather/budget/risk → validator → repair → reporter），**全链零 LLM**（纯规则+词表）；3 城 31 条种子 POI（坐标/票价自声明示例值）；commerce（比价）与 booking（下单）两个子图；provider 七态契约（SUCCESS/NOT_FOUND/UNAVAILABLE/RATE_LIMITED/INVALID_RESPONSE/TIMEOUT/UNAUTHORIZED + DISABLED）；checkpointer 三域共表（PostgresSaver，thread 前缀 `travel:{tenant}:{user}:{conv}`）；544 个测试 + 34 条金标门禁。
- 重构前实测 7 缺陷：D1 ICS 导出中文目的地 500 / D2 预订对话两跳必断 / D3 路由词表缺口 / D4 "2个大人"排成 1 人 / D5 "节奏慢一点"不生效 / D6 关天气仍声称用了天气预报 / D7 /travel 页无鉴墙。
- 仓库铁律：G1 声明式注册 fail-fast / G2 单一事实源禁手抄派生量 / G3 谁定义谁注册 / G4 例外必须登记台账；不动主图 builder；局部测试必须 `--no-cov`；多会话并行提交必须 pathspec 双重限定。

## §2 原计划演进史（三轮用户拍板）

**第一轮（v2 方案，用户提供）**：三层 19 组件版——Conversation 层（Requirement/Preference/Clarification/Memory 4 Agent）+ Planning 层（8 Agent）+ Transaction 层（Hotel/Flight/Ticket/Booking/PriceMonitor/Recovery 6 Agent）+ 统一 Tool 信封 + OR-Tools。经映射分析发现约 70% 与既有 v1 设计重合，且多处与仓库铁律冲突（validator 零 IO 红线、已注册 capability 名冻结、prefilter 守护测试），产出 `docs/travel-domain-design-v2-mapping-plan.md`（映射+差距+6 裁量点）。

**第二轮（v3 收敛，用户拍板）**：**不保留"五专家规则引擎"形态，升级为企业级 Travel Agent Runtime；不拆过细 Agent，不复制 Hotel/Flight/Ticket 三套 Agent**。核心 7 Agent：Travel Supervisor / Requirement / Research / Planning / Optimization / Commerce / Travel Assistant。五专家能力迁移为 Agent 内部 Service；Memory 横向能力不设节点；Route Optimization 轻量算法（不引 OR-Tools）。产出 `docs/travel-domain-agent-runtime-design.md`。关键结构决策：五专家 5 节点收敛为 Research/Planning/Optimization 3 节点，新增 Assistant/Commerce 2 节点，**图节点 10→10**；validator/repair/reporter 是门禁层基础设施**不算 Agent、不改名不搬逻辑**。

**第三轮（v4 企业级补充，用户 8 项，2026-09-29）**：①TravelPlan 生命周期 10 态 ②Plan Version 管理（不覆盖更新+diff+回滚+审计）③Evidence 统一事实可信模型 ④Tool Governance（副作用四级+确认要求+白名单）⑤Human Approval 统一模型 ⑥Agent Evaluation 业务指标 ⑦Cost/Latency Budget（规划链 LLM≤1）⑧Provider Selection Strategy（ProviderRouter）。用户明令：**先冻结 v4 再施工，不要开始 Phase 1**。产出 `docs/travel-domain-agent-runtime-design-v4.md`（冻结稿）。v4 落盘后用户令继续，进入施工。

**四优先级（全程裁决标准）**：稳定性 > Agent 数量；确定性 > 模型自由生成；数据真实性 > 内容丰富；不重复实现既有生产资产。
**禁止事项（用户原文）**：删除已有 validator/repair/provider/booking 核心代码；大规模推倒重写。

## §3 冻结契约要点（计划口径，v4）

### 3.1 七 Agent 职责边界

| # | Agent | 职责 | LLM | 超时/降级 |
|---|---|---|---|---|
| ① | Travel Supervisor（域主） | 意图层 PLAN/MODIFY/QUERY/BOOK/CANCEL + 现有 decide() stage 机保留 + 30s deadline + 护栏 | 无 | 超限强制 REPORT（safe_report） |
| ② | Requirement | NL→TripBrief（13 槽+adults/children 拆分+负偏好）；clarify 内置；调 Memory 预填/保存 | 两层：规则兜底+LLM 结构化（flag off，零重试，失败回落规则+追问） | 10s |
| ③ | Research | 城市归一+候选池构建+天气三态+知识/风险摘录，产出附加 Evidence | 无 | 15s（provider 线程池并行扇出） |
| ④ | Planning | must_go 解析+日程骨架分配 | 无 | 10s |
| ⑤ | Optimization | 排程（rebuild_days 唯一重排）+2-opt+坏天气换点（必去永不换）+四分项预算+版本盖章 | 无 | 15s |
| ⑥ | Commerce | 交易唯一入口；Hotel/Flight/Ticket=kind 参数+provider adapter；内部复用现有 commerce 归一门+booking 八态状态机 | 无 | fail-closed：UNKNOWN→IN_DOUBT 绝不猜成败 |
| ⑦ | Travel Assistant | QUERY 行程问答；只引 state/RAG 事实禁编造 | 模板优先，LLM 润色 flag off | 8s，失败模板直答 |

Memory（横向，无图节点）= preferences（含负偏好）+ checkpoint（执行态/版本链/生命周期/审批单）+ ConversationContext 摘要；消费方 Requirement/Assistant/Commerce；软失败不挡主流程。门禁层 travel_validator（六轴+3 项新 warning）/ travel_repair（≤2 轮+防震荡）/ travel_reporter 原样保留。

### 3.2 TravelPlan 生命周期（v4 §3.1）

10 态：`DRAFT → COLLECTING_REQUIREMENTS → RESEARCHING → PLANNING → OPTIMIZING → WAITING_CONFIRMATION → CONFIRMED → TRAVELING → COMPLETED → ARCHIVED`。合法迁移表（冻结）：

| from → to | 触发 |
|---|---|
| DRAFT→COLLECTING_REQUIREMENTS | 首条旅游消息（trip_id 分配） |
| COLLECTING_REQUIREMENTS→自身/RESEARCHING | 追问自环 / required 槽齐 |
| RESEARCHING→PLANNING→OPTIMIZING | stage 推进顺带写 |
| OPTIMIZING→WAITING_CONFIRMATION | gate PASS/WARNING 交付或审批挂起 |
| WAITING_CONFIRMATION→CONFIRMED | 用户明确接受（规则匹配，歧义→clarify） |
| WAITING_CONFIRMATION→RESEARCHING/PLANNING/OPTIMIZING | 用户修改三类重入点 |
| CONFIRMED→WAITING_CONFIRMATION | 已确认计划修改：新版本重新待确认 |
| CONFIRMED→TRAVELING→COMPLETED→ARCHIVED | **保留态本期不激活**（无行程时钟基础设施） |

存储：TravelSessionState 字段（trip_id/plan_status/status_reason/status_history 截断 20 条/confirmed_version），复用 checkpoint，**新表 0 张**。CANCEL≠生命周期终态。

### 3.3 Plan Version 管理（v4 §3.2）

版本链已存在（`models/itinerary.py::stamp_version`，每次修改出新版不覆盖）。v4 冻结：change 由新旧版本**确定性 diff 派生**（禁手写，G2）；结构 `{version, parent_version, change:{added/removed/moved/brief_fields}, change_reason, quality}`；回滚=以旧版内容出新版本（type=rollback）永不删史；对比=纯函数 diff。边界如实声明：checkpoint TTL 7 天，跨会话持久审计账本（travel_plans 表）**本期不建**，trip_id 已备。

### 3.4 Evidence 模型（v4 §4）

`{fact_id, value, source, source_type: LIVE|RAG|CACHE|SEED|ESTIMATE, confidence, verified_at, expire_at}`。confidence 基线：LIVE=0.95/CACHE=0.85/RAG=0.7/SEED=0.5/ESTIMATE=0.5；缺 verified_at 上限 0.5（fail-fast 不变量）。三档消费口径（唯一）：trusted（LIVE 未过期）/ may_change（CACHE/过期）/ needs_confirmation（SEED/ESTIMATE/置信<0.6）。SEED 是对用户四类的诚实扩展（种子示例值不许冒充任何一类）。validator 的 SOURCE_STALE 检查消费 state 内 Evidence 字段（**零 IO 红线不变**）。

### 3.5 Tool Governance（v4 §5）

side_effect 四级 READ/COMPUTE/WRITE/TRANSACTION。规则：TRANSACTION 只经 Commerce Agent；requires_confirmation=True 必须先有审批单；fee_bearing 只出现在资金相关；Agent→Tool 白名单+守护测试。与 capabilities.yaml 边界：后者管主图 Skill 面（travel.poi_search 唯一注册项冻结），本表管域内治理面。

### 3.6 Human Approval（v4 §6）

`pending_approval`：type = PLAN_CHANGE / MUST_GO_CONFLICT / BUDGET_EXCEED / BOOKING / REFUND + reason + options[A/B]。裁决走 TravelPendingResolver 扩展档（approval_id 优先于现有 cancel>avoid_patch>补槽>new_run）；超时作废如实披露不默认选 A。booking_intent 并入此模型（修 D2 一处机制两用）。

### 3.7 评测与预算（v4 §9）

指标冻结：Requirement 槽位 ≥95%/人数 ≥99%/否定 ≥98%（clarification rate 只记基线不压低）；Research coverage/freshness 基线后冻结；Planning/Optimization 沿用 34 条金标（阈值已冻结不放松）；Assistant citation correctness + **hallucination=0 容忍**（无证据断言即违规）；Commerce 沿用 B1-B20+生产门 T1-T12=0。LLM 预算：规划链 ≤1（仅 Requirement，run 内硬上限+state 计数器，零重试），其余 0；Assistant ≤1/轮；`travel_llm_calls_total` 计数器进 CI 断言。时延：Tool 3-5s / Agent 10s（Research/Optimization 15s）/ 整图 30s deadline；审批等待不计入（跨轮状态）。

### 3.8 ProviderRouter（v4 §8）

选择链 primary→fallback→stale cache→七态失败；weather=[tencent,qweather]（FallbackWeatherProvider 现状为首个消费者，parity=qweather 15 用例）；map=[tencent_live, local_estimate]；poi=[seed]（唯一源，未来城市级 live 源追加链头）；price=[fake]。纪律：Router 只在 Provider 层；Agent/Tool 只拿统一 ProviderResult，**不知道背后是哪个 adapter**（Agent 报 capability，Router 决定供给）。

### 3.9 Migration Phase 0.5-8（v4 §10 执行卡）

| Phase | 内容 | 状态 |
|---|---|---|
| 0.5 可先行 | D1 ICS 修复+回归测试；.env TRAVEL_USE_LIVE_MAP 还原 true | **✅ 已实施** |
| 1 建边界（零行为） | core/{agent_base,contracts,events,supervisor action 枚举,plan_lifecycle,plan_diff}+Evidence/ToolSpec 契约+services/memory/evaluation 骨架+守护测试+四层规范口径更新 | **✅ 已实施（规范文档更新除外）** |
| 2 Requirement Agent | slot_filler git mv→planning/requirement_agent.py（shim）；D3/D4/D5 词表；adults/children；负偏好；LLM 二层框架（flag off）；memory/ 迁入；slot 评测集 ≥50 例 | 未开工 |
| 3 五专家 service 化+三 Agent 包裹 | experts git mv→services/{candidate_pool,skeleton,scheduler,adaptation,estimator,knowledge_notes,destination}；supervisor stage 重映射+agent_history 双写；validator 加 3 warning；deadline 30s+Agent 超时生效；travel.weather.query+route.optimizer(2-opt) Tool | 未开工 |
| 4 Provider 归位+数据契约位 | providers git mv→travel/providers/；TTL 调整；poi.detail Tool；events/notice 契约位（implemented=False）；城市级 POI 源 BLOCKED 登记；ProviderRouter（weather 首消费） | 未开工 |
| 5 Commerce 统一+D2 | commerce_agent（kind 参数）；BOOK 意图收编（flag）；booking_intent 挂起槽并入审批模型 | 未开工 |
| 6 Assistant Agent+报告面 | assistant_agent（QUERY 事实问答）；map.link；assistant_qa 评测集 | 未开工 |
| 7 Payment/Recovery 收口 | admin API 断头补齐（webhook 接收+IN_DOUBT 对账/人工裁决+审批门）；PRICE_CHANGED 钩子消费 | 未开工 |
| 8 E2E 收口 | 删 shim；死代码清理；全量回归+联网金标+实机走查；全指标基线冻结；文档口径 | 未开工 |

## §4 已实施明细（逐 commit）

### 4.1 `87b39fd` — Phase 0.5a：D1 修复（2 文件 +32/-2）

**缺陷**：`backend/app/api/routes/travel.py` ICS 导出把中文目的地直接内嵌 `Content-Disposition`，HTTP 头 latin-1 编码必抛异常 → 500。

**修复**（新增纯函数 + 端点改调用）：

```python
def ics_content_disposition(destination: str) -> str:
    """构造 RFC 6266 合规的 Content-Disposition。HTTP 头只能 latin-1 编码，
    中文目的地直接内嵌 filename 会 500（D1）。ASCII fallback 用固定名，
    真实文件名经 filename*=UTF-8'' 百分号编码携带。"""
    encoded = quote(destination, safe="")
    return f"attachment; filename=\"travel-plan.ics\"; filename*=UTF-8''{encoded}.ics"
```

**回归测试**（test_p0_mvp.py，强断言）：头值可 latin-1 编码（修复前此处抛 UnicodeEncodeError）+ `filename*=UTF-8''` 存在 + percent-decode 无损往返还原「杭州」。**结果：test_p0_mvp.py 14 passed**（13 存量 + 1 新增）。

### 4.2 Phase 0.5b — 环境还原

`.env:235` `TRAVEL_USE_LIVE_MAP=false`（09-28 实机测评遗留）→ 还原 `true`（.env gitignored，不入库）。自此地图路线恢复「腾讯 live 通道 + 本地估算兜底」主链。

### 4.3 `63710d5` — 设计链 6 文档入库（1697 行）

审计 / v1 设计 / 合并评审稿 / v2 映射 / v3 / v4 冻结稿。防并行会话收编，固化设计基线。

### 4.4 `3077c76` — Phase 1 建边界（17 文件 +1180，纯新增零行为）

| 文件 | 内容 | 对应冻结条目 |
|---|---|---|
| `travel/core/__init__.py` | 底座包声明（禁止反向依赖业务模块） | v3 §1 |
| `travel/core/contracts.py`（105 行） | SourceType 五档 + CONFIDENCE_BASELINE + TravelContext（frozen）+ Evidence（构造期 fail-fast 两条不变量 + evidence_level() 三档口径） | v4 §4/§5 |
| `travel/core/agent_base.py`（96 行） | 7 规范名常量 + AgentSpec(name/description/timeout_s/degradation) + AGENT_SPECS 全 7 项（超时与降级口径=v3 §3 表代码化） | v3 §3 |
| `travel/core/actions.py`（35 行） | SupervisorAction 五枚举 + ENTRY_ACTION_MAPPING（现有 5 入口等价归入，零行为；BOOK/QUERY 登记追加时点） | v3 §5.3 |
| `travel/core/plan_lifecycle.py`（92 行） | TravelPlanStatus 10 态 + RESERVED_STATUSES + LEGAL_TRANSITIONS 迁移表 + transition() fail-fast + IllegalPlanTransition + history_entry()（时钟可注入） | v4 §3.1 |
| `travel/core/plan_diff.py`（98 行） | poi_placements 提取 + diff_poi_placements（排序确定性）+ build_change_record（diff 派生禁手写）+ rollback_record（永不删史） | v4 §3.2 |
| `travel/core/events.py`（31 行） | build/emit_travel_event（JSON 可序列化 default=str 兜底） | v3 §2 |
| `travel/tools/__init__.py` + `travel/tools/spec.py`（220 行） | SideEffect 四级 + ToolSpec + TOOL_SPECS 20 项（含 TRANSACTION 3 项与契约位 5 项）+ TOOL_SPECS_BY_NAME | v4 §5 |
| `travel/services/` `travel/memory/` `travel/evaluation/` `__init__.py` ×3 | 包骨架（各自 docstring 写明 Phase 落点） | v3 §2.1/§7、v4 §9 |
| `tests/travel/test_plan_lifecycle.py` | 16 例：全链/自环/三类修改重入/确认重入/8 非法迁移 parametrize/保留态链/表完整性/history 形状与时钟注入 | v4 §3.1 |
| `tests/travel/test_plan_diff.py` | 8 例：提取/空行程/diff 三类/确定性（同输入同输出+排序）/首版/派生非手写（「不去迪士尼」示例）/JSON 可序列化/回滚 | v4 §3.2 |
| `tests/travel/test_tool_governance.py` | 16 例：唯一名/必填字段/白名单 actor 合法性/TRANSACTION 只经 commerce+必须确认+必须 fee_bearing/确认门只挂 TRANSACTION/fee_bearing 只挂 TRANSACTION/关键工具在位/side_effect 枚举冻结/契约位 5 项登记 | v4 §5 |
| `tests/travel/test_core_contracts.py` | 17 例：Context frozen/基线表冻结/超基线 parametrize raise/未核实 0.5 上限/JSON 往返/三档口径 6 断言（LIVE fresh=trusted、LIVE 过期=may_change、CACHE=may_change、SEED/ESTIMATE=needs_confirmation、低置信=needs_confirmation）/事件形状与不可序列化对象兜底 | v4 §4 |
| `tests/travel/test_travel_core_boundary.py` | 4 例：core 文件集锁定（新增文件必须有意更新测试）+ **planning.py 遮蔽防护** + experts/base 生产资产仍在 + 新包可导入 | Phase 1 边界 |

**回归门结果（全部实跑）**：

| 门 | 范围 | 结果 |
|---|---|---|
| 新增守护 | 5 文件 61 例 | **61 passed** |
| 一致性三门 | test_registry_consistency + test_layer_consistency + test_adr0001_dual_registry_merge | **37 passed** |
| node_runtime 契约门（第四门） | tests/node_runtime/（STOP F 冻结公开契约） | **40 passed** |
| 金标+代表性分块 | test_quality_golden + test_travel_graph + test_validator + test_versioning + test_planning_contract | **106 passed**（含金标 34 条阈值门） |

## §5 计划 ↔ 实施对照矩阵

| v4 冻结条目 | 状态 | 证据 |
|---|---|---|
| D1 ICS 修复 | ✅ | 87b39fd + 14 passed |
| TRAVEL_USE_LIVE_MAP 还原 | ✅ | .env:235（不入库） |
| Evidence 模型 + confidence 基线 + 三档口径 | ✅ 契约冻结 | core/contracts.py + 17 例 |
| 生命周期 10 态 + 迁移表 fail-fast | ✅ 契约冻结（未接线 stage） | core/plan_lifecycle.py + 16 例 |
| PlanChangeRecord 确定性 diff + 回滚 | ✅ 契约冻结（未接 stamp_version） | core/plan_diff.py + 8 例 |
| ToolSpec 四级 + TRANSACTION 只经 Commerce + 白名单 | ✅ 契约冻结（Agent 未接线） | tools/spec.py + 16 例 |
| SupervisorAction 枚举 + 入口映射 | ✅ 零行为立枚举 | core/actions.py |
| AgentSpec 超时/降级口径代码化 | ✅ 规格冻结（run_expert_safely 未升格） | core/agent_base.py |
| 统一事件出口 | ✅ 骨架（Prometheus 面 Phase 3 收编） | core/events.py |
| services/memory/evaluation 包 | ✅ 骨架（Phase 2/3 填充） | 3 × __init__ |
| HumanApproval / pending_approval state 字段 | ◐ 契约在 v4 文档冻结，代码字段未落（随 Phase 3/5 flag 组） | v4 §6 |
| plan_status 等新 state 字段 | ◐ 定义待 Phase 3（flag TRAVEL_SESSION_STATE_V2 组） | v4 §3.1 |
| slot 评测集 95/99/98 | ❌ Phase 2 | — |
| LLM 二层（flag off）+ run 内硬上限 | ❌ Phase 2 | — |
| 五专家 service 化 + 三 Agent 节点 + stage 重映射 | ❌ Phase 3 | — |
| validator 3 项新 warning | ❌ Phase 3（POI_UNVERIFIED/SOURCE_STALE/PREFERENCE_VIOLATION） | — |
| Provider 归位 + ProviderRouter + TTL | ❌ Phase 4 | — |
| Commerce kind 参数 + D2 修复 | ❌ Phase 5 | — |
| Assistant Agent + assistant_qa 评测 | ❌ Phase 6 | — |
| admin API 断头补齐（webhook/对账/裁决） | ❌ Phase 7 | — |
| shim 删除 + 全量收口 | ❌ Phase 8 | — |

## §6 实施偏差与裁决（诚实披露）

1. **planning/ 包推迟建（最重要的偏差）**。v3/v4 计划 Phase 1 建 `travel/planning/` 包骨架；实施时发现 `travel/planning.py` 模块已存在（must_go 契约，names_match 唯一事实源，test_planning_contract.py 依赖）——Python 包优先于同名模块，先建包会**静默遮蔽**炸掉存量 import。裁决：planning/ 包推迟到 Phase 2 与 slot_filler git mv 同 commit 建立（含 shim），并把约束固化进守护测试（`test_travel_core_boundary.py` 显式断言包不存在 + 存量模块仍在）。
2. **action 枚举落在 `core/actions.py` 而非 v3 卡片写的 `core/supervisor.py`**。避免与 Phase 3 的 supervisor.py git mv 目标路径冲突；Phase 3 落地时 core/supervisor.py import core/actions。
3. **SourceType 增加 SEED 档（用户四类 +1）**。种子数据坐标/票价是自声明示例值，归入 LIVE/CACHE/ESTIMATE 任何一类都会虚高信任；v4 §4 已注明并经 Evidence 测试钉住（SEED→needs_confirmation）。
4. **治理面 actor 集合 = 7 Agent + 门禁节点 reporter**。calendar.export/map.link 的产出者是门禁层 reporter（非 Agent），ToolSpec 白名单把门禁节点纳入 actor 枚举；守护测试断言所有 allowed_agents ⊆ 7 Agent + reporter。
5. **TOOL_SPECS 含契约位工具（无实现先登记规格）**：hotel.book/flight.book/hotel.cancel 三项 TRANSACTION 与 ticket/event/notice/train/currency 五项契约位，规格先行（BLOCKED 纪律：登记不实现、不假装可用），实现等真实数据源。
6. **回归门分块执行而非全量**：宿主机 travel 区全量 pytest 有挂死前科（历史实测），且并行会话正在提交；本轮跑了一致性/契约门/金标/五个代表性分块（共 250 例），**全量 544 分块回归排在 Phase 2 开工前**。此为如实披露，不是门禁放宽。

## §7 约束自评（用户禁止事项逐条）

| 禁止 | 自评 | 证据 |
|---|---|---|
| 删除 validator/repair/provider/booking 核心代码 | **零触碰**：Phase 1 提交 diff 仅新增文件 + D1 两文件（路由头构造+测试），validator.py/repair.py/providers/booking/ 无一行改动 | `git show --stat 3077c76`（17 文件全为 create mode）+ 87b39fd（2 文件） |
| 大规模推倒重写 | 存量代码零修改；新增 1180 行全部为契约/规格/测试；图结构未动（节点接线 Phase 3 起）；五专家算法本体将在 Phase 3 经 git mv + 函数级搬运（承诺算法零改动，届时可核 diff） | 同上 |
| G1-G4 | 新增 travel/tools/spec.py 为域内治理面，与 capabilities.yaml（主图 Skill 面）不重复不冲突（G2 边界已在两处 docstring 声明）；未注册任何新主图 capability；无集中代注册（G3）；例外全部登记于 v4 文档遗留清单（G4） | tools/spec.py、v4 §附录 |
| checkpoint 纪律 | Phase 1 未触碰 state（新字段全在契约文档层）；plan_lifecycle/plan_diff 输出一律 JSON 可序列化 dict（测试断言） | 测试 json roundtrip 用例 |
| 多会话纪律 | 全部提交 pathspec 双重限定；实施期间并行会话落了 STOP H（b93bd10）等 4 commit，pathspec 隔离零冲突 | git log 提交链 |

## §8 未做与阻塞（如实）

- **Phase 2-8 全部未开工**（§3.9 表）。
- 生命周期 TRAVELING/COMPLETED/ARCHIVED 三保留态本期不激活（无行程时钟基础设施）；plan_status 未接 stage 推进（Phase 3）。
- 跨会话持久 plan 账本（travel_plans 表）本期不建（checkpoint TTL 7 天边界已声明；trip_id 主键已备，立项零返工）。
- 真实数据源四件套 BLOCKED（城市级 POI 候选源 / event / notice / 真实酒店机票供应商）——采购事项，重构不背；对应工具全部「契约位 + implemented=False」。
- LLM 二层、slot/assistant_qa 评测集、Admin API 断头（webhook/对账/人工裁决无 HTTP 入口，IN_DOUBT 闭环生产不可达）均待对应 Phase。
- D2-D7 缺陷未修（D2 随 Phase 5、D6 随 Phase 3、D7 独立待排）。

## §9 请评审者重点确认的八个问题

1. **Evidence confidence 基线值**（LIVE=0.95/CACHE=0.85/RAG=0.7/SEED=0.5/ESTIMATE=0.5，缺核实上限 0.5）：数值与三档映射是否合理？SEED 扩展档是否认可？
2. **生命周期迁移表**：WAITING_CONFIRMATION 允许三个修改重入点（RESEARCHING/PLANNING/OPTIMIZING）是否过宽？CONFIRMED→WAITING_CONFIRMATION 的"重新待确认"语义是否符合企业审批直觉？
3. **保留态决策**：TRAVELING/COMPLETED/ARCHIVED 本期冻结枚举不激活（无时钟基础设施）——是正确的克制还是应该现在就设计时钟？
4. **持久化边界**：lifecycle/版本链放 checkpoint（7 天 TTL）+ 持久账本缓建，对"企业审计"要求是否足够？账本立项触发条件（"列出我的行程"产品需求）是否合理？
5. **Tool Governance 粒度**：side_effect 四级 + 白名单静态守护（import 面断言）是否足够防越权？是否需要运行时拦截层？
6. **LLM 预算 ≤1 硬上限**：规则层永远兜底 + LLM 仅补缺失槽——是否过紧导致理解层提升有限？（用户目标：自然语言人数/节奏/偏好全覆盖 vs 确定性优先）
7. **planning.py 遮蔽规避**：包推迟 + 守护测试固化的方案，vs 立即迁移 planning.py 进包——哪个风险更低？
8. **偏差披露完整性**：§6 六条偏差是否有遗漏或避重就轻？§5 矩阵中 ◐ 项（契约已冻结、代码未落）的划法是否诚实？

## 附录：提交链（评估时点）

```
03a8eb3 docs: reduce CLAUDE.md to thin pointer at AGENTS.md   ← 设计基线
87b39fd fix(travel): ICS Content-Disposition RFC 6266（D1）      ← Phase 0.5a
63710d5 docs(travel): 设计链 6 文档（审计/v1/合并/v2/v3/v4）      ← 设计冻结
3077c76 feat(travel): Phase 1 建边界（17 文件 +1180，零行为）    ← Phase 1
（其间并行会话提交：270e6dd/c44d3af/b93bd10 等，pathspec 隔离无交叉）
```

# TRAVEL_ARCHITECTURE_AUDIT.md — 旅游规划域架构审计报告（Phase 0）

> 任务书：旅游规划 Agent 企业级增强改造任务书（§2 Phase 0）
> 审计日期：2026-09-17 ｜ 审计方式：**只读**，未修改任何业务代码
> 审计人：WorkBuddy（基于当日三轮实测 + 全量源码精读，所有「实测」结论均可指到文件与行号）

---

## 0. 审计范围与方法

**扫描范围**：`backend/travel/`（全量精读）、`backend/orchestration/`（domain_registry / domain_graph / travel_graph_node / travel_prefilter / checkpoint）、`backend/tools/travel/`（poi / cost / routing / live_map / poi_seed）、`backend/config/travel.py`、`backend/evaluation/`（结构 + 数据集格式 + gate）、`backend/observability/`（tracer / 存储链路）、`backend/tests/travel/`、`backend/services/`。

**目录存在性核查**：任务书要求审计的 `providers/` **不存在**（`backend/providers` 无此目录）——数据 Provider 层目前没有独立承载模块，Phase 1 需新建。

**Git 状态声明**（多会话协作环境）：
- 工作区有未提交改动：`backend/travel/slot_filler.py`、`backend/travel/reporter.py`、`backend/tests/travel/test_slot_filler.py`（本会话当日修复）；另有 `backend/selection_funnel/*`、`frontend-admin/*` 等其他会话的未提交改动。
- 本审计阶段不提交、不修改任何代码；Phase 1 开工前建议先将当日 travel 修复以路径限定方式提交（`git commit -- backend/travel backend/tests/travel`），避免被并行会话收编。

**当日实测基线**（审计证据的一部分）：
- travel 全套 pytest **131 passed**（含当日新增 20 个用例）。
- e2e 实测：全链 13 步 ≈1s；追问分支、BUDGET_OVER 修复、闭馆日必去项保留、跨轮改单（指纹失效重排）、换城市重排均正确。
- 当日发现并修复 6 类缺陷（§6 详列），全部有回归测试。

---

## 1. 总体结论

当前实现是一个**纪律性很强的 P0**：域隔离、纯规则调度、不猜不伪造、护栏优先、必去项不可自动删——任务书 §19/§20 的原则**大都已经落地**，且部分 P0 项（repair_stalled、来源标注、指纹失效重排、prefilter）已有可用雏形。主要缺口集中在五处：

1. **数据事实无时效语义**：`Poi.source` 存在但无 `observed_at / valid_until / verification_status / is_estimate`，腾讯补全 POI 的营业时间是默认占位值（`live_map.py` resolve_place），这是当前最大的可信度风险点。
2. **版本管理只有需求指纹**：无 brief_version / data_snapshot_version / plan_version 三层版本，产物无法回答「属于哪个需求、基于哪份数据」。
3. **Persistence 降级不可见**：Postgres 失败静默降级 MemorySaver，仅日志告警，无 `persistence_status` 上浮。
4. **评测缺旅游数据集**：evaluation 体系成熟（5 个 ModuleKind + gate 分层阈值），但没有 travel 数据集与 runner。
5. **无 USER_DECISION 中断**：必去项冲突目前「保留 + 文字提示」，未走 LangGraph interrupt 真正暂停等用户决策。

**明确不建议做的**（遵守任务书 §20）：不新增任何 Agent/Planner/Critic；不引入 LLM 抽槽位（现有规则层 45/45 fuzz 全过，LLM 只会破坏可复现性）；不现在实现 Booking 真实调用。

---

## 2. 核心审计表（任务书 §2 要求格式）

| 模块 | 当前实现 | 企业级风险 | 改造建议 | 优先级 |
| ---- | -------- | --------- | -------- | ---- |
| **POI** | 种子名录（`poi_seed.py`，福州/厦门/杭州）+ 腾讯点名补全（`live_map.resolve_place` :208-268）；`Poi.source` 默认 `seed:local`（`models/poi.py:45`） | 补全 POI 的营业时间/票价是**默认占位值**却参与硬校验与预算；无 `observed_at/valid_until`，数据新鲜度不可判定 | 建 `POIProvider` Protocol + `POIFact`（source/observed_at/valid_until/verification_status/is_estimate）；占位字段降级为 `unverified` 并进 reporter 披露；禁止 LLM 补营业时间/价格 | P0 |
| **Transit** | `routing.estimate_leg`（:102-129）优先注入的 `live_leg`（腾讯 direction，15min 缓存，失败返 None），回落直线×绕行系数；来源 `tencent:lbs / estimate:local`（routing.py:38-39） | 无 `traffic_aware/fallback_reason/observed_at`；**远期出行日期套用当日实时路况**是隐性伪事实 | `TransitProvider` + `TransitResult`（mode/duration/distance/departure/traffic_aware/source/observed_at/is_estimate/fallback_reason）；出行日期距今天 >N 天强制 estimate + 提示出发前重算 | P0 |
| **Planning（版本）** | 仅 `brief_fingerprint`（SHA1，`graph_state.py:174-192`）用于变更检测；`Itinerary` 无任何版本字段（`models/itinerary.py:92-107`） | 无法回答「这版行程基于哪个需求、哪份数据」；跨轮产物可复用性无判据 | 增加 `brief_version/data_snapshot_version/plan_version` + 状态机（draft/planning/needs_clarification/needs_user_decision/validating/ready/degraded/failed）+ `parent_plan_version/changed_fields` | P0 |
| **Repair** | `repair_stalled` 终态标记（`repair.py:332-350`）+ 轮数上限 + `MAX_STEPS` + recursion_limit 四层防循环；必去项 `kept_required` 保留（:182-187） | **无震荡检测签名**：无法识别「约束集不变/行程不变」的原地打转（目前仅靠 ≤2 轮兜底）；无 before/after 对比记录 | 增加 `constraint_signature / plan_signature` before/after，连续两轮无改善即 stall；签名入 `repair_log` | P0 |
| **Persistence** | PostgresSaver（autocommit+setup）→ 失败降级 MemorySaver 仅 logger.warning（`graph_builder.py:150-163`）；TTL 清理守护 24h 单例（`orchestration/graph/checkpointer_cleanup.py`） | 降级**不可见**：reporter/trace 不知道 `persistence_degraded`，多 worker 各存一份内存态时跨轮改单会静默失效 | `persistence_status`（healthy/degraded/disabled）写入状态与 trace；策略开关要求强持久化时降级应拒绝进入多轮规划 | P0 |
| **Booking** | 无任何实现（正确——P0 不该有） | 无业务状态语义，未来易出现「生成行程=已预订」 | 仅预留 `BookingProvider` Protocol + 行程项 `booking_status` 枚举（recommended/…/booked），数据模型先行 | P1 |
| **Evaluation** | evaluation 体系成熟：5 个 ModuleKind（`evaluation/models.py:10`）、manifest+cases.jsonl 数据集、gate 分层阈值（smoke 0.95/core 0.85/hard 0.70/regression 1.00）、基线对比 | **无 travel 数据集与 runner**：当日 45 条 fuzz 用例是开发自测，不可作为持续回归门禁 | 扩 ModuleKind + `datasets/travel/` + `runners/travel.py`；按任务书 §15 六类（Slot/Planning/Validation/Repair/Multi-turn/Failure）建用例 | P0 |

---

## 3. 逐模块详细审计

### 3.1 数据层：POI / Transit / 来源标注

**现状事实**
- `Poi` 有 `source`（默认 `seed:local`），**无** `observed_at / valid_until / verification_status / is_estimate`（`models/poi.py:26-45`）。
- `TransitLeg` 有 `source`（`models/itinerary.py:57`）；`routing.py:38-39` 定义两种来源标识；`estimate_leg` 异常/None 时回落直线估算（:102-129）；`live_leg` 腾讯 direction + 15 分钟缓存，失败返 None（`live_map.py:126-174`）。
- `live_map.resolve_place`（:208-268）腾讯 POI 补全，**营业时间使用默认占位值**；有 120km 安全半径（:41）。`resolve_missing_places`（:271-307）对 must_go 缺失补池，`required=True`。
- reporter 有来源人话映射 `_SOURCE_LABELS`（reporter.py:24-45，含「未登记来源」兜底）并在行程单输出「数据来源」节。

**风险（按严重度）**
1. 【高】腾讯补全 POI 的营业时间是占位值，但它会进入 `check_time` 硬校验与排程——「用假开放时间排行程再一本正经地校验」是自欺链路。
2. 【中】`estimate:local` 与 `tencent:lbs` 无时间戳，无法判断「这趟实时路线对三个月后的出行日还有没有意义」。
3. 【中】`TransitLeg` 无 `fallback_reason`：降级发生了但用户不知道为什么。

**改造落点**：`models/poi.py` 加字段（向后兼容默认值）→ `live_map.resolve_place` 标 `verification_status="unverified"` → routing 返回结构升级 → reporter 分维度披露。**不改专家编排逻辑**。

### 3.2 版本管理（§4）

**现状**：只有 `brief_fingerprint`（SHA1 12 位，只含影响排程的字段，`graph_state.py:174-192`）+ `planning_reset()`（:195-223）。`Itinerary` 无版本字段；`Itinerary` 生命周期只有 `repair_rounds` 计数。

**差距**：§4 要求的三层版本（brief/data_snapshot/plan）全部缺失；无 plan 状态机；无 parent_plan_version/change_reason/changed_fields。当前「为什么和上一版不一样」只能靠 repair_log 与 notes 文字说明。

**改造落点**：`TravelBrief` 加 `version` 递增；`Itinerary` 加 `plan_version/parent_plan_version/brief_version/data_snapshot_version/created_at/change_reason/changed_fields/status`；supervisor 产物可复用性判断改为「brief_version 匹配且数据未过期」。指纹保留作为变更检测的快速通道，版本号承担追溯。

### 3.3 Supervisor（§5）

**现状**：`decide()` 纯函数（`supervisor.py`），只认状态事实——已实现：expert_history、candidates 数、itinerary 有无、validation 结果、repair_rounds、repair_stalled、step_count 七类事实；四条护栏（MAX_STEPS / 修复轮数 / repair_stalled / 专家失败无产物直接 report）。**已达标「不认上游声明的 stage」**（repair 分支曾因违反此原则死循环，已在 :332-350 修复并有注释存档）。

**差距**：① 决策已是 dataclass（stage+reason），但无 `action` 字段语义（任务书示例 `action="run_transit"`）；② **无法检查「产物属于哪个 brief_version / 是否过期」**——这不是 supervisor 的问题，是版本字段不存在（3.2 的下游）；③ 阶段机穷举测试已有（test_travel_graph.py::TestSupervisorDecide 穷举各分支），但未覆盖「provider degraded / persistence degraded」两个新事实源。

**改造落点**：decision 增加 action 命名 + 可复用性检查（依赖 §4 落地）；穷举测试补两个降级分支。

### 3.4 Repair 防震荡（§6）

**现状**：`repair_stalled`（本轮无可自动执行动作，repair.py:332-350，含实测死循环案例注释）；`MAX_REPAIR_ROUNDS=2`（:317-326 轮数用尽如实披露）；`MAX_STEPS=12` + `TRAVEL_GRAPH_RECURSION_LIMIT=max(25, 2×12+10)=34`（config/travel.py，注释记录过「护栏等不到生效」的修正）。**任务书列的四个保留项全部存在。**

**差距**：无 `constraint_signature_before/after` 与 `plan_signature_before/after`——目前判断「无动作」靠 `repair_itinerary` 返回 None，但**有动作却无效**（丢了 A 又触发同等级违反，约束集不收敛）的场景只能靠 ≤2 轮硬截断，且截断后用户不知道「修了但没修好」与「没法修」的区别（现在都笼统归入提示文案）。

**改造落点**：repair_node 内计算修复前后签名（约束码集合 + 行程 POI 序列哈希），写入 `repair_log`；连续两轮签名不变 → `repair_stalled=True` + 区分文案「修复无改善」。

### 3.5 Validator 约束分级（§7）

**现状**：5 轴 9 码（time/geo/pace/budget/coverage，`validator.py:51`），error/warning 分治，`check_coverage` 的 MUST_GO_MISSING 是 warning（:333-352）；纯函数零 IO（:10-17 三条硬纪律）。**§7 的 HARD/SOFT 语义已隐式存在**：error≈HARD、warning≈SOFT。

**差距**：① 无显式 `USER_DECISION` 层级——「必去项闭馆」目前是 error + repair `kept_required` 保留 + 文字提示（实测 E07 行为正确），但语义上它是「必须由用户裁决的约束」，不该与普通 HARD error 同列，也不触发任何暂停；② 无 `needs_user_decision` 状态（依赖 §4 状态机与 §13 interrupt）。

**改造落点**：`Violations` 增加 `decision_required` 字段或第三层级；repair 遇必去冲突时不再只写 note，而是产出结构化 decision 请求（选项：改日期/删其他点/保留冲突）。

### 3.6 Confidence（§8）

**现状**：`compute_confidence`（validator.py:358-373）= 1 − 0.15×errors − 0.05×warnings − 0.20×有种子数据 − 0.05×无日期 − 0.05×修复轮数。**已经不是 validator_pass_rate 单因子**（含数据来源与日期完备度），reporter 也明示「非模型自评」（reporter.py:188-194）。

**差距**：仍是一个**单一数字**，无法分别回答「约束过没过」和「数据靠不靠谱」——实测中 E06（seed 数据+超预算）0.65 与 E07（闭馆日冲突）0.65 同分不同因。缺 `data_freshness / source_coverage / execution_feasibility` 维度。

**改造落点**：拆成结构化四维对象（各维独立可核对），reporter 展示四行而非一个百分比；保留总分作排序用，但标注为派生值。

### 3.7 Transit（§9）

**现状**：`live_leg` + 缓存 + 回落（见 3.1）；`TransitLeg` 有 mode/minutes/distance_km/cost_cny/source。远期日期问题：**无任何处理**——9 月排元旦行程用的仍是 9 月的实时路况。

**改造落点**：`TransitResult` 结构 + `trip_date` 距今阈值策略（估算 + 提示 + 建议出发前重算）。这是纯增量改造，`estimate_leg` 注入机制（register.py 幂等安装）保留不动。

### 3.8 Persistence（§10）

**现状**：`_build_checkpointer` 三级降级（graph_builder.py:122-163）：postgres → 失败 warning + MemorySaver → 再失败裸跑。TTL 清理守护与主图/客服域共享（全进程单例）。`.env` 当前 TRAVEL_CHECKPOINTER_ENABLED=true、backend=postgres。

**差距**：降级后 `persistence_status` 无处存在——state、trace、reporter 都不知道。任务书要求的生产语义（degraded 可见 / 强持久化策略下拒绝多轮）完全缺失。**风险实锤**：多 worker 部署时 MemorySaver 各存一份，第二轮用户可能被路由到另一个 worker 导致「跨轮改单静默失效」，用户看到与上一轮无关的新行程——这正是 §4 里 fingerprint 机制防的问题，但降级时防线只剩 fingerprint 的「changed=False → 重放旧行程」，行为正确但原因不可见。

**改造落点**：checkpointer 工厂返回 `(checkpointer, status)`；status 进 state/trace/notes；新增 `TRAVEL_REQUIRE_PERSISTENCE` 策略开关。

### 3.9 Run / Trace（§11）

**现状**：tracer 体系完整（TraceRecord 有 tags/metadata/metrics，Span 有 status/retry_count/metrics；异步入队 trace_writer + SQLite/PG 存储，MAX_TRACES=200）。旅游域已接：`travel_graph_node._stamp_execution_tags`（travel_status/travel_destination tags + travel_validation/travel_confidence metadata，:70-101）；validator 每轴独立 span（:75-112）。

**差距**：① 无 `TravelPlanRun` 运行级实体（run_id/current_node/retry_count/failure_reason）；② 无 `TravelToolCall` 记录——LBS 调用**未建 span**（专家执行也只有 duration_ms 日志，与 validator 不一致），provider 延迟/失败率无法归因；③ 大响应处理：live_map 有 15min 进程内缓存，无 snapshot 外置——P0 可接受，标注为 P1。

**改造落点**：复用现有 tracer（不新建体系）：专家与 provider 调用统一 start_span；TravelPlanRun 以 trace tags + metadata 承载（或独立轻表），避免把地图大对象塞进 LangGraph State。

### 3.10 Booking（§12）/ 3.11 User Decision & Interrupt（§13）/ 3.12 Candidate Plans（§14）

- **Booking**：全项目无任何预订语义（正确）。P1 仅落 Protocol + `booking_status` 枚举 + 幂等键字段设计，不接供应商。
- **Interrupt**：checkpointer 已具备（Postgres，含 TTL），**LangGraph interrupt/Command 未使用**。当前「必去项冲突」是保留+提示的软处理。改造时注意：中断状态天然随 checkpointer 持久化，但**用户决策必须结构化存状态**（不可依赖聊天记录重推）；恢复路径走 `Command → slot_filler → 需求版本变化 → 重排`，与现有指纹失效机制天然兼容。
- **Candidate Plans**：纯数据结构预留（`candidate_plans` 列表 + objective/cost/time/violations/quality），P0 单方案不变。明确不做优化算法、不加 Planner Agent。

### 3.13 Evaluation（§15/§16）

**现状**：evaluation 框架能力齐全——`ModuleKind`（models.py:10，Literal["planner","rag","cs","sql","e2e"]）、数据集目录规范（manifest.json + cases.jsonl）、gate 分层阈值、基线对比（data/baselines）、5 个 runner 注册模式（runner 末尾 `register_runner`）。RAG 36 例评测基线已有（Recall@5 0.9394 等，另见交接文档）。

**差距**：travel 缺席。需要：扩枚举 + `datasets/travel/`（建议按任务书 §15 的 A-F 六组组织 cases，metadata 带 tier）+ `runners/travel.py`（调子图断言 final_answer/validation codes/repair 行为）。**Failure 组（Provider 失败/PG 不可用/stalled）需要可注入的假 Provider**——这也是 Phase 1 Provider 接口的测试红利。

### 3.14 测试现状（§17）

**现状**：`backend/tests/travel/` 5 个文件 131 用例（checkpointer/repair/slot_filler/travel_graph[含 supervisor 穷举]/validator），conftest 有工厂函数。当日新增 20 例（连接词捕获/复合数字/口语人数/merge 冲突/城市过滤/区间/不支持城市）。**无 `except: pass` 吞异常**（validator._run_axis 异常重抛；run_expert_safely 异常分类为 failed）。

**差距**：§17 要求的 8 个测试文件中 5 个缺失（versioning/provider/persistence/multiturn/evaluation）；`test_travel_supervisor.py` 的内容现位于 test_travel_graph.py（可保留，不必为凑文件名拆分）；异常**分类**粒度粗（只有一个 FAILED，无 error_code——`TravelExpertResult` 有 error 字符串字段但无分类枚举）。

---

## 4. 任务书 P0/P1 条目 × 现状映射总表

| 任务书条款 | 现状 | 判定 |
|-----------|------|------|
| §3 Provider 抽象 + 事实时效字段 | source 有；observed_at/valid_until/verification_status/is_estimate 无；providers/ 目录不存在 | **部分** |
| §4 三层版本 + plan 状态机 | 仅 brief_fingerprint | **缺失** |
| §5 Supervisor 事实检查 + 结构化决策 | 7 类事实检查已有；action 字段与版本/时效检查缺（依赖 §4） | **部分（较好）** |
| §6 Repair 签名防震荡 | 四个保留护栏全部存在；签名机制无 | **部分** |
| §7 HARD/SOFT/USER_DECISION | HARD/SOFT 隐式存在（error/warning）；USER_DECISION 无结构化承载 | **部分** |
| §8 Confidence 拆维 | 复合计算已有但单数字 | **部分** |
| §9 TransitResult 增强 | source 有；traffic_aware/fallback_reason/远期日期策略无 | **部分** |
| §10 persistence_status | 三级降级有；状态可见性无 | **部分** |
| §11 TravelRun/ToolCall | trace 框架在，旅游域仅 tags/metadata 级接入；无 run 实体与 provider span | **部分** |
| §12 Booking Adapter | 无（P1 按任务书只做接口） | **缺失（按计划）** |
| §13 interrupt / 结构化用户决策 | checkpointer 具备；interrupt 未用 | **缺失（P1）** |
| §14 candidate_plans 数据结构 | 无 | **缺失（P1，仅建模）** |
| §15 Travel Evaluation Dataset | 框架在，travel 数据集/runner 无 | **缺失** |
| §16 验收指标 | 部分可测（见 §6 基线）；槽位准确率无验收集不可测 | **部分** |

---

## 5. 当日实测修复记录（审计可信度上下文）

以下缺陷在审计当日已被发现并修复（均有回归测试，改动未提交）：
1. 触发词捕获半截话进清单（「福州玩」「地方很多」「人多拥挤的地方」）→ `_clean_captured_name` 清洗器
2. 复合中文数字被劈开（「十二天」→2）→ `_CN_COMPOUND`
3. 口语人数不识别（一家三口/带爸妈/和女朋友）→ 三层兜底
4. 跨轮 merge：必去被拉黑后仍留在 must_go → avoid 优先过滤
5. 区间天数/猜测人数无感知 → 透明化 notes
6. 城市名进 must_go 产生假警告 → `_filter_city_names`（fresh + merge 双层）

另有两个**设计内正确行为**经实测确认（勿当缺陷修）：极低预算 stall 后如实披露；必去项闭馆保留 + 醒目警告 + 置信度降级。

---

## 6. 验收指标基线（§16 现状可测值）

| 指标 | 当前状态 | 测量方式 |
|------|---------|---------|
| 核心槽位准确率 ≥95% | **不可测**（45/45 fuzz 为开发用例，非独立验收集） | Phase 5 建 dataset 后测定 |
| 硬约束违反率 = 0 | **定义需修正**：stall 时仍交付带 error 行程（如实披露）。建议改为「未披露的硬约束违反 = 0」——当前 = 0（errors 全部进「需要你确认」） | e2e 断言 final_answer 含 violations 文案 ⇔ validation 非空 |
| 未经授权删除必去项 = 0 | **已满足**（repair.py:182-187 kept_required 路径） | 现有 test_repair + e2e E07 |
| repair 无限循环 = 0 | **已满足**（四层护栏） | 现有测试 + recursion_limit |
| 需求变更错误复用 = 0 | **已满足**（指纹失效 + planning_reset；M01-M05 实测） | multiturn 数据集后进 gate |
| 关键事实无来源 = 0 | 基本满足（source 全覆盖），缺时效维度 | 加 observed_at 后补断言 |
| 虚构实时数据 = 0 | 最大风险点 = resolve_place 占位营业时间（live_map.py:208-268） | verification_status 落地后断言 |
| Provider 故障无降级说明 = 0 | routing 回落有来源标注；缺 fallback_reason | TransitResult 落地后补 |
| 持久化恢复失败 = 0 | test_checkpointer 存在；degraded 不可见 | persistence_status 落地后补 |

---

## 7. Phase 1-7 落点与执行建议

| Phase | 内容 | 主要落点 | 备注 |
|-------|------|---------|------|
| 1 | Provider 层 | 新建 `backend/providers/travel/`（Protocol + POIFact/TransitResult + 现有 seed/live_map 包装为 Provider 实现） | 纯增量，专家编排不动 |
| 2 | 版本管理 | `models/brief.py`+`models/itinerary.py` 加字段、`graph_state.py` 版本逻辑、supervisor 复用性检查 | 与指纹共存 |
| 3 | Supervisor/Repair/Validator 增强 | decision.action、repair 签名、validator 第三层级 | 纯规则不变 |
| 4 | Persistence + Run Trace | checkpointer 工厂返状态、专家/provider span、TravelPlanRun | 复用 tracer 不新建 |
| 5 | Travel Dataset | evaluation/models.py 扩枚举 + datasets/travel + runners/travel.py | Failure 组需假 Provider（吃 Phase 1 红利） |
| 6 | User Decision interrupt | validator decision_required → interrupt → Command 恢复 | 依赖 checkpointer（已具备） |
| 7 | Booking/Candidate 预留 | 纯 Protocol + 枚举 + 空数据结构 | 不接真实供应商 |

**执行纪律确认**：每 Phase 走「改 → 单测 → 集成 → 真实样例 → 验收 → 下一阶段」；开工前先提交当日 travel 修复（路径限定）；并发 pytest 期间不动 backend 代码（协作纪律）。

---

*审计结束。Phase 1 待批准后开工。*

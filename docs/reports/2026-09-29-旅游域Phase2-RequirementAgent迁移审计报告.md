# Phase 2 Requirement Agent 迁移审计报告（施工前只读审计，未改任何代码）

> 依用户指令：Phase 2 开工前先审计，**禁止直接 git mv**。本报告回答五件事：slot_filler 职责分析 / 迁移目标结构 / 风险列表 / 最小修改方案 / 测试影响范围。
> 审计基线：HEAD `3077c76`（Phase 1 完成态）。审计对象：`backend/travel/slot_filler.py`（844 行）、`backend/travel/planning.py`、`backend/travel/models/brief.py`、`backend/travel/graph_state.py`、`backend/config/travel.py` 及全部 import 方（生产 3 处真实 import + 测试 10 文件）。
> 结论先行：**迁移可行，但 slot_filler 不是"一个职责"而是七块，其中意图信号函数是跨层事实源，不能整体搬进 planning/ 包**——方案把 844 行拆成"真身进包 + 意图信号独立落点 + 双 shim 承接旧路径"，存量 20 个测试文件零改动通过。

---

## 一、当前 slot_filler 职责分析（逐块盘点）

| # | 职责块 | 位置（行号） | 内容 | 被谁消费 |
|---|---|---|---|---|
| R1 | **13 槽确定性抽取器**（约 540 行） | 35-574, 577-637 | 中文数字解析、日期遮蔽（T-E02 事故修复：日期区间防误读成天数）、区间天数、人数三层兜底（数字 > 一家X口 > 同伴，含 explicit/guess 来源标记）、预算三档（万/货币单位/预算锚定裸数字，STOP I2）、住宿区域（城市拒绝，STOP I2）、目的地多城市确定性消歧（否定语境/变化目标/最左）、偏好/忌口/节奏词表抽取、must_go/avoid 触发词捕获与清洗（脏条目过滤）、`merge_brief` 多轮合并（avoid 语义优先于 must_go）、`extract_brief` 组装 | 本模块节点函数、travel_pending_resolver（值型补槽探测）、全部 slot 测试 |
| R2 | **意图信号判定（跨层事实源）** | 374-438 | `is_new_run_query`（重开规划）、`is_cancel_run_query`（取消整个规划，保守词表+局部排除句先行）、`is_avoid_patch_query`（completed 态避雷补丁） | **orchestration 层 lazy import**：`travel_graph_node.py:31,254`（is_cancel/is_new_run）、`travel_pending_resolver.py:136`（is_cancel）+ `test_travel_pending_resolver.py`（is_new_run）、`test_cancel_lifecycle.py`（is_cancel）。源码注释明示："路由层 resolver 与 slot_filler/adapter 都以这里为准（context → travel 单向依赖）" |
| R3 | **追问文案** | 640-675 | `build_clarification`：缺失槽→话术一一对应（SLOT_QUESTIONS）、不支持城市明示原因、destination 缺失时接 `recommend.py` 推荐榜（软失败 try/except） | 本模块节点、test_scenarios |
| R4 | **图节点 slot_filler_node** | 678-844 | brief 基底三源选择（checkpoint > reconstruct_brief > 零抽）；**persistence_status 唯一产生点**（延迟 import graph_builder 防环）；强持久化降级处置（require_fresh 拒绝复用跨轮产物）；指纹计算与 brief_changed 判定；版本链 version+1/change_reason/changed_fields；notes 透明化（日期区间换算/区间天数上限/人数 guess）；planning_reset 三分支（require_fresh / brief_changed / NEW_RUN）；update 组装 | graph_builder.py:51（顶层 import 注册节点）、test_travel_graph/checkpointer/persistence/versioning |
| R5 | **偏好预填+回写** | 708-743 | 跨轮首轮预填历史偏好（发生在指纹计算之前，防重排抖动）；本轮显式表达 upsert 落库；双层软失败（失败不影响主链）。延迟 import `tools/travel/preferences.py` | 偏好表 travel_preferences |
| R6 | **对 poi_seed 的名录依赖** | 190-198, 304-310, 366-371, 533-574 | 城市名录（destination 匹配域）、POI 名录（must_go 真名命中）、别名表、不支持城市名录 | poi_seed（只读） |
| R7 | **词表常量**（不在本文件） | models/brief.py | PACE_KEYWORDS / PREFERENCE_KEYWORDS（7 类）/ DIET_KEYWORDS / SLOT_QUESTIONS / REQUIRED_SLOTS | 抽取器与追问共用 |

**依赖方向事实**：slot_filler → poi_seed / models.brief / graph_state(load_brief) / config.travel / tools.travel.preferences（全部延迟或顶层，无反向）；被 graph_builder 顶层 import（注册节点）。**无循环 import 风险点：R4 内部对 graph_builder 的 import 已是函数内延迟**。

## 二、职责归属判定（Requirement Agent / 保留 service / 不能移动）

| 职责块 | 归属 | 理由 |
|---|---|---|
| R1 抽取器 + R3 追问 + R4 节点主体 + R5 偏好接入 | **→ Requirement Agent**（`planning/requirement_agent.py`） | 这就是"自然语言→TripBrief+追问"的完整闭环，是 v4 §3.1 ② 号 Agent 的全部职责面 |
| R2 意图信号三函数 | **→ 独立落点 `core/intent_signals.py`（新增文件）**，不进 Requirement Agent | 语义上它们回答"这条消息是什么会话意图"（重开/取消/避雷补丁），是 **Supervisor 意图层（action 枚举）的判定素材**，不是需求抽取；且被 orchestration 层消费，塞进 planning/ 包会让 orchestration 依赖"需求 Agent"——层次错位。落 core/（底座）方向正确：planning/ 依赖 core 合法，orchestration 经旧路径 shim 继续工作。Phase 3 supervisor 意图层接线时直接消费 core/intent_signals |
| R6 poi_seed 依赖、R7 词表常量 | **保持原位（不能移动）** | poi_seed 是数据源不是 Agent 资产；词表在 models/brief.py 是契约文件（Phase 2 只做字段级加法，不搬家） |
| graph_state（load_brief/brief_fingerprint 9 槽/planning_reset） | **不能移动** | 跨轮契约生产资产（三次事故修复 + 38 测试钉住），Phase 2 只在指纹槽位白名单做**加法** |
| tools/travel/preferences.py | **保持原位** | v3/v4 计划 Phase 3 才迁 travel/memory/；Phase 2 只经现有接口消费（读偏好/存稳定偏好），不新建存储 |

**Memory 接入边界（依用户 §六）**：现状已符合"横向能力"语义——Requirement 经 `preferences.get/upsert` 读偏好/存稳定偏好（预填+回写，软失败）；一次性行程信息（destination/days/budget 等 brief 槽位）只进 checkpoint（conversation checkpoint），偏好表只存 pace/diet/preferences/origin 四类稳定偏好——**preference memory 与 conversation checkpoint 的分界现状即正确，Phase 2 不引入新存储、不建 Memory Agent**。Phase 2 唯一增量：回写内容扩展（avoid_categories 类别级负偏好可持久化，仍只写用户显式表达的字段）。

## 三、TravelBrief 现状 vs 目标 TripBrief schema 差距

| 目标字段（用户 §四） | 现状（models/brief.py:87-105） | 差距与动作 |
|---|---|---|
| destination | `destination: str` ✓ | 无 |
| dates | `start_date: date\|None` ✓ | 无 |
| duration | `days: int\|None` ✓ | 无 |
| **adults / children** | `party_size: int = 1`（单一整数，D4 根因） | **新增 adults/children 可选字段**，party_size 保留为兼容派生（=adults+children，缺省回退旧解析）；pending_resolver 补槽只认 explicit 来源的口径沿用 |
| budget | `budget_cny: float\|None` ✓ | 无 |
| interests | `preferences: list[str]`（7 类词表） ✓ | 词表扩类别（二次元/动漫/汉服/露营/音乐节等） |
| must_go / avoid | ✓（地点级） | 无 |
| **avoid（类别级负偏好）** | 无 | **新增 `avoid_categories: list[str]`**（"不购物/不爬山"→类别标签），与地点级 avoid 并存；进指纹 |
| pace | `pace`（relaxed/moderate/intense） ✓ | 无（词表补 D5「节奏慢一点」） |
| constraints | `diet/lodging/transport`（记录性槽位，不进指纹） ✓ | 无 |

**指纹口径变更（风险声明）**：指纹白名单从 9 槽扩为 11 槽（+adults/children 或 party_size 二选一、+avoid_categories）——**存量会话首轮会触发一次性重排**（v2 裁决 B 用户已接受）。二选一规则：party_size 与 adults/children 语义重叠，指纹只纳入 canonical 字段（建议 adults/children 存在时用之、否则 party_size），避免双计入导致每次都变。此项属**冻结契约内的既定扩展**（v4 §3.1 TripBrief v2 字段），不触发设计偏差报告协议。

## 四、planning.py 兼容方案（用户点名要求，关键设计）

**约束**：`travel/planning.py`（must_go 契约，names_match 唯一事实源）与目标 `travel/planning/` 包**同名不可共存**——Python 包优先于同名模块，先建包会静默遮蔽炸掉 5 个 import 方（validator.py / experts/poi.py / evaluation/travel_quality.py / test_planning_contract.py / test_travel_core_boundary.py）。Phase 1 守护测试已把此风险固化为断言。

**方案（单 commit 内原子完成，无中间态）**：

```
同一 commit：
① git mv backend/travel/planning.py  →  backend/travel/planning/must_go.py   （真身进包，内容零改动）
② 新增 backend/travel/planning/__init__.py：re-export 契约全部公开符号
   （names_match / resolve_must_go / MustGoResolution / UNRESOLVED_NOTICE 等）
   ——包的 __init__ 即「shim」，承接原 travel.planning 命名空间
③ import 方零改动：`from backend.travel.planning import names_match` 语句不变，
   解析目标从模块变为包 __init__，符号同源
④ test_planning_contract.py 16 例零改动必须通过（这是本方案的验收标准）
⑤ test_travel_core_boundary.py 有意翻转断言：planning/ 包存在 + planning.py 不存在
   + must_go.py 在包内 + names_match 可经包命名空间导入
```

**为什么不用"保留 planning.py 文件 + 换包名"**：v4 冻结目录树明确 `travel/planning/`（requirement_agent 等落点），换名违背冻结边界；而 `__init__` re-export 是 Python 标准的模块→包迁移 shim 形态，grep 可见、无 `__getattr__` 魔法、IDE 可解析。

## 五、迁移目标结构（Phase 2 完成态）

```
backend/travel/
├── slot_filler.py                     # 【改造为纯 shim】re-export requirement_agent 全部公开符号
│                                      #   + is_new_run/is_cancel/is_avoid_patch（承接 orchestration 旧 import 路径）
│                                      #   Phase 8 删
├── core/
│   ├── intent_signals.py              # 【新增】R2 三意图信号真身（含 _NEW_RUN_RE/_CANCEL_RUN_RE/_CANCEL_EXCLUDE_RE）
│   ├── ...（Phase 1 已有，不动）
├── planning/
│   ├── __init__.py                    # 【新增】must_go 契约 re-export（承接 planning.py 命名空间）
│   ├── must_go.py                     # 【git mv】原 planning.py 真身（内容零改动）
│   └── requirement_agent.py           # 【git mv + 改造】原 slot_filler.py 的 R1/R3/R4/R5 真身
│                                      #   + LLM 二层框架（flag off）+ adults/children/avoid_categories 抽取
├── models/brief.py                    # 【原位修改】+3 可选字段 + D3/D4/D5 词表补丁 + 偏好类别扩展
├── graph_state.py / graph_builder.py  # 【不动】（graph_builder 经旧路径 import 节点函数，Phase 3 图重排时切新路径）
├── config/travel.py                   # 【原位修改】+ TRAVEL_REQUIREMENT_LLM_ENABLED（默认 false）
└── ...
```

**图节点名不动**：LangGraph 节点仍叫 `travel_slot_filler`（test_agent_inventory.py:34 钉住节点清单），Phase 3 图重排时才改名 `travel_requirement`——Phase 2 只换"实现住址"，不换"门牌"。

## 六、风险列表

| # | 风险 | 级 | 缓解 |
|---|---|---|---|
| P1 | planning.py→包命名空间切换，5 个 import 方断裂 | 高 | §四方案：单 commit 原子迁移 + `__init__` re-export + test_planning_contract 16 例零改动通过作为验收门 + 守护测试翻转 |
| P2 | orchestration 层 lazy import 经 shim 断裂（travel_graph_node 的 is_cancel/is_new_run、travel_pending_resolver 的 7 个抽取函数） | 高 | slot_filler.py 纯 shim 显式 re-export 全部历史公开符号（不用 `__getattr__`）；`test_travel_pending_resolver.py` 16 例 + `test_cancel_lifecycle.py` 9 例零改动通过作为验收门 |
| P3 | 指纹白名单扩展 → 存量会话首轮一次性重排 | 中 | 用户已裁决接受（v2 裁决 B）；canonical 字段二选一防双计入；版本链 change_reason 照常记录 |
| P4 | adults/children 与 party_size 双字段不一致（merge_brief 合并、补槽 explicit 口径） | 中 | party_size 改为 computed 派生（显式表达时 =adults+children）；merge 逻辑单测覆盖「adults 变 children 不变」等组合 |
| P5 | D3/D4/D5 词表补丁引入误命中（"节奏慢一点"命中 relaxed 但"节奏快一点"必须正确分派 intense，不能被"慢"误伤） | 中 | 每条补丁带正反用例；D3 路由词表只加不删（prefilter 词表单调扩展） |
| P6 | 循环 import（requirement_agent 顶部不得 import graph_builder） | 中 | 保持现状延迟 import 模式（R4 内部 graph_builder/pending 均函数内 lazy）；迁移后用 py_compile + 全量 travel 测试验证 |
| P7 | LLM 二层引入隐性业务行为 | 中 | flag `TRAVEL_REQUIREMENT_LLM_ENABLED` 默认 false；本期只落框架+state 计数器（run 内 ≤1 硬上限）+失败回落路径单测（mock LLM 边界），**不启用**；禁止重试/多轮调用/LLM 写业务状态 |
| P8 | git mv 后 git 历史断裂（rename 检测失败） | 低 | planning.py 内容零改动 mv（相似度 100%）；slot_filler→requirement_agent 允许 rename 检测失败（内容有改造），审计报告已留对照表 |
| P9 | 隐蔽消费方（文档/注释引用路径失效） | 低 | 已 grep 全仓：注释提及不影响运行；AGENTS.md 旅游域段落在 Phase 8 文档任务统一更新 |

## 七、最小修改方案（diff 范围预算）

**新增（4 文件）**：
- `travel/core/intent_signals.py`（R2 真身，约 70 行，含三个正则与判定函数原样搬运）
- `travel/planning/__init__.py`（re-export，约 20 行）
- `travel/planning/requirement_agent.py`（R1/R3/R4/R5 真身 = 现 slot_filler.py 约 770 行搬运 + LLM 二层框架约 60 行 + adults/children/avoid_categories 抽取约 50 行）
- `tests/travel/test_requirement_contract.py`（新契约测试，预计 20~25 例：新字段抽取/合并/指纹 canonical/LLM 框架边界 flag off/词表正反用例/planning 包命名空间等价）

**修改（4 文件）**：
- `models/brief.py`：+3 可选字段 + 词表补丁（D3/D4/D5 + 偏好新类别）
- `travel/slot_filler.py`：真身移走后改纯 shim（re-export，约 15 行）
- `tests/travel/test_travel_core_boundary.py`：翻转 planning 断言（有意变更）
- `config/travel.py`：+1 开关

**git mv（2 项）**：planning.py→planning/must_go.py；slot_filler.py 主体→planning/requirement_agent.py（git mv 后就地改造）

**不触碰（明确清单）**：graph_builder.py / graph_state.py / validator.py / repair.py / experts/* / orchestration/* 全部 / tools/travel/preferences.py / poi_seed.py / capabilities.yaml。

**预计 diff 规模**：净新增约 400 行（含测试），存量代码改动 ≤ 40 行（brief.py 字段+词表、config 开关、守护测试翻转），其余全部为搬运（git mv）。

## 八、测试影响范围

| 测试文件 | 用例数 | 影响 | 验收标准 |
|---|---|---|---|
| test_slot_filler.py | 49 | **零改动**（经 shim import） | 全绿 |
| test_slot_patch_boundary.py | 20 | 零改动 | 全绿 |
| test_planning_contract.py | 16 | 零改动（命名空间等价） | 全绿 ← §四方案验收门 |
| test_travel_pending_resolver.py（orchestration） | 16 | 零改动 | 全绿 |
| test_cancel_lifecycle.py | 9 | 零改动 | 全绿 |
| test_travel_graph.py / test_persistence.py / test_checkpointer.py / test_versioning.py / test_p0_mvp.py / test_scenarios.py / test_run_context.py | 约 110 | 零改动 | 全绿 |
| test_travel_core_boundary.py | 4 | **有意翻转 1 例**（planning 断言） | 全绿 |
| test_requirement_contract.py | 新增 20~25 | 新增 | 全绿 |
| 一致性三门 + node_runtime 门 + 金标 | 37+40+106 | 零改动 | 全绿（每 Phase 必跑） |
| **合计回归面** | **约 380 例** | 存量测试改动 = 1 例（守护测试有意翻转） | — |

## 九、实施步骤（确认后执行，预计 3 个 commit）

1. **Commit A（契约先行）**：models/brief.py 字段与词表 + config 开关 + test_requirement_contract.py 的 schema/词表部分 → 全绿。
2. **Commit B（原子迁移）**：planning.py→包（§四方案）+ slot_filler 主体 git mv→requirement_agent.py + intent_signals.py 落位 + slot_filler.py 变 shim + 守护测试翻转 → 全量 travel 测试 + 一致性门全绿。
3. **Commit C（LLM 框架 + 偏好扩展）**：requirement_agent 内 LLM 二层框架（flag off、计数器、回落路径）+ avoid_categories 回写扩展 + 评测集骨架 `evaluation/datasets/travel/slot/cases.jsonl`（≥50 例规则层基线）→ 全绿。
4. **Phase 2 Completion Report**（按用户 §十二 八项格式：Verdict/修改文件/为什么/冻结契约对应/测试结果/未完成/风险/Commit）。

---

## 十、设计偏差检查

本轮审计**未发现违反冻结契约、必须走偏差报告协议的问题**。planning.py 同名约束 v4 文档已预见（Phase 1 守护测试登记），§四方案是该约束下的计划内解法。两处执行层裁量（intent_signals 落 core/、图节点名 Phase 2 不改）均为冻结边界"不冻结内部文件划分"授权范围内，已在 §五/§六 说明理由。

**待确认后开工。确认点：① §四 planning.py 原子迁移方案 ② intent_signals.py 落 core/ 的归属 ③ 指纹 canonical 字段二选一规则（adults/children 优先）④ 三 commit 拆分。**

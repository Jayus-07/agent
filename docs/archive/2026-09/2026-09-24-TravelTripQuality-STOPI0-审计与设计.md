# Travel Trip Data & Itinerary Quality — STOP I0 审计与设计

> 日期：2026-09-24 ｜ 基线 HEAD：`cf71842`（STOP H 最终验收报告提交）
> 性质：**只读审计**（I0 阶段零业务代码修改）+ STOP I1~I5 设计冻结
> 前置：STOP_H_PASS=true / TRAVEL_RUNTIME_E2E_PASS=true / TRAVEL_INFRASTRUCTURE_FROZEN=true
> 回归基线：1032 passed / 0 failed

---

## 0. 总判断（I0 结论）

当前 Travel Agent 的规划链路是**全确定性管线，域图内零 LLM 调用**（grep 全模块
无 ChatOpenAI / llm.invoke / model.invoke），因此任务书最担心的「模型凭空生成
POI / reporter 补造事实」在**结构上不可能发生**——POI 只能来自种子池或腾讯 LBS
解析，行程单由模板从结构化 itinerary 渲染。STOP I 的真实工作不是「防 LLM 造假」，
而是把这种结构正确性**变成可验证的契约**：候选池成员校验（当前对非法 poi_id
静默跳过）、跨天重复检测（当前只查同天）、must_go 三态契约（当前只藏在 notes
文案里）、质量金标回归（当前 22 条评测用例全是契约断言，无聚合质量指标）。

STOP_I0_PASS=true

---

## 1. I0-1 真实 Travel 数据源审计

### 1.1 数据源全量清单（grep poi/attraction/dataset/seed/fixture/city/opening_hours/location/lat/lng/ticket/duration/category/rating/popularity）

| # | 数据源 | 位置 | 形态 | 分类 | 进规划链路 |
|---|--------|------|------|------|-----------|
| 1 | 本地种子池 31 条 POI | `backend/tools/travel/poi_seed.py`（`_RAW`） | Python 常量（福州 11 + 厦门 10 + 杭州 10） | **STATIC_SEED** | ✅ 唯一默认候选源 |
| 2 | 腾讯位置服务 POI 检索 | `backend/tools/travel/live_map.py::resolve_place` | 第三方 API（真实数据） | **REAL**（坐标/名称真实；营业时段/票价为契约默认值→`unverified`） | ✅ 仅 must_go 缺失补全（`TRAVEL_USE_LIVE_MAP`+key 双闸） |
| 3 | 腾讯真实路线规划 | `live_map.live_leg` → `routing.set_route_provider` 插槽 | 第三方 API（距离/时长/真实油价） | **REAL**（失败自动回落估算 `estimate:local`） | ✅ 通勤段 |
| 4 | 腾讯天气预报 | `backend/infra/lbs.py::api.weather_for_city` | 第三方 API | **REAL**（失败软降级跳过） | ✅ weather 专家 |
| 5 | RAG 旅游知识库 | `backend/tools/travel/knowledge.py`（KB id=travel） | pgvector 检索摘录 | **REAL**（原文引用，时效未核实，独立成段展示） | ✅ risk 专家（仅免责/参考，**不产 POI**） |
| 6 | 城市消费档位 | `backend/config/travel.py::TRAVEL_CITY_COST_TIERS` | Python 常量 | **STATIC_SEED**（餐费/房价均值占位） | ✅ budget 专家 |
| 7 | 速度/绕行/步行距离模型 | `backend/config/travel.py` | env 可调常量 | **INFERRED**（估算系数，行程单标 `is_estimate`） | ✅ routing |
| 8 | 用户偏好持久化 | `backend/tools/travel/preferences.py` | PG（agent_memory） | 用户历史输入回填（**非事实数据**，只填偏好槽） | ✅ slot_filler |
| 9 | LLM 生成 POI | — | — | **不存在**（域图内零 LLM 调用） | ❌ |
| 10 | POI 数据库表 | — | — | **不存在**（POI 不落库，种子即代码） | ❌ |

### 1.2 「当前 POI 数据到底来自哪里」的唯一答案

```
候选池 = poi_seed.load_city(resolve_city(destination))     ← 31 条种子
         ∪ live_map.resolve_missing_places(must_go…)      ← 腾讯 LBS 补全（仅必去点名）
排除   = is_excluded(avoid)                                 ← 硬过滤
排序   = score_poi（must_go 1000 > 偏好标签 10/个 > rating）  ← 确定性
```

无任何路径能让「不存在于以上两源的名字」进入行程（`ItineraryItem.poi` 只能由
`candidates` dict 按 poi_id 取出构造）。**但该保证目前没有校验器/测试守护**
（见 §6 缺口 G-1）。

---

## 2. I0-2 POI Schema 审计（真实代码，`travel/models/poi.py`）

| 任务书字段 | 现状 | 判定 |
|-----------|------|------|
| poi_id | ✅ `str`（种子=语义 id `xm_gulangyu`；LBS=`lbs_<腾讯id>`，稳定非索引） | 事实字段 |
| name | ✅ | 事实字段 |
| city | ✅（对齐 `TravelBrief.destination` 的城市键） | 事实字段 |
| district | ❌ 不存在 | — |
| category | ✅（5 常量：景点/美食/购物/夜生活/公园） | 事实字段 |
| lat / lon | ✅ `lat`/`lng` float | 事实字段 |
| address | ❌ 不存在 | — |
| opening_hours | ⚠️ 部分：`open_time`+`close_time`（每日同窗）+`closed_weekdays`（0=周一），无按星期细分窗口 | 事实字段（种子=verified；LBS=默认值+unverified） |
| visit_duration | ⚠️ `suggested_minutes` int 默认 90——种子为**示例值** | **推断字段**（非官方数据，seed 文件头部已声明） |
| price | ✅ `ticket_cny` 默认 0.0（0=免费语义） | 事实字段（LBS 场景=占位+unverified） |
| rating | ✅（种子有值；LBS 恒 0） | **推断字段**（热度评分，用于排序） |
| popularity | ❌ 独立字段不存在（rating 兼任） | — |
| tags | ✅（对齐 `PREFERENCE_KEYWORDS` 键） | 推断字段 |
| source | ✅ `seed:local` / `tencent:lbs`；reporter `_SOURCE_LABELS` 登记，**未登记来源会显式报「来源未登记」**（禁止 source=""） | 溯源字段 |
| source_url/source_id | ⚠️ 无独立字段；LBS 的 poi_id 内嵌腾讯 id | — |
| updated_at | ⚠️ `observed_at: str\|None`（ISO UTC）+ `verification_status: verified/unverified` | 时效字段（Phase 1 已落地） |

**结论**：契约已具备「来源显式 + 时效语义（verified/unverified + observed_at）」，
reporter 对 unverified 营业时间/票价逐点披露。缺 address/district/source_url
对排程与校验无影响（地理计算只用 lat/lng），**不新增**——避免为凑 schema 造列。
STOP I1 的增量 = 候选池成员契约与 must_go 解析三态（见 §9），不动 Poi 模型。

---

## 3. I0-3 五个专家真实行为（调用链逐一核实）

### POI Expert（`travel/experts/poi.py`）
```
state → load_brief → search_poi(城市归一→avoid 硬排除→打分→截断60)
      → live_map.resolve_missing_places（must_go 缺失时腾讯补全，required=True）
      → build_skeleton（必去置顶→热度降序→逐个挑「项数最少/地理跨度最小」的天，
        双容量约束[档位 POI 数 + 活动分钟]，超容量进 dropped 如实记录）
      → state.candidates / day_plan / notes
```
**判定：从真实数据选 POI（规则），无 LLM。** 候选不足时少排并写 notes
（「空行程比假行程诚实」）。

### Transit Expert（`travel/experts/transit.py`）
```
state → day_plan(poi_id) → candidates 还原 Poi → order_pois（最近邻+开门时间起点选择）
      → schedule_day（时间轴推进：cursor=max(cursor, open_time) 等开门、午餐显式占时段）
      → 段间 estimate_leg（真实腾讯路径优先 / Haversine×1.35÷速度模型回落，
        is_estimate/source/observed_at 全标注，远期行程强制本地估算）
      → Itinerary（含 stamp_version 版本章）
```
**判定：真正计算 distance/travel time/mode（几何+真实 API），非自然语言猜测。**

### Weather Expert（`travel/experts/weather.py`）
**判定：真实腾讯天气 API**（未来预报），坏天气关键词规则 → 户外 POI 换同城室内
候选（只从既有候选池换，必去永不换），失败/无日期软降级并披露。

### Budget Expert（`travel/experts/budget.py` + `tools/travel/cost.py`）
**判定：规则估算**——门票=真实票价×人数；餐饮/住宿=城市档位常量；通勤=分段
真实/估算费用。**不判定超支**（判定权在 validator 轴四，单一口径）。无预算时
如实写「未做预算校验」。

### Risk Expert（`travel/experts/risk.py`）
**判定：结构化 disclaimer（seed 数据声明/预约/签证未接入）+ RAG 原文摘录**
（独立「知识库参考」段，标注时效未核实）。不产出任何行程事实。

---

## 4. I0-4 Planner / Itinerary Builder 审计

itinerary 的生成位置与「谁决定第几天去哪里」：

```
candidates（poi expert）
   ↓ build_skeleton：day allocation（规则：必去优先→热度→日均均衡→地理聚类）
   ↓ day_plan: list[list[poi_id]]                     ← 「第几天去哪里」在这里决定
   ↓ build_itinerary/schedule_day（transit expert）   ← 时刻/通勤/午餐（规则）
   ↓ Itinerary + 版本章
```

**判定：规则（100% 确定性，无算法搜索、无 LLM）。** 复现性由「同输入必同输出」
保证（排序键全部带 poi_id 稳定 tie-break）。

---

## 5. I0-5 Validator 矩阵（`travel/validator.py` 实测代码，非设计文档）

| Constraint | 已检查 | 严格程度 | 数据来源 |
|-----------|-------|---------|---------|
| destination | ⚠️ 间接（resolve_city 失败→0 候选→reporter「没有该城市数据」） | 提示（非 validator 轴） | poi_seed 城市键 |
| days | ⚠️ 空白天被压缩 + notes 披露（「行程由 N 天压缩为 M 天」），validator 不判违反 | 提示（设计取舍：候选不足时诚实缩天优于伪造空天） | skeleton |
| must_go | ✅ check_coverage：未排入→**warning**（MUST_GO_MISSING）+ skeleton notes「未匹配到」 | warning | brief.must_go × itinerary |
| avoid | ✅（在**检索层硬排除** is_excluded，validator 不再查） | 硬（过滤层） | brief.avoid |
| duplicate POI | ⚠️ **仅同天**（GEO_REVISIT warning）；**跨天重复无检查**（结构上 skeleton 不会产生，但无守护） | warning | itinerary |
| daily count | ✅ PACE_TOO_MANY_POIS（error） | error | 档位常量 |
| opening hours | ✅ TIME_CLOSED（窗口外）+ TIME_CLOSED_WEEKDAY（闭馆日）；必去项冲突→**decision_required**（交用户裁决不自动删） | error / decision_required | Poi.open/close/closed_weekdays |
| geographic consistency | ✅ GEO_SCATTER（单日在途>150min，error）+ GEO_FAR_LEG（单段 60/105min warn/error） | error+warning | legs |
| travel time | ✅（legs 为真实 API/几何估算，估算显式标注） | —（数据质量在 TransitLeg.source/is_estimate） | routing |
| budget | ✅ BUDGET_OVER（error）+ BUDGET_TIGHT（90% 预警）；无预算→不判定+reporter 披露 | error | cost + brief.budget_cny |
| pace | ✅ PACE_TOO_INTENSE（单日活动分钟，error） | error | 档位常量 |
| diet | ❌ 明确不评估（P1-1 刻意：种子池无餐厅级数据，不假装推断） | not_evaluable | — |
| lodging | ❌ 不参与路线（记录性槽位；无住宿经纬度数据，**不伪称已优化住宿距离**） | not_evaluable | — |
| weather | ⚠️ 非 validator 轴（weather 专家：替换+披露） | 增强 | 天气 API |

**修复（repair.py）**：deterministic 局部修复（drop-by-id / drop-lowest-priority /
drop-until-minutes / drop-most-expensive / farthest-from-center），必去项永不静默
删除（kept_required），`TRAVEL_MAX_REPAIR_ROUNDS=2` + `repair_stalled` +
防震荡签名（连续两轮违反集无改善即停）——**有界、无死循环**（supervisor 只认
状态事实，GraphRecursionError 事故已修并有回归）。

---

## 6. 缺口清单（STOP I1~I5 要关的账）

| # | 缺口 | 证据 | 归属 |
|---|------|------|------|
| G-1 | 非法 poi_id **静默跳过**：transit `candidates[pid] for pid in day if pid in candidates`——注入不存在的 poi_id 不报错，直接蒸发 | transit_expert_node / reschedule_after_repair | I4（validator 候选池轴 + T11） |
| G-2 | 跨天重复无校验（同天有 GEO_REVISIT） | validator.check_geo | I4（+T12） |
| G-3 | must_go 三态契约缺失：resolved/unresolved/scheduled 只散在 notes 文案 | poi_expert `_build_notes` | I2 |
| G-4 | avoid-PATCH 无路由通道（completed 态「不去鼓浪屿了」0 信号词，prefilter 与 pending resolver 都不接） | travel_prefilter `_TRAVEL_PATTERNS` + pending resolver（补槽仅 pending 期） | I2（STOP H Deferred #1） |
| G-5 | 裸数字预算不识别：「预算改成5000」（无「元」）→ 抽不出 → T3 失败 | `_RE_BUDGET_YUAN` 需货币单位 | I2（STOP H Deferred #2） |
| G-6 | lodging 抽取城市尾缀误捕：「住在厦门」会把目的地当住宿区 | `_RE_LODGING_ZHU` + `_clean_lodging` | I2（STOP H Deferred #2） |
| G-7 | 质量金标缺失：22 条评测全是逐例契约断言，无 Q1-Q10 聚合指标与门禁；CLI `module` choices 不含 travel | evaluation/cli.py | I5 |
| G-8 | 候选池规模/过滤统计无 trace 字段（candidate_count 等） | travel_graph_node `_stamp_execution_tags` | Observability |
| G-9 | Prometheus 无 travel 质量指标 | observability/metrics.py 无 travel_* | Observability |

**不修的（审计确认现状正确，不动）**：
- days 压缩（诚实缩天优于伪造空天，notes 已披露）——金标用例只取种子数据可满足的天数；
- diet/lodging not_evaluable（无数据不假装）；
- 种子数据为示例值（已 verified 语义错位问题见 §7 注）；
- reporter 模板渲染（无事实创造，结构正确）。

---

## 7. STOP H Deferred 分类（I0-8）

| Deferred 项 | 分类 | 理由 |
|------------|------|------|
| avoid-PATCH 通道（completed 态） | **纳入 STOP I（I2）** | 直接决定 itinerary correctness（T4 核心场景） |
| Travel slot extraction boundary（预算裸数字/lodging 尾缀） | **纳入 STOP I（I2）** | 同上（T3/T8） |
| 第二 tenant 实机 E2E | 不纳入（Infra backlog） | 单租户部署；tenant 隔离已由 STOP G T4 覆盖 |
| LB 压测 / soak / chaos | 不纳入 | 容量与韧性专项，与数据质量无关 |
| WATCH→Lua | 不纳入 | STOP H 实测 T7/M2 零 conflict 耗尽 |

---

## 8. I0-7 现有测试质量盘点

`backend/tests/travel/` 共 **326 个测试**（16 文件）：routing/state 类占大头
（slot_filler 49、travel_graph 38、validator 32、persistence 20、repair 19、
versioning 18、dataset 17、providers 23、scenarios 23…）。

**真正验证 itinerary quality 的**：validator 32（时间/地理/体力/预算/覆盖五轴）
+ repair 19（修复动作与有界性）+ scenarios 23（端到端约束场景）+ dataset 17
（种子数据完整性：坐标/时段/票价字段级校验）。**缺少的**：跨天重复、非法 poi_id
注入、聚合质量指标（Q1-Q10）、≥30 条金标的回归门禁。评测数据集
`evaluation/datasets/travel/cases.jsonl` 22 条（A-F 六组），runner 已注册但
CLI choices 漏了 travel 模块名。

---

## 9. STOP I 设计冻结（I1~I5 最小演进）

### 目标架构（职责分离开销最小化——在现有模块上补契约，不搬目录）

```
TravelBrief
   ↓ resolve_must_go（I2 新增，纯函数：resolved/unresolved 三态）
CandidateRetriever = search_poi（现状：hard filter=城市/avoid + 打分排序）
   ↓ 成员契约（I1）：候选池即唯一合法 poi_id 全集
ItineraryPlanner = build_skeleton + schedule_day（现状确定性，I3 只补审计确认+金标）
   ↓
DeterministicValidator = check_itinerary（现状五轴）+ 新增两轴（I4）：
   + check_pool（候选池成员：非法 poi_id→error，T11）
   + 跨天重复→error（T12）
   ↓ Repair（现状有界局部修复，I4 补跨天重复/非法 poi 的修复动作）
Reporter（现状模板渲染，grounding 由金标 T15 固化）
   ↓
Quality Evaluation（I5）：golden ≥30 + Q1-Q10 聚合 + CLI travel + 门禁
```

### I1 Canonical POI Data Contract（增量最小化）
- Poi 模型**不改字段**（§2 已有 source/verification_status/observed_at）。
- 新增**候选池成员不变式**的守护点：域图内所有 itinerary 构造路径的 poi_id
  必须来自本轮 candidates（validator 强制，见 I4）。
- poi_id 稳定性：种子=语义 id；LBS=`lbs_<腾讯id>`——已满足「非 list index」；
  补测试固化（同输入同 id，跨会话可复现）。
- freshness：unverified + observed_at 已落——补「unknown ≠ false」断言测试
  （无营业时间数据的 LBS POI 不得出现确定语气时段，reporter 已披露，测试固化）。
- Unknown must_go（T10）：unresolved 不伪造——**显式化**为 resolve_must_go
  三态返回，reporter/金标消费「未能确认该地点的数据，暂未自动安排」。

### I2 Candidate Retrieval & Ranking + 契约补全
- `travel/planning.py`（新模块，纯函数）：
  - `resolve_must_go(brief, candidates) -> (resolved, unresolved)`：单一事实源，
    poi expert 的 notes、validator coverage、金标 Q2 三处共用（G-3）。
  - ranking 保持现有确定性打分（must_go≫tags>rating），**category diversity
    在 skeleton 的日均均衡+地理聚类已隐式达成**（每天跨类别），金标 Q6 度量；
    不引入新评分权重（先有度量再谈调参）。
- avoid-PATCH 路由通道（G-4）：`travel_pending_resolver` 新增分支——
  `active_domain=travel` ∧ `travel_run_id` 存在（completed 态含）∧ 消息命中
  avoid 抽取（复用 `extract_avoid` 非空）∧ 非取消/非 NEW_RUN ∧ 长度≤40 →
  `route_mode=travel, resume_mode=patch_avoid`。域图内 slot_filler 合并 avoid →
  指纹变化 → planning_reset → 重排后行程不含该 POI（merge_brief 已有
  「avoid 优先于 must_go」语义，不破坏冻结契约）。
  「不去厦门了，改去杭州」类目的地变更：城市名被 `_filter_city_names` 挡在
  avoid 之外，destination 变化自然走指纹重排/NEW_RUN，不误入 avoid。
- slot 边界（G-5/G-6）：`extract_budget` 增加「预算关键词锚定的裸数字」分支
  （`预算[^。\d]{0,3}(\d+)`，仍然不要求数字带货币单位时不误捕「3天」——
  锚定词在数字之前）；`extract_lodging` 剥尾缀后若命中城市名/目的地则拒绝。

### I3 Deterministic Itinerary Planner（现状加固）
- 审计确认现有 build_skeleton/schedule_day 即「deterministic skeleton」：
  day capacity（档位 4/5/7 × 240/360/480min）、visit duration（suggested_minutes，
  derived 语义已在种子声明）、travel time（Haversine 桶 + 真实 API）、
  geographic clustering（day_radius_km 最小化）、lodging（无经纬度→不假装，
  记录性槽位）、budget（validator 轴四 + repair drop-most-expensive）。
- start_date 存在→真实日期（schedule_day day_date 已实现），缺失→Day N。
- 增量：补齐金标用例覆盖（T1/T13）与可解释性字段透传（notes 已带原因）。

### I4 Deterministic Validator + Repair
- validator 新增候选池轴：`check_pool(itinerary, valid_ids)`——行程内 poi_id ∉
  候选池 → error `CODE_POI_NOT_IN_CANDIDATES`（G-1/T11）；validator 节点从
  state.candidates 取合法集（保持 check_* 纯函数，注入参数）。
- 跨天重复：`CODE_GEO_REVISIT` 从「同天」扩到「全行程」（同一 poi_id 出现两次
  → error，必去项同样报——重复安排即使必去也是 bug；T12）。
- repair 补两动作：pool 违反→删除该条目；跨天重复→保留首现、删除后续。
- 修复有界性：沿用 MAX_REPAIR_ROUNDS=2 + repair_stalled + 防震荡签名（已冻结）。
- Reporter grounding：不改模板（已正确）；金标 T15 + Q10 从下游度量兜底。

### I5 Quality Evaluation
- 数据集扩到 **≥32 条**：现有 22 + 新增金标 T1~T15 映射（基础规划/must_go/
  avoid/budget/pace/lodging/冲突/跨轮/unknown/注入/重复/地理离群/营业时间/
  reporter 幻觉），城市覆盖福州/厦门/杭州（种子真实支持的全部城市，不硬塞）。
- **聚合质量指标 Q1-Q10**（确定性，无 LLM judge）：
  Q1 valid_poi_rate=1.0；Q2 must_go_coverage=1.0（resolvable 口径）；Q3
  avoid_violation=0；Q4 duplicate=0；Q5 day_count_accuracy=1.0；Q6
  intra-day total estimated distance（超限用例检出）；Q7 daily load
  （visit+transit ≤ 当日合理上限）；Q8 budget compliance（可计算场景 ≤
  budget 或显式 unsatisfied）；Q9 hard_constraint_pass_rate ≥0.98；Q10
  unsupported_fact_rate=0（答案中 ¥ 数字 ⊥ 无票价 POI 断言 + POI 名 ⊆
  canonical 集）。
- CLI：`python -m backend.evaluation travel [--smoke] [--all] [--compare latest]`
  ——补 choices 与聚合报告输出；**门禁** = pytest
  `backend/tests/travel/test_quality_golden.py`（跑金标断言 Q1-Q10 阈值，
  阈值取自 I0 审计：dataset 全三城、种子数据可满足，不拍脑袋）。

### Observability
- `backend/travel/quality_metrics.py`（独立文件，模式对齐 context_metrics.py，
  避免多会话并行改 metrics.py）：`travel_candidate_total`、
  `travel_itinerary_validation_total{status}`、
  `travel_itinerary_constraint_violation_total{constraint}`、
  `travel_itinerary_repair_total{status}`、`travel_unresolved_place_total{status}`。
  标签低基数（constraint/status/source_type），禁止 poi_id/user/city label。
- trace 字段补：`travel_candidate_count`、`travel_filtered_count`、
  `travel_scheduled_poi_count`（travel_graph_node `_stamp_execution_tags`）。
- 结构化事件（logger info 事件行）：`travel.candidates.retrieved` /
  `travel.itinerary.planned` / `travel.itinerary.validation_failed` /
  `travel.itinerary.repaired` / `travel.itinerary.validated` /
  `travel.data.unresolved_place`（复用现有 logger，事件名入结构化字段）。

---

## 10. 提交拆分（预期）

```
I0 docs: audit trip data and itinerary quality          ← 本文档
I1 feat(travel): canonicalize poi data contract          ← resolve_must_go + 契约测试
I2 feat(travel): deterministic candidate retrieval and constraints ← avoid-PATCH 路由 + slot 边界
I4 feat(travel): close validation and repair loop        ← pool 轴 + 跨天重复 + repair
I5 test(travel): itinerary quality golden evaluation     ← 数据集 + Q1-Q10 + 门禁 + CLI
I6 obs(travel): quality telemetry and freeze report      ← 指标 + trace + 最终报告
```

（I3 并入 I1/I5：现有 planner 即确定性实现，无独立代码增量。）

## 11. 风险与不动清单

- **不碰**：graph_builder 装配、supervisor 决策、ConversationContext/Repository
  mutation、checkpointer、APISIX/JWT/SSE、main graph builder——STOP H 冻结。
- **并行会话**：工作区现有 WIP（context_budget/customer_service/rag）属其他
  会话，本轮不触碰、不收编；提交一律 `git add -- <exact> && git commit -- <exact>`。
- 路由层改动（travel_pending_resolver）是**新增分支**，不改已有分支行为；
  守护用例 `test_router_prefilter_order.py` 必须保持全绿。

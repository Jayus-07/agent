# Travel Trip Data & Itinerary Quality — STOP I 最终验收报告

> 日期：2026-09-24 ｜ 基线 HEAD：`cf71842`（STOP H 冻结）→ 收口 HEAD：`6129a8a`
> 前置：STOP_H_PASS=true / TRAVEL_RUNTIME_E2E_PASS=true / TRAVEL_INFRASTRUCTURE_FROZEN=true

---

## 1. Verdict

```text
STOP_I_PASS=true
TRAVEL_TRIP_DATA_PRODUCTION_CLOSURE_PASS=true
TRAVEL_ITINERARY_QUALITY_FROZEN=true
```

Gate（任务书 §79）逐条判定见 §19。

---

## 2. Before Architecture

STOP H 后的状态：**runtime 已冻结，quality 尚未冻结**。

- 真实链路（APISIX→JWT→FastAPI→Router→TravelPendingResolver→域图→Redis Context→PG Checkpointer→SSE）已验收；
- 但「规划结果本身是否值得信任」没有契约守护：候选池成员无校验（非法 poi_id
  静默蒸发）、跨天重复无检测、must_go 三态散落在 notes 文案、completed 态
  avoid-PATCH 无路由通道（STOP H Deferred #1）、裸数字预算抽不出（Deferred #2）、
  22 条评测全是逐例契约断言而无聚合质量指标。

## 3. Data Architecture

```
poi_seed.py（31 条 STATIC_SEED，福州11/厦门10/杭州10，source=seed:local）
   ∪ live_map.resolve_missing_places（腾讯 LBS 真实解析，仅 must_go 缺失补全，
     source=tencent:lbs + verification_status=unverified）
   ↓ search_poi（城市归一 → avoid 硬排除 → 确定性打分 must_go≫tags>rating）
Candidate Pool（本轮行程的合法 poi_id 全集 = canonical 投影）
```

**I0 审计核心结论**：域图内**零 LLM 调用**（slot_filler 正则 → poi 检索 →
transit 几何/腾讯路线 → weather 真实预报 → budget 规则 → risk 免责+RAG 摘录 →
validator 纯规则 → repair 确定性 → reporter 模板）。「模型凭空造 POI」在结构上
不可能；STOP I 把该结构正确性变成了**可验证契约**。

## 4. POI Contract

- schema（`travel/models/poi.py`，**本轮零字段变更**——I0 审计确认已有
  source/observed_at/verification_status 时效语义，缺 address/district/source_url
  对排程与校验无影响，不为凑 schema 造列）：
  `poi_id(name 语义 id / lbs_<腾讯id>) / name / city / category / lat / lng /
  open_time+close_time+closed_weekdays / suggested_minutes(derived) /
  ticket_cny / tags / rating / required / source / observed_at / verification_status`
- **source 显式**：`seed:local` / `tencent:lbs` / `estimate:local` / `tencent:lbs(路线)`
  全部在 reporter `_SOURCE_LABELS` 登记，未登记来源显式报「来源未登记」，禁止 source=""。
- **poi_id 稳定性（本轮实测修复）**：LBS 回退 id 原用 `abs(hash(...))` —— Python
  字符串 hash 带进程盐（PYTHONHASHSEED），同地点跨进程 id 漂移；改为
  `sha1(name|city)[:12]`（`live_map.stable_fallback_id`），回归测试
  `test_planning_contract.py::TestPoiIdStability`。
- **unknown ≠ false**：LBS 补全 POI 的营业时段/票价为占位值且强制
  `unverified`+`observed_at`，reporter 逐点披露「未经核实，请出行前确认」。
- **unknown must-go（T10）**：`travel/planning.py::resolve_must_go` 唯一事实源
  产出 resolved/unresolved 两态；unresolved 走「未能确认该地点的数据，暂未自动
  安排」披露，绝不伪造 canonical 条目（金标 T-G10 + 单测双守护）。

## 5. Candidate Retrieval

- **Hard filter**：城市归一（resolve_city，含否定语境消歧）→ avoid 命中整条剔除
  （名称/类别/标签）→ invalid POI 不可能入池（种子+LBS 唯一二源）。
- **Ranking**（确定性规则）：`must_go(1000) > 偏好标签(10/个) > rating(1)`，
  poi_id 稳定 tie-break —— 同输入必同候选序。
- **Diversity**：类别多样性由骨架的「日均均衡 + 地理跨度最小」分配实现；
  金标 Q6 以在途分钟峰值度量（实测 70 分钟 ≪ 150 上限），未出现同类扎堆跨日
  横跳。不加新评分权重（先有度量，再谈调参）。
- **must_go 契约**：resolved（强制置顶排入，validator coverage 守护）/ unresolved
  （如实披露）/ scheduled（金标 Q2 = scheduled/resolvable，实测 1.0）。
- **avoid 契约（含 STOP H Deferred #1 关闭）**：completed 态「不去鼓浪屿了」
  → `TravelPendingResolver` 新增 avoid-PATCH 分支（`is_avoid_patch_query`
  唯一判定源；cancel/new_run/客服强信号优先级在前）→ `resume_mode=patch_avoid`
  短路回域图 → merge_brief 加 avoid（avoid 优先于 must_go 的既有冻结语义不动）
  → 指纹变化 → planning_reset → 重排后行程不含该 POI。

## 6. Planner

现有 `build_skeleton + schedule_day` 即确定性 planner（I0 审计确认，本轮无算法
变更）：

| 维度 | 实现 |
|------|------|
| Day capacity | 档位 POI 数上限（4/5/7）+ 单日活动分钟上限（240/360/480）双约束 |
| Visit duration | `suggested_minutes`（derived 语义，种子文件头声明示例值） |
| Travel time | 真实腾讯路线优先 / Haversine×1.35÷速度模型回落，`is_estimate`+`source` 标注，远期行程强制本地估算 |
| Geographic clustering | 逐个挑「项数最少→地理跨度(day_radius_km)最小」的天；起点选「已开门」 |
| Pace | 档位常量进骨架容量 + validator 轴三复检 |
| Budget | cost 规则估算（真实票价×人数+城市档位餐饮/住宿+分段通勤）；超支判定单点在 validator 轴四 |
| Start date | 有 → 真实日期+闭馆星期复检；无 → Day N 表述 |
| Lodging | 记录性槽位（无住宿经纬度数据，**不伪称已优化住宿距离**），金标 T-G8 回归 |

## 7. Validator

`check_itinerary(itinerary, valid_poi_ids)` 八轴（新增两轴加粗）：

| 轴 | 约束 | 级别 |
|----|------|------|
| time | TIME_CLOSED / TIME_CLOSED_WEEKDAY（必去冲突→decision_required 交用户）/ TIME_OVERLAP / TIME_DAY_OVERRUN(w) / TIME_LONG_WAIT(w) | error/decision |
| geo | GEO_SCATTER（单日在途>150min）/ GEO_FAR_LEG（60w/105e）/ GEO_REVISIT（同天，w） | error+warning |
| **pool（新）** | **POI_NOT_IN_CANDIDATES：行程含候选池外 poi_id**；无候选池事实时 not_evaluable（不假通过不假失败） | **error** |
| **structure（新）** | **POI_DUPLICATED：同一 poi_id 跨不同天重复**（首现所在天不判；同天重复保持 GEO_REVISIT 原语义） | **error** |
| pace | PACE_TOO_MANY_POIS / PACE_TOO_INTENSE | error |
| budget | BUDGET_OVER / BUDGET_TIGHT(w)；无预算不判定+reporter 披露 | error |
| coverage | MUST_GO_MISSING（w，匹配口径统一到 planning.names_match） | warning |
| diet / lodging | **not_evaluable**（无餐厅级/住宿经纬度数据，不假装） | — |

## 8. Repair

确定性局部修复（`repair.py`，有界：`TRAVEL_MAX_REPAIR_ROUNDS=2` +
`repair_stalled` + 连续两轮违反集无改善即停 + 必去项永不静默删除 kept_required）。
新增两动作（对应新轴）：

- **POI_NOT_IN_CANDIDATES** → 直接移除（事实不可核实；即使挂 required 名义——
  unresolved 的点名走披露，不伪造行程）；
- **POI_DUPLICATED** → 保留首现（必去语义由首现满足）、摘除后续重复，动作留痕
  kept_required。
- 存量动作保持：drop-by-id（时段/闭馆/远距）、drop-lowest-priority（超量）、
  drop-until-minutes（超时）、drop-most-expensive（超预算）、farthest-from-center
  （折返）。
- ** transit 静默跳过显式化**：day_plan 引用候选池外 id 时不再无声蒸发——
  logger.warning + notes 留痕「已忽略」（G-1 两半：validator 拒绝 + transit 留痕）。

## 9. Reporter Grounding

reporter 为纯模板渲染（I0 审计确认，本轮零改动）：输入只有结构化
itinerary/validation/notes，不查数据、不改行程、不补造事实；来源段/未核实披露/
置信度非模型自评。**金标 T-G12 + Q10 从下游固化**该原则。

**Q10 实测抓到并修复一个真缺陷**：无预算时 budget 专家把「估算总额 ¥197」写进
notes，repair 重排后费用变为 ¥180 而该金额不刷新 → 陈旧金额进行程单。修复：
notes 不再承载金额（权威数字只留费用预估段），`budget.py`。

## 10. Quality Metrics（Golden 34 条 = 22 存量 A-F + 12 新增 G 组金标）

`python -m backend.evaluation travel`（CLI choices 已补 travel 模块）实机输出 +
门禁 `tests/travel/test_quality_golden.py`（离线纪律：天气/RAG/LBS/真实路线全关，
只验证确定性管线）：

| 指标 | 门禁阈值 | 实测 | 判定 |
|------|---------|------|------|
| Q1 valid_poi_rate | =1.00 | **1.0000** | PASS |
| Q2 must_go_coverage（resolvable 口径） | =1.00 | **1.0000** | PASS |
| Q3 avoid_violation_rate | =0 | **0.0000** | PASS |
| Q4 duplicate_rate | =0 | **0.0000** | PASS |
| Q5 day_count_accuracy | =1.00 | **1.0000** | PASS |
| Q6 单日在途分钟峰值 | ≤150（=GEO_SCATTER 阈值，同口径） | **70** | PASS |
| Q7 单日负载峰值（活动+在途） | ≤780（一天时间窗物理约束） | **537** | PASS |
| Q8 budget_silent_over（静默超支） | =0 | **0** | PASS |
| Q9 hard_constraint_pass_rate | ≥0.98 | **1.0000**（期望内违反单独豁免） | PASS |
| Q10 unsupported_fact_rate | =0 | **0.0000** | PASS |

G 组 12 条场景映射：T1 基础 / T2 缺槽追问 / T3 预算 PATCH(裸数字) / T4 avoid
PATCH / T5 must-go / T6 冲突覆盖 / T7 NEW_RUN / T8 lodging / T9 预算不可能
(best effort + BUDGET_OVER 明示 + must_contain「超出预算」) / T10 未知必去 /
T14 营业时间（周一闭馆→decision_required）/ T15 grounding（来源披露）。
T11（非法 poi_id 注入）/T12（重复注入）/T13（地理离群）为结构注入场景，由
`test_pool_contract.py` 在 validator/repair 层直接覆盖（自然语言无法表达注入）。

**契约层门**：34/34 条用例契约断言全过（`test_golden_contract_all_pass`）。

## 11. Cross-turn Quality Evidence

| 用例 | 链路 | 结果 |
|------|------|------|
| T2 缺槽 | 「想去厦门」追问 → 「三天」出单 | 金标 T-G02 + E2E Q-E2E-2 PASS |
| T3 预算 PATCH | 「预算3000元」→「预算改成5000」（裸数字）→ same run 预算回显 | 金标 T-G03 + E2E Q-E2E-5 PASS |
| T4 avoid PATCH | completed 态「不去鼓浪屿了」→ 新路由通道 → 新单无鼓浪屿 | 金标 T-G04 + E2E Q-E2E-3 PASS |
| T5/T6 must-go 与冲突 | 必去鼓浪屿排入；必去后被拉黑→must_go 移除+avoid 加入 | 金标 T-G05/T-G06 + E2E Q-E2E-4 PASS |
| T7 NEW_RUN | 「不去厦门了，重新规划杭州两天」→ 杭州新单，旧 POI 零泄漏 | 金标 T-G07 + E2E Q-E2E-6 PASS |

## 12. Runtime Evidence

真实链路 `Client → APISIX:9080 → JWT → FastAPI → Router → Travel Graph → SSE`
（app 镜像按本轮代码重建，预检通过后重建；驱动
`backend/scripts/e2e_travel_quality.py` 自动断言 SSE 帧序 meta 首帧/done 恰一次）：

```text
q_e2e_1_basic:        PASS   （厦门 3 天，1179 字节，5.5s）
q_e2e_2_pending:      PASS   （追问 →「三天」出单）
q_e2e_3_avoid_patch:  PASS   （completed 态 0 信号词被新通道接住；839→806 元，鼓浪屿消失）
q_e2e_4_must_go:      PASS   （必去鼓浪屿置顶排入）
q_e2e_5_budget:       PASS   （¥3000→「预算改成5000」→ 行程单回显「预算 ¥5000」）
q_e2e_6_new_run:      PASS   （杭州新单，无厦门 POI 泄漏）

STOP_I_QUALITY_E2E_PASS=true
```

实机指标（/metrics）：`travel_candidate_total{result="retrieved"}=91`、
`travel_itinerary_validation_total{status="pass"}=9`（六场景全部一次通过，
repair 计数无样本=符合预期）。

## 13. Observability

- **Prometheus**（`backend/travel/quality_metrics.py`，独立文件对齐
  context_metrics.py 先例；标签低基数 constraint/status/result，禁
  user/conversation/poi_id/city）：`travel_candidate_total`、
  `travel_unresolved_place_total`、`travel_itinerary_validation_total{status}`、
  `travel_itinerary_constraint_violation_total{constraint}`、
  `travel_itinerary_repair_total{status}`、`travel_itinerary_quality_score`（gauge）。
- **trace**：`travel_graph_node._stamp_execution_tags` 新增
  `metadata.travel_quality = {candidate_count, scheduled_poi_count,
  must_go_unresolved}`（存量 travel_validation / travel_plan_run span 保留）。
- **结构化事件**（`[travel.quality] event=...`，与 `[travel.run]` 同风格）：
  `travel.candidates.retrieved / travel.data.unresolved_place /
  travel.itinerary.planned / travel.itinerary.validated /
  travel.itinerary.validation_failed / travel.itinerary.repaired`。
- 全部软失败（`test_quality_metrics.py` 含「指标层抛错节点照常工作」契约测试
  与高基数泄漏守护）。

## 14. Files Changed

| 文件 | 变更 |
|------|------|
| docs/2026-09-24-TravelTripQuality-STOPI0-审计与设计.md | 新增（I0 审计） |
| backend/travel/planning.py | 新增：resolve_must_go/scheduled_must_go/names_match 单一事实源 |
| backend/travel/experts/poi.py | must_go 三态接线 + 候选/未解析遥测 |
| backend/travel/graph_state.py | state 键 must_go_unresolved + planning_reset 清理 |
| backend/tools/travel/live_map.py | poi_id 进程级稳定性修复（hash→sha1） |
| backend/travel/slot_filler.py | 裸数字预算/中文数字万/lodging 城市尾缀/目的地否定语境消歧/不想去收紧/is_avoid_patch_query |
| backend/orchestration/context/travel_pending_resolver.py | avoid-PATCH 路由分支（completed 态） |
| backend/travel/models/validation.py | CODE_POI_DUPLICATED / CODE_POI_NOT_IN_CANDIDATES |
| backend/travel/validator.py | pool 轴 + 跨天重复 + coverage 口径统一 + 遥测 |
| backend/travel/repair.py | 两类新违反的修复动作 + 遥测 |
| backend/travel/experts/transit.py | 候选池外 id 显式留痕 + planned 事件 |
| backend/travel/experts/budget.py | 陈旧金额修复（Q10 实测产出） |
| backend/evaluation/travel_quality.py | 新增：Q1-Q10 确定性度量+聚合 |
| backend/evaluation/runners/travel.py | 质量快照/metrics 摊平/budget_cny 断言 |
| backend/evaluation/cli.py | module choices += travel |
| backend/evaluation/datasets/travel/{cases.jsonl,manifest.json} | 22→34 条（G 组金标） |
| backend/travel/quality_metrics.py | 新增：Prometheus 指标+结构化事件 |
| backend/orchestration/graph/travel_graph_node.py | trace 候选池漏斗字段 |
| backend/tests/travel/（+7 文件，存量 2 文件增量修订） | planning_contract / slot_patch_boundary / pool_contract / quality_golden / quality_metrics / travel_dataset 契约扩展 / validator 一处夹具 |
| backend/scripts/e2e_travel_quality.py | 新增：Q-E2E-1~6 驱动 |

## 15. Tests

| command | passed | failed | exit |
|---------|-------:|-------:|-----:|
| `pytest tests/travel/ tests/orchestration/ -q --no-cov`（首轮） | 1067 | 1* | 1 |
| `pytest tests/travel/ tests/orchestration/ tests/test_stop_g4_matrix.py -q --no-cov`（STOP H 基线命令，复跑） | **1087** | **0** | **0** |
| `pytest tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py -q --no-cov` | 36 | 0 | 0 |
| `pytest tests/travel/test_quality_golden.py`（质量门禁） | 2 | 0 | 0 |
| `python -m backend.evaluation travel --smoke` | 5/5（100%） | 0 | 0 |

\* `test_market_research_smoke.py::test_llm_failure_degrades_to_skeleton`
（workflow LLM 降级冒烟，非 travel 路径）首轮超时抖动，单跑复现 PASS；与本轮
diff 无交集，复跑全绿。

基线 1032 → 1087：净增 55 条全部为本轮新增守护测试。

## 16. Commits

| hash | subject | scope |
|------|---------|-------|
| 8b1f5ff | docs(travel): STOP I0 审计与设计 | I0 |
| cdb6159 | feat(travel): STOP I1 canonical POI contract | I1 |
| 68af9a7 | feat(travel): STOP I2 avoid-PATCH 路由通道+slot 抽取边界 | I2 |
| 81c0543 | feat(travel): STOP I4 close validation and repair loop | I4 |
| c566534 | test(travel): STOP I5 itinerary quality golden evaluation | I5 |
| b9f62c8 | obs(travel): STOP I6 quality telemetry | I6 |
| 6129a8a | test(travel): STOP I 质量 E2E 驱动 | I6 |

（I3 无独立代码增量：I0 审计确认现有 planner 即确定性实现，其质量由 I5 金标
覆盖；提交拆分与任务书建议一致。）

## 17. Parallel Workspace Protection

- 开工即记录检测到的他人 WIP（context_budget/customer_service/rag 及过程中新
  出现的 memory/tasks/orchestration/state 等约 20 文件）——**未触碰、未收编、
  未暂存**；全部提交均 `git add -- <files> && git commit -- <files>` 双重
  pathspec 限定。
- app 镜像重建前做了预检：工作区全部改动 py 文件 `py_compile` 通过 + app 入口
  import 通过（含并行会话新增 memory 模块），确认不会把不可用代码烘进共享栈；
  重建仅 `app` 单服务（不动 postgres/redis/apisix/workers），重建后 health=ok。
- 无 accidental staging；未执行 reset --hard / checkout . / clean / stash。

## 18. Deferred（全部非阻塞）

1. **Live Maps/Transit Provider 深化**（STOP J 范畴）：LBS 已有坐标/路线/天气接
   口，营业时间/票价等事实字段仍为占位+unverified——需要票务类 provider 契约。
2. **Hotel / flight inventory、实时票价**：外部供给集成（STOP J）。
3. **大规模路线优化**（TSP 类算法）：当前最近邻+聚类在种子池规模（≤11 POI/城）
   下 Q6 实测 70 分钟峰值，规模扩大后再引入。
4. **LLM Judge 辅助评价**（行程自然度/理由合理性）：非门禁项，暂未接入。
5. **大模型口语抽取兜底**（「十一前后」类模糊日期）：config 中已留口径位
   （slot_filler P1 说明），不影响现有确定性抽取。

核心质量问题（POI 幻觉 / avoid 违反 / must_go 缺失 / 重复行程 / 非法 POI /
静默超支 / unsupported fact）**全部在本轮关闭，无一项 Deferred**。

## 19. 最终 Gate（任务书 §79 逐条）

| Gate | 判定 | 证据 |
|------|------|------|
| G1 canonical POI contract 存在 | ✅ | §4 + planning.py |
| G2 POI source 可追溯 | ✅ | source/observed_at/verification_status + 来源段 |
| G3 LLM 不能创造 canonical POI | ✅ | 域图零 LLM + pool 轴 error + Q1=1.0 |
| G4 must_go 可确定验证 | ✅ | resolve_must_go 三态 + coverage 轴 + Q2=1.0 |
| G5 avoid 可确定验证 | ✅ | is_excluded 硬过滤 + Q3=0 |
| G6 avoid PATCH 跨轮生效 | ✅ | 新路由通道 + 金标 T-G04 + E2E Q-E2E-3 |
| G7 duplicate=0 | ✅ | POI_DUPLICATED + Q4=0 |
| G8 requested days 严格满足 | ✅ | Q5=1.0（候选不足场景诚实缩天+披露为既有契约） |
| G9 invalid POI 被 validator 拒绝 | ✅ | pool 轴 + test_pool_contract（T11） |
| G10 deterministic itinerary constraints 已接线 | ✅ | 八轴 validator + supervisor 只认状态事实 |
| G11 geographic scheduling 有可计算依据 | ✅ | Haversine/腾讯路线 + day_radius_km + Q6=70min |
| G12 budget 不再静默假满足 | ✅ | BUDGET_OVER 明示 + repair 逼近 + budget_silent_over=0（T9） |
| G13 unknown data 不伪造 | ✅ | unverified 披露 + unresolved 话术 + T10 |
| G14 reporter unsupported fact=0 | ✅ | Q10=0（并实测抓修一处陈旧金额） |
| G15 repair 有界且无死循环 | ✅ | MAX_REPAIR_ROUNDS+repair_stalled+防震荡签名（回归全绿） |
| G16 quality golden suite 建立 | ✅ | 34 条金标 + G 组 12 场景 + CLI travel |
| G17 core quality metrics 达标 | ✅ | §10 全表 PASS |
| G18 真实 gateway E2E 通过 | ✅ | §12 六场景 STOP_I_QUALITY_E2E_PASS=true |
| G19 existing runtime regression=0 | ✅ | 基线命令 1087/0/exit=0 |
| G20 parallel workspace clean | ✅ | §17 |

---

## 20. 冻结声明

```text
TRAVEL_ITINERARY_QUALITY_FROZEN=true
```

冻结范围：POI 数据契约（planning.py 单一事实源 / poi_id 稳定性 / unverified
语义）、validator 八轴与违反码、repair 有界修复契约、quality golden 门禁
（tests/travel/test_quality_golden.py 阈值即冻结基线，修改阈值须同步本报告
§10）、低基数遥测标签。冻结内允许：bugfix、种子数据扩城/扩点（须过金标门禁）、
新增 not_evaluable 约束的数据源接入。禁止：绕过候选池成员轴、绕过金标门禁改
排程算法、给 validator 加 LLM 判定、re-introduce 静默修复。

下一阶段：STOP J — Live Travel Providers（地图/票务/天气/酒店/航班真实供给）。

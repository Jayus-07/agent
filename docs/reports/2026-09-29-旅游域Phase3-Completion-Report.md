# Phase 3 Completion Report — Research / Planning / Optimization Agent 边界建立

> 格式依用户施工军规 §十二（八项）。基线：Phase 2（`242430d` / `df36bca`）+ Phase 3 审计（`c350c15`，D1-D4 全部确认 + Node/Service 两条补充边界约束）。

---

## 1. Verdict

**PASS**（附两项如实披露：① `test_provider_layer` 的 t25 为存量已知失败——qweather_backup 已入 health 而测试预期未更新，STOP F 收官记忆已登记"待补"，基线即如此，非本次引入；② Phase 2 时段已知的 booking 真 PG 分块宿主机挂死未在本阶段重跑，booking 目录零 diff）。

## 2. 修改文件

**Commit A `26a7c8c`（11 文件，+745/-610）— Service 边界建立**：

| 文件 | 操作 | 内容 |
|---|---|---|
| `services/poi_service.py` | 新增 | retrieve_candidates（V1 收敛：search_poi + 必去项腾讯补全）+ build_skeleton/_build_notes/Skeleton 真身 |
| `services/transit_service.py` | 新增 | order_pois/schedule_day/rebuild_days（**唯一重排实现真身**）/build_itinerary/prefetch_day_legs（V3 收敛） |
| `services/weather_service.py` | 新增 | fetch_forecast（V2 收敛，七态降级映射逐字保留）+ is_bad_weather/bad_weather_dates/plan_weather_swaps（重排改调 transit_service） |
| `services/budget_service.py` | 新增 | estimate_cost Tool 归属点（只算不判） |
| `services/risk_service.py` | 新增 | assess_risks 真身 + retrieve_knowledge 软失败封装 |
| `experts/{poi,transit,weather,budget,risk}.py` | 改造 | 纯函数体下沉、节点编排保留、裸名兼容面（本模块命名空间 = 测试补丁缝） |
| `tests/travel/test_travel_graph.py` | 补丁缝迁移 1 处 | search_poi → retrieve_candidates（search_poi 已下沉 service；行为断言不变） |

**Commit B `3de085a`（8 文件，+179/-20）— Agent Wrapper 接入**：

| 文件 | 内容 |
|---|---|
| `agents/research_agent.py` | ResearchAgent：retrieve_candidates / fetch_forecast / assess_risks / retrieve_knowledge |
| `agents/planning_agent.py` | PlanningAgent：build_skeleton |
| `agents/optimization_agent.py` | OptimizationAgent：prefetch_day_legs / build_itinerary / plan_weather_swaps / estimate_cost |
| `experts/*.py` ×5 | Agent 能力裸名改为委托函数（Node → Agent → Service）；节点 Runtime 职责逐字保留 |

**Commit C `6882ee7`（1 文件，+269）— 边界契约测试**：`tests/travel/test_agent_service_boundary.py` 18 例。

## 3. 为什么这样改

依审计报告与 D1-D4 确认：**Agent 边界建立而非图收敛**。三层职责冻结为——

- **Node（experts 五节点，门牌不动）**：state 读写、load/save 契约转换、`run_expert_safely` 包装、stamp_version、遥测（qm）、expert_history、planning_reset 类编排——全部逐字保留；
- **Agent（research/planning/optimization）**：能力语义编排，无状态、零 LLM、零 state 读写、只调 Service、禁止互调；
- **Service（五 service，expert 命名 1:1）**：算法真身（逐字搬运）+ **全域唯二 Provider 触达层**（poi_service 补全 / weather_service 取数；transit 预热经 live_map Tool）——Phase 4 ProviderRouter 插入点由此唯一。

补丁缝设计：expert 模块命名空间保留同名裸名（Commit A = service 函数 re-export；Commit B = 委托 Agent 的函数），`monkeypatch.setattr(W, "fetch_forecast", ...)` 类存量补丁语义不变（实战验证：weather 4 例补丁用例恢复全绿）。

## 4. 冻结契约对应

| 冻结条目 | 落地 |
|---|---|
| STOP F：run_expert_safely/遥测形态逐字节冻结 | ✅ 签名 `(expert_name, fn, state)`/status 枚举/TravelExpertType 五成员/expert_history 形态——契约测试逐项断言；node_runtime parity 40 例全程绿 |
| 五节点注册顺序（D1） | ✅ add_node 序列 slot_filler→supervisor→poi→transit→weather→budget→risk→validate→repair→report 冻结断言；graph_builder 零改动 |
| Provider 治理（Agent/Node 不得直连） | ✅ V1/V2/V3 收敛进 service；全域直连点唯一性静态扫描（越界即红）；Router 本体未提前实现（Phase 4） |
| repair/validator/reporter 独立（禁止吞并） | ✅ 三文件零 diff；repair 的 rebuild_days import 经 re-export 同一真身（`is` 断言） |
| 算法零改动 / 金标零回退 | ✅ 34 条金标全程绿；纯函数逐字搬运 |
| planning.py 原位（D2）/ Evidence 延期（D3）/ service 命名（D4） | ✅ 全部照办 |
| Phase 2 代码不修改 | ✅ requirement_agent/requirement_service/slot_filler 零 diff（契约测试同批覆盖其边界一致性） |

## 5. 测试结果（全部实跑）

| 分块 | 结果 |
|---|---|
| 契约测试（Commit C） | **18 passed** |
| 专家重灾区：weather_expert + provider_layer + pool_contract + p0_mvp + quality_metrics + 金标 | 69 passed（+t25 存量失败 1） |
| 全量分块：travel_graph + scenarios + validator + repair + versioning + persistence + checkpointer + cancel_lifecycle + planning_contract + pending_resolver + 一致性三门 + node_runtime parity | **304 passed** |
| Commit A 独立轮（Commit B 之前）：431 passed | 金标 34 零回退 |
| **两轮合计去重** | **约 500 例全绿**（覆盖五专家/门禁/路由/checkpointer/一致性/契约面） |

过程中发现并修复：① 测试补丁缝失效 3+1 处（weather fetch_forecast ×4 用例、travel_graph search_poi ×1）——根因是 patch 目标在旧模块命名空间，以"裸名委托函数"模式恢复缝语义，仅 1 处测试补丁目标更新（行为断言不变）；② 无其他行为偏差。

## 6. 未完成事项

1. t25（qweather_backup 补录测试预期）——存量债，建议随 Phase 4 provider 面工作顺带修（一行）；
2. booking 真 PG 分块回归（宿主机挂死，容器第二路径待跑）；
3. Evidence 附加 + validator 3 项 warning（D3 已确认推迟 Phase 4）；
4. ProviderRouter 实现（Phase 4：primary→fallback→stale→七态，weather 首消费者 parity=qweather 15 用例）；
5. 图收敛（3 节点 + supervisor stage 重映射）——与 Phase 5/6 意图收编同批设计（D1 确认延后）；
6. 2-opt 路径优化（route.optimizer 接口，OptimizationAgent 增强位已预留）；
7. 死代码清理：reschedule_after_repair（留守 expert，Phase 8 删）。

## 7. 风险

| 风险 | 级 | 状态 |
|---|---|---|
| STOP F 遥测形态漂移 | 高 | 已消解：契约断言 + parity 40 例绿 |
| 算法搬运手误 | 高 | 已消解：逐字搬运 + 金标 34 零回退 + 两轮 500 例回归 |
| 补丁缝失效（运行时测试盲区） | 高 | 已消解并制度化：裸名委托模式 + 契约测试含补丁缝语义注释 |
| weather_service→transit_service 跨 service 依赖成环 | 中 | 已消解：单向（transit 不回依赖 weather），契约扫描覆盖 |
| Provider 直连残留 | 中 | 已消解：全域唯一性静态扫描（rglob travel/ 排除冻结域） |
| Agent 层未来被塞 Tool/Provider import | 中 | 已消解：三 Agent + requirement_agent 静态断言（requirement 按 Phase 2 冻结形态单列） |
| booking 回归缺口 | 低 | 目录零 diff；Phase 8 收口兜底 |

## 8. Commit

```
26a7c8c refactor(travel): Phase3 Commit A——五专家算法真身下沉 service 层（逐字搬运零改动）
3de085a refactor(travel): Phase3 Commit B——Research/Planning/Optimization Agent 封装接入
6882ee7 test(travel): Phase3 Commit C——Agent/Service/Node 边界契约测试 18 例
```

**下一阶段**：Phase 4——Provider 归位 + ProviderRouter + Evidence + TTL（企业级 Runtime 核心三件套，用户明令不得混入 Phase 3，现全部就绪待指令）。

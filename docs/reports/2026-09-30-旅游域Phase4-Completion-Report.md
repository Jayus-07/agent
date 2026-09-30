# Phase 4 Completion Report — Provider 归位裁决 + ProviderRouter + Evidence 数据可信面

> 格式依用户施工军规（八项）。基线：Phase 3（`26a7c8c` / `3de085a` / `6882ee7`）+ Phase 4 审计报告（`d4867d2`，D1-D7 全部确认，含三项现场调整：不建 core/evidence.py 只建 evidence_utils / compute_confidence 本阶段零改动 / research 评测集延后）。

---

## 1. Verdict

**PASS**（附三项如实披露：① 施工中发现并修复 **2 处存量测试缺陷**（`test_core_contracts._ts()` 时间炸弹 + `test_travel_graph` 确定性测试时钟字段剥离不完整）——均与 Phase 4 行为无关，根因与修复见 §3/§5；② booking 真 PG 分块按既定纪律留容器第二路径，本阶段 booking 目录零 diff；③ research freshness/coverage 评测集按用户调整延后，v4 §10 Phase 4 注入项剩余此一项）。

**Provider physical relocation deferred to Phase 8 re-evaluation**（D1 拍板，G4 台账 E11 登记）。

## 2. 修改文件

**Commit A `7f35529`（7 文件，+248/-19）— Provider 层：Router + TTL/超时 + t25**：

| 文件 | 操作 | 内容 |
|---|---|---|
| `providers/travel/live/router.py` | 新增 | PROVIDER_CHAINS 四链声明（weather.forecast/maps.route/poi.search/price.quotes，单一事实源）+ `assemble_weather_provider()`（链账驱动装配，产物与原硬编码逐字节同构）+ `ttl_for`/`timeout_for` 薄读口（读共享表，不复制第二份） |
| `providers/travel/live/__init__.py` | 修改 | 仅 `get_weather_provider()` 函数体改经 Router 装配（惰性 import 防循环）；FallbackWeatherProvider/单例工厂/导出表零改动 |
| `providers/travel/live/cache.py` | 修改 | FRESH_TTLS：place 600→86400 / route 120→1800 / weather 300→600（ticket/commerce/NEGATIVE/GRACE 不动；键格式不变，存量自然过期）+ 头部注释同步（J0 文档义务） |
| `providers/travel/live/resilience.py` | 修改 | TIMEOUT_BUDGETS weather 缺省 6.0→5.0 + 注释（生效点唯一在 resolve_budget 接线） |
| `config/travel.py` | 修改 | `TRAVEL_WEATHER_TIMEOUT_S` 默认 "6"→"5"（已核验 .env 无覆盖） |
| `tests/travel/test_provider_layer.py` | 修改 | t25 一行：health 期望键集补 `qweather_backup`（存量债顺带，实跑先红后绿） |
| `tests/travel/test_provider_router.py` | 新增 | Router 契约测试 9 例（链账完整性/装配 parity/工厂入口不变/未知成员 fail-fast/薄读口） |

**Commit B `a80a594`（17 文件，+799/-34）— Evidence + validator 数据可信面**：

| 文件 | 操作 | 内容 |
|---|---|---|
| `travel/core/evidence_utils.py` | 新增 | Evidence 组装/序列化/过期判定**转换薄层**（模型唯一归属 core/contracts.py，无第二套证据模型）；make_evidence 基线定档+缺 verified_at 压帽 0.5；evidence_to_dict enum/datetime 感知；is_stale 无 expire_at 不判 |
| `travel/graph_state.py` | 修改 | 声明 `evidences: dict[str, dict]`（注释写明三纪律：不预置/.get/合并写入） |
| `travel/services/poi_service.py` | 修改 | `build_candidate_evidences`：种子→SEED/0.5/expire_at=None；腾讯补全→LIVE/0.95/verified_at=observed_at（缺失压帽）；纯函数派生，检索逻辑零改动 |
| `travel/services/weather_service.py` | 修改 | 新增 `fetch_forecast_evidence` 三元组（status×Freshness→LIVE/CACHE、stale 降 0.6、expire_at=observed_at+TTL(600s)、source=result.provider 实际服务源）；`fetch_forecast` 保留为兼容 wrapper |
| `travel/services/risk_service.py` | 修改 | `build_knowledge_evidence`：RAG/0.7、检索时点即 verified_at、value 存索引控 checkpoint 体积 |
| `travel/agents/research_agent.py` | 修改 | 三个委托方法（fetch_forecast_evidence/build_candidate_evidences/build_knowledge_evidence），依赖纪律不变 |
| `travel/experts/{poi,weather,risk}.py` | 修改 | 裸名委托函数（补丁缝模式）+ 节点 evidences 合并写入 `{**state.get("evidences",{}), **new}` |
| `travel/models/validation.py` | 修改 | 三个 CODE_* 常量（POI_UNVERIFIED/SOURCE_STALE/PREFERENCE_VIOLATION） |
| `travel/validator.py` | 修改 | `check_source_trust`/`check_preference` 两检查函数（全 LEVEL_WARNING、零 IO、不进六轴由节点追加）；PREFERENCE 判定复用 `tools/travel/poi.is_excluded` 单一语义源；**compute_confidence 零改动**（D5 调整：warning 走既有 -0.05/条自然计分）；结论映射不触及 |
| `tests/travel/test_evidence_validator.py` | 新增 | 契约测试 26 例（utils/三组装点/三 warning 生命周期/state 纪律与合并/补丁缝） |
| `tests/travel/test_weather_expert.py`（×3）/ `test_scenarios.py`（×2） | 修改 | patch 目标 fetch_forecast→fetch_forecast_evidence（返回 2-tuple→3-tuple，行为断言不变） |
| `tests/travel/test_agent_service_boundary.py` | 修改 | 符号解析表补三个新裸名 |
| `tests/travel/test_core_contracts.py` | 修改 | **存量修正①**：`_ts()` 固定 `2026-09-29T12:00Z` 时间炸弹（expire_at=+1h 自 09-30 起必红）→ 改 now 相对基准 |
| `tests/travel/test_travel_graph.py` | 修改 | **存量修正②**：确定性测试补剥离 `legs[].observed_at` 时钟字段（STOP J9 剥离模式补全） |

**Commit C（本提交，4 文件）— 收口修正 + G4 登记 + 本报告**：

| 文件 | 操作 | 内容 |
|---|---|---|
| `tests/travel/test_provider_layer.py` | 修改 | **补丁缝收尾**：t9/t10 两处 patch 目标 fetch_forecast→fetch_forecast_evidence（2-tuple→3-tuple）——首轮全量分块暴露，属 Commit B 五处枚举的遗漏（rg -ln 命中该文件但只查了 t25 上下文；教训再登记：枚举必须逐文件全量核对） |
| `tests/travel/test_travel_core_boundary.py` | 修改 | **core/ 文件集锁定登记**：EXPECTED_CORE_FILES 有意追加 evidence_utils.py（预期集合变更是设计决策非放宽，测试头部注释即此纪律） |
| `docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md` | 修改 | §4 追加 **E11**（归位延后 Phase 8 + events/notice 只登记不建空壳 + 城市级 POI 源 BLOCKED + unverified 字面量对齐说明） |
| `docs/reports/2026-09-30-旅游域Phase4-Completion-Report.md` | 新增 | 本报告 |

## 3. 为什么这样改

- **归位裁决（D1）**：46 个 import 方中 11 个属冻结交易域，git mv 需豁免冻结域接触或 shim 双轨；Phase 3 已达成"域内闭环"实质（services 唯一触达层+静态扫描），目录搬迁收益只剩美学。原位 + Router 内聚 = 零风险拿到全部架构收益。
- **组装期链账装配（D2）**：链的执行语义（fresh→stale-if-error→七态失败）已内嵌 adapter 内的 STOP J 冻结基建，调用期链执行器=第二套降级语义。Router 只做"声明+装配+薄读口"，weather_service 一行未改即享 Router。
- **Evidence 统一 state 键（D3）**：validator SOURCE_STALE 单点消费、Phase 6 Assistant fact_id 引用单点、checkpoint 序列化纪律单点测试面；Poi 原生字段纯函数派生，无双份簿记。provider 层零改动——全部组装在 service 层（Phase 3 边界红利）。
- **三元组新函数而非改元组（D4）**：保住 fetch_forecast 补丁缝；5 处 patch 目标机械更新（审计估 4 处，精确枚举 5 处）。
- **两处存量测试修正的归因**（均非 Phase 4 引入，证据：git 零 diff 的文件、失败机理与本次改动无交集）：① `_ts()` 是 Phase 1 写死的绝对时间，"fresh"断言活不过 09-30；② 确定性测试两轮间隔被 run1 risk 节点的 RAG 初始化重试（known debt：knowledge.py 无显式超时，审计 §4 已登记）拉到 16 秒，legs 的 `observed_at`（STOP J 时效语义，秒级逐腿取时）必不同——STOP J9 当天只剥离了 created_at。修复均为测试侧剥离/相对化，生产行为零变更。

## 4. 冻结契约对应

| 冻结条目 | 落地 |
|---|---|
| v4 §8 ProviderRouter 选择链 | ✅ PROVIDER_CHAINS 四链声明单一事实源；weather 链装配逐字节同构（主败才降级/DISABLED 不降级/双败保主因，qweather 15 用例绿）；map/poi/price 先声明后接管 |
| v4 §8「Agent 不选 Provider」 | ✅ 触达面只在 service 层（Phase 3 已达成）；Router 在 provider 层内部，Agent/Node 零感知（边界静态扫描持续覆盖） |
| v4 §4 Evidence 五字段+confidence 基线 | ✅ 模型唯一归属 core/contracts.py 零改动；组装/序列化/判定辅助收敛 evidence_utils 单一出口；SEED 不冒充时效（expire_at=None）、缺 verified_at 压帽 0.5、stale 降 0.6 |
| v4 §4「provider 层零改动，域层组装」 | ✅ tencent/qweather/cache 契约面零改动（cache.py 仅 TTL 常量值——用户 D7 拍板的配置项，非契约结构） |
| validator/repair/reporter 门禁独立 | ✅ validator 只加法（两纯函数+节点追加段）；repair/reporter 零 diff；warning 不设 error、不翻结论 |
| STOP F run_expert_safely/遥测 | ✅ 三节点改造都在包装之内（裸名委托+update 键追加）；node_runtime parity 全程绿 |
| checkpoint 跨轮契约 | ✅ evidences 不进 new_travel_graph_input、.get() 读取（旧 checkpoint 无键不误报）、纯 dict 可序列化 |
| commerce/booking 冻结交易域 | ✅ 零接触（两阶段共 24 文件改动无一涉冻结域） |
| Agent/Node 调用方式 | ✅ 节点仍经 Agent 委托（裸名补丁缝模式复刻）；无第二套 fallback/router/evidence 抽象 |

## 5. 测试结果（全部实跑）

| 分块 | 结果 |
|---|---|
| Commit A：provider_layer+providers+qweather+router 契约 | **77 passed**（t25 修复后） |
| Commit A：node_runtime parity | **14 passed** |
| Commit A：commerce 全块（TTL 波及面） | **111 passed** |
| Commit A：一致性四测试 | **54 passed** |
| Commit B：补丁缝先验（weather_expert+scenarios） | **32 passed** |
| Commit B：新契约 test_evidence_validator | **26 passed** |
| Commit B：validator+user_decision+quality_metrics+core_contracts | **92 passed**（_ts 修复后） |
| Commit B：travel_graph（含确定性测试）+金标 34+边界 | **全绿**（observed_at 剥离后） |
| Commit C 收口轮：travel 全量分块（--ignore=booking，真 PG 分块按纪律留容器路径） | **735 passed 全绿**（首轮 733+2 失败：t9/t10 补丁目标遗漏 + core 文件集锁定未登记 → 修复后重跑全绿） |
| Commit C 收口轮：node_runtime 全块 | **48 passed** |
| Commit C 收口轮：一致性四测试 | **54 passed** |
| Commit C 收口轮：`gen_tool_contract_lock --check` | **IN_SYNC**（BREAKING=0，tool 函数签名零改动） |

**两处存量缺陷修复前后对照**：`test_live_fresh_is_trusted` 修复前 FAIL（may_change≠trusted）/修复后 PASS；`test_plan_is_deterministic` 修复前稳定 FAIL（observed_at 16s 差）/修复后 PASS。

## 6. 未完成事项

1. **research freshness/coverage 评测集**——用户 C 调整延后（v4 §10 Phase 4 注入项唯一剩余）；基线口径已在 evidence_utils/评测集设计中备好，随评测批次立项；
2. **booking 真 PG 分块回归**——宿主机挂死既定债，容器第二路径待跑（booking 目录零 diff）；
3. **confidence 权重统一评估**——D5 调整：本阶段只验证 warning 生命周期，新 warning 的扣分权重与既有 -0.05 同权自然生效，专项评估后续做；
4. **knowledge.py 无显式超时**（known debt）——本轮实测它以 16.9s RAG 重试的形式触发了确定性测试的时钟字段暴露；属 v3 Phase 3 卡遗留项，独立修；
5. Provider physical relocation——**Phase 8 与 shim 删除同批重估**（E11）。

## 7. 风险

| 风险 | 级 | 状态 |
|---|---|---|
| Router 装配与原工厂不等价 | 高 | 已消解：装配分支逐字复刻，工厂两用例+qweather 15 例绿 |
| 补丁缝失效（5 处 patch 目标） | 高 | 已消解：裸名委托三层同步+先验轮 32 例绿 |
| evidences 合并丢失（无 reducer 键） | 中 | 已消解：三节点统一合并写入+契约测试断言跨节点留存 |
| confidence 分数漂移 | 中 | 按用户调整验收：生命周期断言全绿，绝对值评估延后（§6-3） |
| TTL 放大（place 24h）缓存陈旧 | 低 | v4 拍板值；traffic_aware/is_estimate 字段语义自带；commerce 111 例绿 |
| checkpoint 体积（evidences） | 低 | 摘要不存全文（~12KB 级）；序列化纪律测试覆盖 |
| 存量测试时间炸弹复发 | 低 | 已修 2 处；本轮全量分块再验证 |
| 并行会话冲突 | 中 | pathspec 双重限定三提交，各自可独立 revert |

## 8. Commit

```
7f35529 refactor(travel): Phase4 Commit A——ProviderRouter 组装期链账装配+TTL/超时调整+t25 顺带修复
a80a594 feat(travel): Phase4 Commit B——Evidence 统一证据表+validator 数据可信三 warning（全 WARNING 级零 IO）
<本提交> docs(travel): Phase4 Commit C——G4 台账 E11 登记+Completion Report
```

**下一阶段**：Phase 5——Commerce 统一 + D2 修复（BOOKING 审批统一外壳、booking_intent 并入 pending_approval 模型），待用户指令。

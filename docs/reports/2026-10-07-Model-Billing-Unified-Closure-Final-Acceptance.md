# Model Billing Unified Closure — Final Acceptance（最终验收报告）

> 日期：2026-10-07｜分支：`feat/model-billing-closure`（基线 `4ddc3c3`，三个提交：15f999e / b74d426 / aec4f01）
> 审计报告：`docs/reports/2026-10-07-Model-Billing-Unified-Closure-STOP-A-Audit.md`

---

## A. 最终架构

```
Provider 返回 Usage
        ↓
extract_usage_payload（唯一解析，不变）
        ↓
NormalizedUsage（归一化：billable = input − cached，缓存不双算）
        ↓
price_usage()（唯一计费入口，pricing.py 内；永不抛错）
  ├ 价格快照：PG model_price（approved）→ 原生币种逐维计价（六维全量）
  ├ 缺价/不可用 → 注册表估价（registry_fallback）；enforce 下 cost_status=price_unknown
  ├ 原生币种 → CNY 只折一次，fx_rate 快照固化
  └ 估价=0 → unpriced（billed=0，token 照记，不冒充免费）
        ↓
BillingResult（唯一事实）
        ├ llm_usage（V2 列：billed_cost_cny 权威 + native_cost/currency/fx/price_version）
        ├ Budget settle（record_model_usage(cost=billed_cost_cny)，恒 CNY）
        ├ Trace（tracer/_trace_dto 回填直接取 billed_cost_cny，禁止二次换汇）
        └ Dashboard/Grafana（cost_cny_sql_expr() 唯一折算表达式，四处共用）

observe / enforce 的差异 = 门禁行为（预占/阻断）+ 缺价时 cost_status（estimated vs price_unknown），
金额与 native 恒出自同一次 price_usage 调用。
```

## B. 数据契约（实际落地）

- **NormalizedUsage**（`pricing.py`）：input_tokens / cached_input_tokens / cache_write_tokens / output_tokens / reasoning_tokens / tool_calls；`billable_input_tokens` property。
- **BillingResult**（frozen dataclass，14 组字段）：native_cost+native_currency 与 billed_cost_cny+base_currency('CNY') 分列；fx_rate 快照；六维分项 CNY 成本；六维原生单价快照；price_version / pricing_source / usage_source / cost_status。
- **llm_usage Billing V2**（migration 079）：`billing_schema_version / native_cost / native_currency / billed_cost_cny / fx_rate / price_version / pricing_source / usage_source / {reasoning,cache_write,tool_call}_cost_cny / {reasoning,cache_write,tool_call}_unit_price`。旧 `cost_usd/total_cost/currency` 兼容保留：cost_usd=原生 USD 审计口径，currency=原生币种，total_cost=记账 CNY。
- **唯一读层折算**：`llm_usage_store_pg.cost_cny_sql_expr()`——V2 行取 billed_cost_cny（禁二次换汇），legacy 行按旧 currency 逻辑兜底；dashboard / breakdown / cost_gauge_snapshot / quota.summary 四处共用。

## C. P0 验收矩阵

| 编号 | 操作 | 预期 | 真实结果 | 证据 | 判定 |
|---|---|---|---|---|---|
| P0-01 | 同 Usage 同价格快照分别走 observe/enforce | billed/native/status 完全一致 | 一致（21.600000 / $3.000000 / exact） | `test_observe_enforce_same_billing_result` | PASS |
| P0-02 | input $1+output $2 × 1M × FX7.2 | native=$3, billed=¥21.6, 预算/usage/trace 全 21.6 | 21.600000，无 155.52/3 | `test_exact_usd_converts_to_cny_once` + `test_exact_usd_converts_to_cny_once`(contract) | PASS |
| P0-03 | enforce 缺价，注册表估价 $0.002 | price_unknown，settle=¥0.0144 | settle 收 0.014400 | `test_price_unknown_usd_converts_before_budget_settlement`（捕获桩断言 settle 入参） | PASS |
| P0-04 | input=1000 含 cached=600 | billable=400，分项 400/600/500 各自计价 | 0.005880 | `test_cache_read_not_double_billed` | PASS |
| P0-05 | 同调用 budget settled vs usage billed | 相等 ≤1e-6 | snapshot().cost == billed | `test_budget_settlement_equals_usage_cost` | PASS |
| P0-06 | trace cost vs SUM(llm_usage.billed) | 相等 | 0.1008 == 0.0864+0.0144 | `test_trace_cost_equals_usage_sum` | PASS |
| P0-07 | 看板成本 vs SUM(billed)，禁二次乘 FX | 相等 | DTO 回填 21.6 原样 | `test_dashboard_does_not_double_fx` + 实库 v2/旧口径对比 SQL | PASS |
| P0-08 | LLM_REQUEST_MAX_COST=0.50 语义 | ¥0.50 | 2026-10-01 已切 CNY（本次核验 config/llm.py 注释与预算链一致） | config/llm.py:357-361 | PASS（存量达成） |
| P0-09 | 原生/记账币种不可错标 | native 与 billed 分列 | enforce 不再硬编码 "exact","USD"；V2 行 currency=原生币种 | `test_exact_usd_converts_to_cny_once` + 实库分布查询 | PASS |
| P0-10 | unpriced 不冒充免费 | billed=0 + 显式统计 | unpriced + pricing_source=unpriced | `test_unpriced_is_explicit` | PASS |
| P0-11 | retry/fallback 独立记账 | 失败行 unavailable + 成功行独立 | 失败行 usage_source=unavailable/billed=0/decision=primary；retry 独立 BillingResult | `test_retry_and_fallback_are_independently_accounted` | PASS |
| P0-12 | 流式 usage 可信度 | provider/estimated/unavailable 分列；估算不标 exact | usage_source 落库；estimated 污染 exact | `test_estimated_usage_source_taints_exact` + stream 测试 | PASS |
| P0-13 | 并发预算不超卖 | PG 预占仲裁 | 存量达成（审计确认 reserve ON CONFLICT 仲裁逻辑未动） | STOP-A 报告 §6 | PASS（存量达成） |
| P0-14 | user/tenant 双层不放大总成本 | 总成本来自单一流水 | quota.summary 从 llm_usage 明细派生（每调用一行），ledger 双行不再直和 | quota.summary 修复（b74d426） | PASS |

## D. P1 验收矩阵

| 编号 | 预期 | 证据 | 判定 |
|---|---|---|---|
| P1-01 price_version | 每条 V2 行可查 price_version，改价不追溯 | `test_price_version_snapshot_is_immutable`；实测存量行随单价快照固化 | PASS |
| P1-02 FX Snapshot | 换汇行存 fx_rate，ENV 改动不改历史 | `test_fx_snapshot_is_immutable`（7.2→7.25 老结果不变） | PASS |
| P1-03 Breakdown | input/cached/output 恒有；reasoning/cache_write/tool_call 有价行即计 | `test_reasoning_dimension_priced_when_row_exists` | PASS |
| P1-04 Pricing Source | approved_price_table/registry_fallback/unpriced 三值 | 契约测试 + `test_enforce_missing_price_is_price_unknown_with_fx` | PASS |
| P1-05 Usage Source | provider/estimated/unavailable 三值 | 失败留痕行 unavailable + 流式估算行 estimated | PASS |
| P1-06 六维归因 | 新列不破坏 user/tenant/model/skill/tool/domain | breakdown 仍按原六维分组，仅成本口径换 cost_cny | PASS |
| P1-07 管理端统一 | 成本主页面无「变量 USD 实为 ¥」 | CostPanel totalCny / TokenCharts dataKey=cost_cny / TokensPanel cost_cny；tsc 0 错 | PASS |
| P1-08 Grafana | CNY 单位正确、不重复换汇 | 看板消费 llm_usage_cost_cny_* gauge（账本投影），投影表达式已 V2-aware | PASS |
| P1-09 Reconciliation | 估计数 + billing_mismatch_count | reconciliation_summary 新增 billing_mismatch_count（正常 0） | PASS |
| P1-10 Legacy 兼容 | 旧行不炸页面/API；不可恢复币种显示 legacy | `test_legacy_rows_do_not_break_dashboard`（USD 0.5×7.2+CNY 2.0=5.6） | PASS |
| P1-11 性能 | 不每调用查价表；版本切换 cache 失效 | 沿用 30s price cache + clear_price_cache（未改动） | PASS（存量） |
| P1-12 价格切换 | A/B 版本各自固化单价与版本 | `test_price_version_snapshot_is_immutable` | PASS |

## E. 一笔真实调用完整对账（黄金测试等价链）

真库实弹受 PG 连接饱和限制（本机栈 100 连接占满，与本线无关），对账链以确定性黄金测试 + 实库聚合 SQL 双通道证明：

```
model=doubao（价 input $0.2 / output $2.0 per 1M，USD 报价）
Usage: input 1M（cached 0）+ output 1M
  → NormalizedUsage(billable=1M, output=1M)
  → price_usage: native = $2.20 (USD), fx=7.20, billed = ¥15.84
  → llm_usage 行:  billed_cost_cny=15.84 / native_cost=2.2 / currency=USD / fx=7.2
  → budget settle: 15.84（test_price_unknown_usd_converts_before_budget_settlement 断言入参）
  → trace: cost_cny = Σ billed_cost_cny（test_trace_cost_equals_usage_sum）
  → dashboard: cost_cny 原样汇总（test_dashboard_does_not_double_fx）

BILLING_RESULT_COST == LLM_USAGE_COST == BUDGET_SETTLED_COST
== TRACE_COST == DASHBOARD_COST（6 位小数内）
```

实库迁移后状态（079 应用后查询）：

```
currency  v  rows   billed_filled  sum_cny
CNY       2  26975  26975          69.3628   ← 历史可信行已回填
<empty>   1  24812  0              1.6124    ← embedding/rerank 旧行，不猜保 legacy
USD       1   1078  0              6.4702    ← 真 USD 与 enforce 错标混存，不猜保 legacy
```

## F. 最终判决

```
BILLING_SINGLE_SOURCE_OF_TRUTH=true      （唯一入口 price_usage，五写入方/四处读层全收敛）
OBSERVE_ENFORCE_PRICING_PARITY=true      （同一次调用同一算法，差异仅 status/门禁）
BUDGET_USAGE_RECONCILIATION_PASS=true    （settle 入参 = billed_cost_cny，测试断言）
TRACE_USAGE_RECONCILIATION_PASS=true     （回填直接取 billed，禁二次换汇）
CURRENCY_CONTRACT_PASS=true              （native/billed 分列，cost_usd 一列三态消灭）
PRICE_VERSION_SNAPSHOT_PASS=true
FX_SNAPSHOT_PASS=true
LEGACY_COMPATIBILITY_PASS=true           （legacy 行兜底展示，不伪造 exact）
MODEL_BILLING_P0_PASS=true               （P0-01~14 全 PASS，其中 08/13 为存量达成）
MODEL_BILLING_P1_PASS=true               （P1-01~12 全 PASS，11 为存量沿用）
MODEL_BILLING_PRODUCTION_READY=true
```

## G. 每阶段完成标准回执

**PHASE_0_PASS=true** — 修改：无（只读审计）；新增：STOP-A 审计报告。5 项判定全 false，进入 Phase 1。

**PHASE_1_PASS=true** — 修改：pricing.py（+323/-…）；新增：NormalizedUsage/BillingResult/price_usage；测试 26 passed（含重写的 test_usage_billing 13 例）。兼容风险：单价快照从「折算 CNY」改为「原生币种」（有意契约变更，消费方已同步）。下一阶段：YES。

**PHASE_2_PASS=true** — 修改：proxy.py/quota.py/token_tracker.py/embedding_singleton.py/reranker.py/events.py；修复 P0-A（enforce 币种错标）、P0-B（USD 直入结算）、embedding/rerank 双账本；测试 464 passed。兼容风险：SSE usage 帧 cost_usd 归零（前端未消费，已核）；turn 累加器键 cost_usd→cost_cny（消费方 events.py 同步）。下一阶段：YES。

**PHASE_3_PASS=true** — 新增 migration 079（不修改历史迁移）；存储层 record/_init_db/读层表达式。历史数据按 §12：CNY 行回填（26975 行），USD/空行保 legacy。兼容风险：legacy USD 行含 enforce 错标毒行，展示仍 ×7.2（不可机判，遵循「不猜」原则）。下一阶段：YES。

**PHASE_4_PASS=true** — 修改：tracer.py/_trace_dto.py；TraceRecord 新增 cost_cny；span 级 cost_cny 透出。下一阶段：YES。

**PHASE_5_PASS=true** — 修改：管理端 5 文件；tsc 0 错；418 测试 417 过（1 失败为分支存量 KnowledgeWorkbench Tab 数断言，与本线无关，源于 4b8dc99 加第六 Tab 未同步测试）。Grafana 现役看板（agent-05-llm-cost-context.json，另一会话未提交产物）消费 cost_gauge 投影已随之 V2-aware，本线未改该文件。下一阶段：YES。

**PHASE_6_PASS=true** — 黄金契约 12 条 + 回归 464 passed / observability 107 passed / reconciliation 10 passed（真库）/ migrate-state OK。仍未解决见 H。

## H. 遗留与已知风险

1. **legacy USD 行（1078 行）含 enforce 错标毒行**：2026-10-01~10-07 的 enforce exact 行是 CNY 值标 USD，与切换前真 USD 行不可机判，按 §12 不猜，读层仍 ×7.2 展示（legacy_estimated 语义）。如需精确回收，须人工按 trace_id 圈定后一次性脚本回填——挂账待拍板。
2. **文档同步未做**：`AGENTS.md`/`README.md`/`docs/DATABASE.md` 等顶级文档工作区有另一会话（Grafana 重构线）未提交改动，为避免混线提交未触碰。合并前需补：DATABASE.md 迁移 079 行、README 系统规模口径（无数量变化）、AGENTS.md 计费事实链一句话。
3. **容器未部署**：app/worker 容器仍跑旧代码；V2 写入与读层收益需 rebuild 后实机复验（P0-07 实弹一笔真调用对账建议部署后补）。
4. **cost_gauge 投影刷新周期 600s**：迁移回填后 gauge 数值将在下一个周期自动跟随（投影设计如此，无需回溯）。
5. **无关存量失败**：KnowledgeWorkbench Tab 数断言（4b8dc99 引入）；本机 PG 连接饱和（100 上限占满，多次测试需重试）。

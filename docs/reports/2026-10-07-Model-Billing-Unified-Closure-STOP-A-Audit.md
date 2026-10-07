# Model Billing Unified Closure — STOP A 现状审计报告（Phase 0）

> 日期：2026-10-07｜分支：`feat/model-billing-closure`｜基线：`4ddc3c3`
> 性质：只读审计，未改任何代码。任务书与本报告冲突时以实际代码为准，差异见 §13。

---

## 1. Usage 来源

| 来源 | 位置 | 说明 |
|---|---|---|
| Provider 真实 usage | `observability/usage_parse.py::extract_usage_payload`（proxy `_record_tokens` 消费） | LangChain `usage_metadata` 统一解析；缓存/推理 token 从 `input_token_details.cache_read` / `output_token_details.reasoning` 透传 |
| 流式缺失兜底 | `infra/llm/usage_estimator.py`（`StreamTextMeter` + `estimate_prompt_tokens`） | 字符统计估算，`binding_source='estimated'`，开关 `LLM_USAGE_ESTIMATION_ENABLED`（默认开） |
| 失败 attempt | `proxy._record_failed_attempt` | 全 0 行，`cost_status=unpriced`，`decision` 标注 |

**结论**：usage 解析已统一（C4 单一实现），无第二份解析。缺 `usage_source` 显式列（现在靠 `binding_source='estimated'` / `finish_reason='estimated_no_usage'` 间接表达）。

## 2. Pricing 入口（现状 = 双入口，即 P0-C 根因）

`backend/infra/llm/pricing.py` 存在**两条独立算法**：

| 入口 | 消费方 | 算法 |
|---|---|---|
| `calculate_current_cost(enforce=True/False)` | proxy enforce 分支、embedding/rerank/token_tracker | `PriceTable.calculate_cost`：对传入的**全部维度**（input/output/cache_read/cache_write/reasoning/tool_call）逐维乘价，缺行**静默跳过**；硬模式缺价抛 `MissingModelPrice`；出口 `_to_base` 折 CNY |
| `calculate_llm_cost_with_status()` | proxy observe 分支、流式估算结算 | `_llm_cost_breakdown`：**只计 input(billable)/cache_read/output 三维**，reasoning/cache_write/tool_call 即使有价行也不计；状态机 exact/estimated/unpriced；出口 `_to_base_or_unpriced` 折 CNY（永不抛错） |

第三函数 `calculate_fallback_cost`：注册表内置价 `compute_cost_usd`（恒 USD，只有 input/output 两维），被 observe 缺价分支与 enforce 缺价分支各自调用。

## 3. observe 路径

```
proxy._record_tokens（budget_state 非 enforce）
  → calculate_llm_cost_with_status(model, quantities)
  → (CNY 总额, status, 'CNY', breakdown{input/cached/output cost + 三单价快照})
  → record_model_usage(cost=CNY) → quota settle(CNY)   ✅ 结算口径正确
  → llm_usage 行：cost_usd=CNY值, total_cost=CNY值, currency='CNY'  ✅ 币种标注正确
```

## 4. enforce 路径

```
proxy._record_tokens（budget_state.mode == enforce 且 quota_store 在场）
  → calculate_current_cost(enforce=True)  → 返回 CNY
  → 代码硬编码 cost_status, currency = "exact", "USD"   ❌ P0-A：CNY 值标成 USD
  → record_model_usage(cost=CNY) → quota settle(CNY)   ✅ 结算口径正确
  → llm_usage 行：cost_usd=CNY值, total_cost=CNY值, currency='USD'  ❌
     → dashboard/trace 回填按 currency!=CNY 再乘 7.2 → 二次换汇（$1 实付 ¥7.2 显示 ¥51.84）
  缺价/价格库不可用时：
  → calculate_fallback_cost → USD 金额
  → record_model_usage(cost=USD值) → quota settle(actual_cny=USD值)  ❌ P0-B：少扣 7.2 倍
  → llm_usage 行：currency='USD'（金额确实是 USD，看板折算碰巧正确）
```

## 5. llm_usage 写入路径

唯一写入出口 = `PostgresLLMUsageStore.record()`。写入方五处，**金额币种语义不一**：

| 写入方 | cost_usd 列实际含义 | currency 列 |
|---|---|---|
| proxy 主链 observe | CNY | 'CNY' ✅ |
| proxy 主链 enforce exact | CNY | 'USD' ❌（P0-A） |
| proxy enforce price_unknown | USD | 'USD' ✅（看板碰巧对） |
| proxy 流式估算 | CNY | 'CNY' ✅ |
| `rag/embedding_singleton.py:286` | **CNY**（calculate_current_cost 返回值） | **''** ❌ 空 → 按 USD ×7.2 二次换汇 |
| `rag/reranker.py:365` | **CNY**（同上） | **''** ❌ 同上 |
| `infra/token_tracker.py:279` | USD（注册表估价，与预算结算用的 PG 价**不同源**） | '' ⚠️ 折算碰巧对，但行金额 ≠ 预算结算金额 |

**P0-D 实锤**：`cost_usd` 列已同时承载 USD、CNY、以及「CNY 值但标 USD」三种语义。

## 6. budget reserve（预占）

`budget.py::RequestBudget.reserve` → `PostgresQuotaStore.reserve`：`reserved_cny`，user/tenant × day/month 四行，PG 事务内 `FOR UPDATE`-语义冲突仲裁（`ON CONFLICT ... WHERE used+reserved+amount < limit`），并发防超卖成立（P0-13 现状达标）。预占前 `get_current_price_table.require(enforce=True)` 仅作缺价探测（缺价放行 + `price_unknown` 计数，2026-09-22 拍板保留）。预占金额 = `LLM_REQUEST_MAX_COST`（**单位已是 CNY**，2026-10-01 改，旧名 `LLM_REQUEST_MAX_COST_USD` 仅兼容别名）。

## 7. budget settle（结算）

`budget.py::record_model_usage` → `quota_store.settle(reservation, Decimal(cost))`。**settle 本身无币种概念，金额含义完全由调用方决定**——observe/流式估算路径传 CNY（对），enforce price_unknown 路径传 USD（错，P0-B）。结算失败已转 `needs_review`（2026-10-01 修复，不吞）。

## 8. Trace 成本

- 新 trace finish：`tracer.py:883-913` 按 llm_usage 明细行折算 `usage.cost_cny`（行 currency=CNY 用原值，否则 ×7.2）——**enforce 行与 embedding/rerank 行被二次换汇** ❌。
- 存量 trace 读时回填：`_trace_dto.py::backfill_usage_from_llm_store`，同一套行级折算逻辑（同一错误）❌。
- `trace.cost_usd` 顶层字段 = 明细 `cost_usd` 混算直和（CNY+USD 混加）❌。

## 9. Token 看板（管理端）

- `dashboard()` 聚合：`cost_cny` = 按行 currency 折算（enforce/embedding 行二次换汇 ❌）；`cost_usd` = 混算直和（兼容保留）。
- `breakdown()`（六维归因）：只出 `SUM(cost_usd)` 混算 ❌。
- `quota.summary()`（预算总览）：`SUM(total_cost)` **无币种感知**，CNY 行与 USD 行直接相加 ❌（2026-10-01 只修了 ledger 翻倍问题，未修混算）。
- 前端：`CostPanel.tsx` `const totalUsd = trace.cost_cny ?? trace.cost_usd`（变量名 USD 装人民币）；`TokenCharts.tsx` 趋势图 `dataKey="cost_usd"`（Tooltip ¥ / 数据混算）；`TokensPanel.tsx` 总览与明细全部 `cost_usd`。

## 10. Grafana

工作区已重构：任务书提到的 `agent-cost.json` **已删除**（未提交），现役 = `agent-05-llm-cost-context.json`（7 看板体系之一，同为未提交文件）。成本面板已全部走 `llm_usage_cost_cny_24h / llm_usage_cost_cny_month`（cost_gauge 账本投影，¥ 口径），无 usd 指标名 ✅。缺口：无 `unpriced / price_unknown / needs_review` 专项面板（`estimated` 兜底已有）。

## 11. 历史字段兼容现状

- migration：013（建表）→ 032（分项成本+status+currency）→ 046（身份链+单价快照）→ 055（归因列）。下一个可用编号 **079**。
- 存量数据币种判定（2026-10-01 切换点）：
  - `currency='CNY'` 行 = 切换后 observe/流式估算行，`total_cost` 金额即 CNY（可安全回填 `billed_cost_cny = total_cost`）；
  - `currency='USD'` 行 = 两种：切换前真 USD 行、切换后 enforce 行（**CNY 值标 USD，不可机判**）→ 按任务书 §12 不猜，`billing_schema_version=1`，`billed_cost_cny=NULL`，读层按旧逻辑展示；
  - `currency=''` 行 = embedding/rerank/token_tracker 行（切换后为 CNY 值；更早为 USD 估价）→ 同样不可机判，不回填。
- 兼容读取链（dashboard cost_cny 表达式、trace 回填）已存在，本次改为 **billing schema 感知**。

## 12. 真实链路图（现状）

```
Provider 返回 usage
        ↓
extract_usage_payload（唯一解析）          流式缺 usage → StreamTextMeter 估算
        ↓                                          ↓
   ┌─ enforce? ─┐                          calculate_llm_cost_with_status
   │ 是         │ 否                                ↓
   ▼            ▼                            (CNY, status, 'CNY', breakdown)
calculate_current_cost   calculate_llm_cost_with_status
(CNY, 全维度)            (CNY, 三维度, breakdown)
   │                        │
   │ 缺价→calculate_fallback_cost(USD)
   ▼                        ▼
[硬编码 "exact","USD"]❌   [currency='CNY']✅
   └────────┬───────────────┘
            ▼
   record_model_usage(cost=?)
      ├─ RequestBudget.cost += ?（请求级预算，口径混杂 ❌）
      └─ quota settle(? as CNY)（price_unknown 时=USD ❌）
            ▼
   llm_usage 行（cost_usd 列语义三态 ❌）
      ├─ tracer / _trace_dto 回填（×7.2 二次换汇 ❌）
      ├─ dashboard cost_cny 表达式（同 ❌）
      └─ cost_gauge 投影 → Grafana（继承同 ❌）
```

## 13. 判定结论

| 判定 | 结果 | 依据 |
|---|---|---|
| `OBSERVE_ENFORCE_SAME_PRICING` | **false** | 双入口算法不同：维度集（全维度 vs 三维度）、缺价行为（fallback+price_unknown vs estimated）、breakdown（无 vs 有） |
| `COST_USD_SEMANTICS_CLEAN` | **false** | §5 表：一列三态（USD / CNY / CNY值标USD），embedding/rerank 行 currency 空 |
| `HARD_EXACT_CURRENCY_CORRECT` | **false** | `proxy.py:1237` `cost_status, currency = "exact", "USD"`，而 `calculate_current_cost` 2026-10-01 起返回 CNY |
| `PRICE_UNKNOWN_SETTLEMENT_CURRENCY_CORRECT` | **false** | `proxy.py:1245-1248` USD 估价直入 `record_model_usage` → `settle(actual_cny=USD值)` |
| `BILLING_SINGLE_SOURCE_OF_TRUTH` | **false** | 双入口 + 五写入方币种不一 + 三处独立折算（trace 回填 / dashboard SQL / quota summary 直和） |

**→ 前四项全部存在，进入 Phase 1。**

## 14. 与任务书的差异声明（以实际代码为准）

1. **Grafana**：`agent-cost.json` 在工作区已被「Grafana 观测体系重构」删除，现役 `agent-05-llm-cost-context.json`（未提交，另一会话产物）。本任务 Phase 6 以新文件为准，**不提交该批次文件**（提交严格路径限定）。
2. **`LLM_REQUEST_MAX_COST`**：已是 CNY 语义 + 旧名兼容别名（P0-08 大半已达成，只差文档/变量语义清扫）。
3. **quota 账本**：`used_cny/reserved_cny/settled_cny` 已全 CNY，P0-13 并发预占已成立——Phase 2 只需修结算入参，不动账本结构。
4. **Trace / 管理端**：2026-10-01 已做过一轮「人民币为主」改造（`cost_cny` 字段已在），但**折算数据源被 enforce 行污染**——本轮是修数据源而非新建展示。
5. **测试基线**：`tests/infra/test_usage_billing.py` 4 用例在当前 main 即红（断言旧「USD 出账」契约），`tests/infra/test_pricing_cost_status.py` 断言新契约——两份测试口径互相矛盾，以本轮 Billing Contract 为唯一口径重写前者。

## 15. 冻结的 Billing Contract（Phase 1 实施基准）

```
NormalizedUsage（冻结字段）
  input_tokens / cached_input_tokens / cache_write_tokens / output_tokens /
  reasoning_tokens / tool_calls
  billable_input_tokens = max(input_tokens - cached_input_tokens, 0)   # 缓存不双算

BillingResult（冻结字段）
  身份:   model_name / component
  用量:   input_tokens / billable_input_tokens / cached_input_tokens /
          output_tokens / reasoning_tokens / cache_write_tokens / tool_calls
  原始:   native_cost / native_currency          # 供应商报价币种
  记账:   billed_cost_cny / base_currency='CNY'  # 预算/看板唯一金额
  汇率:   fx_rate                                # 该次调用实际使用的快照；未换汇=None
  分项:   input_cost_cny / cached_input_cost_cny / output_cost_cny /
          reasoning_cost_cny / cache_write_cost_cny / tool_call_cost_cny
  单价:   input/output/cache_input/reasoning/cache_write/tool_call_unit_price
          （原生币种 per_1m_tokens 调用时快照）
  治理:   price_version / pricing_source(approved_price_table|registry_fallback|unpriced)
  可信:   usage_source(provider|estimated|unavailable) / cost_status(exact|estimated|unpriced|price_unknown)

唯一入口: pricing.price_usage(model_name, component, usage, *, enforce, usage_source) -> BillingResult
  - 同 Usage + 同价格版本 + 同 FX → observe 与 enforce 金额/状态完全一致（enforce 只多「预占已发生」这一事实）
  - 缓存价行缺失且 cached>0 → 缓存按 input 价，cost_status=estimated
  - 任意维度 tokens>0 且无价行 → estimated（不静默跳过）
  - PG 缺价/不可用 → 注册表估价（pricing_source=registry_fallback）；enforce 下 cost_status=price_unknown，
    observe 下 = estimated；估价 USD 先 ×fx 得 billed_cost_cny，预算只收 CNY
  - 估价=0 → unpriced（billed=0，token 照记，不冒充免费）
  - usage_source='estimated' 时最终 status 不为 exact（用量近似污染精确性）
```

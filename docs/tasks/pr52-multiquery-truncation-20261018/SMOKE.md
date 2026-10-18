# 唯一真实 Smoke 证据

- Run：[39108656690](https://github.com/Jayus-07/agent/actions/runs/39108656690)，`multiquery=true`。
- 评测实现 HEAD：`a87df8316f820fa5cd008ec24fcab2d568022a88`。
- 对照：原始基线39064795128、上一轮39075781263；未更改Golden或阈值。

## 验收结论：BLOCKED，MERGE_READY=false

- 16份fixture成功、索引完整性PASS；严格质量门禁FAIL、费用对账门禁FAIL。用例级8/8 pass不是质量验收通过。
- RC-024与RC-031均在6000ms改写等待后降级原问题；Trace有效上限1024，未取得完整响应，finish_reason/completion tokens不可得，不报告为0或“1024输出完整”。本轮没有真实3查询评分，逐查询max与新Prompt质量仍未验收。
- RC-024 root=7754ms，8000ms预算剩246ms，rerank=1642ms；原问题单查询评分仍将inventory_13/14排在purchase_3之前。RC-031 root=8064ms，预算已耗尽；rerank HTTP read timeout约1.90784s，降级无分数透传，Top-1指标缺失。
- Top-1本轮为4/6=0.6667；基线为5/7=0.7143。RC-031因不可靠重排没有有效Top-1评分，分母变化，不能把差值全部归因于评分算法。RC-005、RC-024仍为0。
- 账本记录113 calls/104861 tokens，108条settled合计CNY0.117488，差额0；另有5条needs_review（3 chat unpriced、2 rerank unpriced）。已计账envelope=WITHIN_LIMITS，但最终实际费用/用量不可核实；needs_review预占CNY1.25不是已支出，也不能以此替代供应商账单。
- 无身份归属异常（unattributed_calls=0）；artifact JSON中`_runtime_mq_queries`出现次数=0。
- PR保持Open/Draft；未发布、回滚、解除Draft或合并。Active仍v1/`d3ea6cc684a20879`，旧v2 Draft/`898e7970527161cf`；本轮候选文件Hash=`a9e9f66220217388`。timeout span未保留Prompt Hash/版本字段，不能用文件Hash冒充完整的实际调用归因。

## 根因边界与下一条最小建议

直接证据是改写子任务6000ms未返回，几乎耗尽8000ms检索窗口；RC-031余下约2秒仍发生重排ReadTimeout。现有Trace尚不能细分线程池排队、治理前置、Provider推理与网络耗时，也未导出5条异常的逐调用账本，因此第三条chat异常不能精确归属到case。已批准价格存在（同模型其他调用均正常定价），没有把未知用量补成0或伪造结算。

下一条最小建议：先补改写子任务的排队/角色解析与预占/Provider调用分段耗时，并在渲染时即保存Prompt Hash/版本；对取消后迟到用量增加安全逐调用归因与回收证据。以Fake Provider重放6s改写+8s总窗口，确认最耗时段后再针对同模型的改写耗时提出最小调整。**不继续盲目增加timeout或token，不在本轮实施或再付费验证。**

## Precision与Noise的逐例解释

指标以去重后的文档来源计算，以下Top-5列表是chunk排序，两者不是同一分母。

- RC-001：P/N=0.3333/0.6667，三轮不变，FAQ来源与分数稳定。
- RC-005：P/N三轮不变0.3333/0.6667；FAQ仍1.0第一，退货政策0.9741第三；未触发MQ，原Top-1失败保留。
- RC-012：P/N三轮不变0.3333/0.6667；本轮来源集合恢复returns/售后FAQ/同形异义词，前3个chunk分数与基线相同。
- RC-021：来源由基线qc+returns两种，变为上一轮qc+supplier+returns+inventory四种，本轮qc+returns+inventory三种；仅qc命中required_docs（另一必需来源为采购合同），因此P=1/2→1/4→1/3。三轮无MQ成功，变化来自候选来源集合；尚不足以区分重索引/候选选择的全部因果，不归咎于新改写语义。
- RC-024：三轮P/N保持0.75/0.25；本轮MQ超时，库存0.6747/0.6592仍压过采购0.5266，Top-1未解决。
- RC-031：基线仅supplier，上一轮加入qc与一句话FAQ，本轮回退后剩supplier+qc，故P/N由1/0→0.3333/0.6667→0.5/0.5。**supplier_7仍属已标注supplier文档，不能单独解释文档级Precision下降**；上一轮qc_7=0.3114、one_sentence_faq_3=0.3011直接引入另外两种来源。supplier_7=0.3457的获胜查询在旧Trace中缺失；本轮未执行MQ评分，不能补造逐查询分数。本轮P/N改善伴随无有效rerank分数，不能作为新Prompt质量提升证据。
- RC-056：基线supplier/returns/purchase三种来源，本轮及上一轮均另含qc，P由1/3降至1/4，Noise升至3/4；本轮qc_7=0.4597，supplier_7=0.4071。未触发MQ，来源差异保留为独立候选稳定性问题。
- RC-061：拒答场景，Precision=0，Noise无适用分母；拒答指标通过，未伪造检索证据。

## 汇总指标

| 指标 | 原始基线 | 上一轮 | 本轮 |
| --- | ---: | ---: | ---: |
| top1_accuracy | 0.7143 | 0.7143 | 0.6667 |
| recall@5 | 0.9286 | 0.9286 | 0.9286 |
| mrr | 0.8333 | 0.8333 | 0.8333 |
| multi_hop_success | 0.8333 | 0.8333 | 0.8333 |
| precision@5 | 0.4479 | 0.3229 | 0.3541 |
| context_noise@10 | 0.4881 | 0.631 | 0.5953 |

## 账本汇总原始导出（含未核实项）

```json
{
  "note": "隔离测试库账本，未同步生产计费",
  "eval_run_id": "rag-smoke-39108656690-1",
  "budget_request_id": "eval:rag-smoke-39108656690-1",
  "budget_mode": "enforce",
  "cost_verification": "UNVERIFIED",
  "budget_ledger_verification": "UNVERIFIED",
  "verification_error": null,
  "unverified_calls": 5,
  "usage_calls": 113,
  "usage_tokens": 104861,
  "unattributed_calls": 0,
  "budget_envelope_verification": "WITHIN_LIMITS",
  "budget_reservations": {
    "needs_review": 5,
    "settled": 108
  },
  "reservation_amounts_cny": {
    "needs_review": {
      "reserved_cny": 1.25,
      "settled_cny": 0.0
    },
    "settled": {
      "reserved_cny": 23.95,
      "settled_cny": 0.117488
    }
  },
  "usage_cost_cny": 0.117488,
  "settled_cost_cny": 0.117488,
  "ledger_difference_cny": 0.0,
  "usage": [
    {
      "component": "embedding",
      "model": "qwen3.7-text-embedding",
      "provider": "rag-eval-embedding",
      "cost_status": "estimated",
      "pricing_source": "approved_price_table",
      "calls": 66,
      "total_tokens": 18137,
      "billed_cost_cny": 0.009087
    },
    {
      "component": "llm",
      "model": "doubao-seed-2.0-mini",
      "provider": "rag-eval-chat",
      "cost_status": "exact",
      "pricing_source": "approved_price_table",
      "calls": 35,
      "total_tokens": 75630,
      "billed_cost_cny": 0.102852
    },
    {
      "component": "llm",
      "model": "doubao-seed-2.0-mini",
      "provider": "rag-eval-chat",
      "cost_status": "unpriced",
      "pricing_source": "unpriced",
      "calls": 3,
      "total_tokens": 0,
      "billed_cost_cny": null
    },
    {
      "component": "rerank",
      "model": "qwen3.7-text-rerank",
      "provider": "rag-eval-rerank",
      "cost_status": "exact",
      "pricing_source": "approved_price_table",
      "calls": 7,
      "total_tokens": 11094,
      "billed_cost_cny": 0.005549
    },
    {
      "component": "rerank",
      "model": "qwen3.7-text-rerank",
      "provider": "rag-eval-rerank",
      "cost_status": "unpriced",
      "pricing_source": "unknown",
      "calls": 2,
      "total_tokens": 0,
      "billed_cost_cny": null
    }
  ]
}
```

## 逐例 Precision / Noise / 召回

| 用例 | 原始 P/N | 上轮 P/N | 本轮 P/N | 本轮 Recall@5 / Multi-hop |
| --- | --- | --- | --- | --- |
| RC-001 | 0.3333 / 0.6667 | 0.3333 / 0.6667 | 0.3333 / 0.6667 | 1.0 / N/A |
| RC-005 | 0.3333 / 0.6667 | 0.3333 / 0.6667 | 0.3333 / 0.6667 | 1.0 / N/A |
| RC-012 | 0.3333 / 0.6667 | 0.3333 / 0.6667 | 0.3333 / 0.6667 | 1.0 / N/A |
| RC-021 | 0.5 / 0.5 | 0.25 / 0.75 | 0.3333 / 0.6667 | 0.5 / 0.5 |
| RC-024 | 0.75 / 0.25 | 0.75 / 0.25 | 0.75 / 0.25 | 1.0 / 1.0 |
| RC-031 | 1.0 / 0.0 | 0.3333 / 0.6667 | 0.5 / 0.5 | 1.0 / 1.0 |
| RC-056 | 0.3333 / 0.6667 | 0.25 / 0.75 | 0.25 / 0.75 | 1.0 / N/A |
| RC-061 | 0.0 / None | 0.0 / None | 0.0 / None | None / N/A |

### RC-001

问题：现货商品的发货时间是多久？

- baseline Top-5：baseline_faq_1=1.0；baseline_faq_19=0.9989；baseline_faq_10=0.7306；baseline_numeric_trap_6=0.5216；baseline_numeric_trap_7=0.5027
- previous Top-5：baseline_faq_1=1.0；baseline_faq_19=0.9989；baseline_faq_10=0.7306；baseline_numeric_trap_6=0.5216；baseline_numeric_trap_7=0.5027
- current Top-5：baseline_faq_1=1.0；baseline_faq_19=0.9989；baseline_faq_10=0.7306；baseline_numeric_trap_6=0.5216；baseline_numeric_trap_7=0.5027

rag_eval_root：status=success，6938ms

```json
{
  "retrieval_budget": {
    "total_ms": 8000.0,
    "remaining_ms": 1068.1,
    "expired": false
  }
}
```

query_rewrite：status=skipped，1ms

```json
{}
```

rerank：status=success，1978ms

```json
{
  "rerank_mode": "single_query"
}
```

### RC-005

问题：商品质量问题的退货运费由谁承担？

- baseline Top-5：baseline_faq_4=1.0；baseline_after_sales_faq_2=0.989；baseline_returns_6=0.9741；baseline_faq_19=0.9661；baseline_returns_7=0.9575
- previous Top-5：baseline_faq_4=1.0；baseline_after_sales_faq_2=0.989；baseline_returns_6=0.9741；baseline_returns_7=0.9575；baseline_returns_5=0.8438
- current Top-5：baseline_faq_4=1.0；baseline_after_sales_faq_2=0.989；baseline_returns_6=0.9741；baseline_returns_7=0.9575；baseline_returns_5=0.8438

rag_eval_root：status=success，2461ms

```json
{
  "retrieval_budget": {
    "total_ms": 8000.0,
    "remaining_ms": 5538.8,
    "expired": false
  }
}
```

query_rewrite：status=skipped，1ms

```json
{}
```

rerank：status=success，1099ms

```json
{
  "rerank_mode": "single_query"
}
```

### RC-012

问题：七天无理由退货需要满足什么条件？

- baseline Top-5：baseline_returns_4=0.9734；baseline_returns_5=0.9548；baseline_after_sales_faq_1=0.8449；baseline_homograph_1=0.5421；baseline_returns_6=0.5151
- previous Top-5：baseline_returns_4=0.9734；baseline_returns_5=0.9548；baseline_after_sales_faq_1=0.8449；baseline_returns_8=0.665；baseline_faq_15=0.5365
- current Top-5：baseline_returns_4=0.9734；baseline_returns_5=0.9548；baseline_after_sales_faq_1=0.8449；baseline_homograph_1=0.5421；baseline_returns_7=0.5271

rag_eval_root：status=success，4794ms

```json
{
  "retrieval_budget": {
    "total_ms": 8000.0,
    "remaining_ms": 3206.0,
    "expired": false
  }
}
```

query_rewrite：status=skipped，1ms

```json
{}
```

rerank：status=success，2480ms

```json
{
  "rerank_mode": "single_query"
}
```

### RC-021

问题：质检不合格率超过多少时甲方有权要求换货或退货？

- baseline Top-5：baseline_qc_sop_6=0.9773；baseline_qc_sop_4=0.6014；baseline_qc_sop_7=0.4067；baseline_returns_6=0.3845；baseline_returns_7=0.3698
- previous Top-5：baseline_qc_sop_6=0.9773；baseline_supplier_11=0.6118；baseline_supplier_14=0.4824；baseline_qc_sop_7=0.4067；baseline_returns_6=0.3845
- current Top-5：baseline_qc_sop_6=0.9773；baseline_qc_sop_4=0.6014；baseline_qc_sop_7=0.4067；baseline_returns_6=0.3845；baseline_returns_7=0.3698

rag_eval_root：status=success，6401ms

```json
{
  "retrieval_budget": {
    "total_ms": 8000.0,
    "remaining_ms": 1575.9,
    "expired": false
  }
}
```

query_rewrite：status=skipped，1ms

```json
{}
```

rerank：status=success，2431ms

```json
{
  "rerank_mode": "single_query"
}
```

### RC-024

问题：采购新品上架需要哪些部门协作？

- baseline Top-5：baseline_inventory_9=0.6747；baseline_inventory_10=0.6592；baseline_purchase_3=0.5266；baseline_supplier_7=0.4249；baseline_qc_sop_2=0.403
- previous Top-5：baseline_inventory_13=0.6747；baseline_inventory_14=0.6592；baseline_purchase_3=0.5266；baseline_supplier_7=0.4249；baseline_qc_sop_2=0.403
- current Top-5：baseline_inventory_13=0.6747；baseline_inventory_14=0.6592；baseline_purchase_3=0.5266；baseline_supplier_7=0.4249；baseline_qc_sop_2=0.403

rag_eval_root：status=success，7754ms

```json
{
  "retrieval_budget": {
    "total_ms": 8000.0,
    "remaining_ms": 246.0,
    "expired": false
  }
}
```

query_rewrite：status=timeout，6001ms

```json
{
  "max_tokens": 1024,
  "rewrite_status": "degraded",
  "query_count": 1,
  "degrade": "rewrite_timeout",
  "fallback": "original_query"
}
```

rerank：status=success，1642ms

```json
{
  "rerank_mode": "single_query"
}
```

前五证据引用（节选）：

1. `baseline_inventory_13` / ops_库存管理制度.pdf / 0.6747：第三章 出入库流程 入库流程：采购到货 → 验收 → 质检 → 上架。 出库流程：订单审核 → 拣货 → 复核 → 打包 → 交接物流。
2. `baseline_inventory_14` / ops_库存管理制度.pdf / 0.6592：入库流程：采购到货 → 验收 → 质检 → 上架。 出库流程：订单审核 → 拣货 → 复核 → 打包 → 交接物流。
3. `baseline_purchase_3` / purchase_采购流程.docx / 0.5266：二、采购申请 2.1 需求部门在 OA 提交采购申请，注明商品名称、规格、数量、预算及用途。 2.2 单笔金额 5000 元以下由部门负责人审批;5000-50000 元需总监审批;50000 元以上需总经理审批。
4. `baseline_supplier_7` / supplier_供应商管理规范.md / 0.4249：三、供应商准入 1. **资质初审**：营业执照、生产 / 经营许可证、商标注册或授权链、质检报告齐备。 2. **样品验证**：提供样品并送质检，关键指标须达标。 3. **试单考核**：小批量试单，评估交期达成率、质量合格率、售后响应。 4. 三项通过后方可纳入《合格供应商名录》。
5. `baseline_qc_sop_2` / qc_质检标准SOP.docx / 0.403：一、目的 规范跨境商品入库质检流程，确保上架商品符合质量与合规要求，降低售后退货与客诉风险。

### RC-031

问题：供应商连续两季度评分低于60分，同时出现了安全事故，应如何处理？

- baseline Top-5：baseline_supplier_17=0.9768；baseline_supplier_16=0.9719；baseline_supplier_10=0.7507；baseline_supplier_9=0.7471；baseline_supplier_4=0.3093
- previous Top-5：baseline_supplier_17=0.9768；baseline_supplier_16=0.9719；baseline_supplier_10=0.7507；baseline_supplier_9=0.7471；baseline_supplier_7=0.3457
- current Top-5：baseline_supplier_16=None；baseline_supplier_17=None；baseline_supplier_10=None；baseline_supplier_9=None；baseline_supplier_4=None

rag_eval_root：status=success，8064ms

```json
{
  "retrieval_budget": {
    "total_ms": 8000.0,
    "remaining_ms": 0,
    "expired": true
  }
}
```

query_rewrite：status=timeout，6001ms

```json
{
  "max_tokens": 1024,
  "rewrite_status": "degraded",
  "query_count": 1,
  "degrade": "rewrite_timeout",
  "fallback": "original_query"
}
```

rerank：status=error，1971ms

```json
{
  "degrade": "rerank_error",
  "fallback": "passthrough"
}
```

前五证据引用（节选）：

1. `baseline_supplier_16` / supplier_供应商管理规范.md / None：六、退出机制 连续两季度为 D 级，或出现售假、重大安全事故、制裁合规风险，立即启动退出：冻结新订单、清算在途与库存、保留追溯与索赔权利。
2. `baseline_supplier_17` / supplier_供应商管理规范.md / None：连续两季度为 D 级，或出现售假、重大安全事故、制裁合规风险，立即启动退出：冻结新订单、清算在途与库存、保留追溯与索赔权利。
3. `baseline_supplier_10` / supplier_供应商管理规范.md / None：| 等级 | 标准 | 权益 | |------|------|------| | A 级（战略） | 年度评分 ≥ 90，且合作满 1 年 | 优先份额、账期优惠 | | B 级（合格） | 年度评分 75–89 | 正常合作 | | C 级（观察） | 年度评分 60–74 | 限期整改、减少份额 | | D 级（
4. `baseline_supplier_9` / supplier_供应商管理规范.md / None：四、供应商分级 | 等级 | 标准 | 权益 | |------|------|------| | A 级（战略） | 年度评分 ≥ 90，且合作满 1 年 | 优先份额、账期优惠 | | B 级（合格） | 年度评分 75–89 | 正常合作 | | C 级（观察） | 年度评分 60–74 | 限期整改、减少份额
5. `baseline_supplier_4` / supplier_供应商管理规范.md / None：规范供应商从准入、合作到退出的全生命周期管理，建立稳定、合规、低风险的供应链体系。

### RC-056

问题：供应商退出的完整流程是什么？

- baseline Top-5：baseline_supplier_17=0.9415；baseline_supplier_16=0.9087；baseline_supplier_7=0.4071；baseline_returns_13=0.363；baseline_purchase_0=0.3106
- previous Top-5：baseline_supplier_17=0.9415；baseline_supplier_16=0.9087；baseline_supplier_4=0.588；baseline_qc_sop_7=0.4597；baseline_supplier_7=0.4071
- current Top-5：baseline_supplier_17=0.9415；baseline_supplier_16=0.9087；baseline_qc_sop_7=0.4597；baseline_supplier_7=0.4071；baseline_qc_sop_8=0.374

rag_eval_root：status=success，7191ms

```json
{
  "retrieval_budget": {
    "total_ms": 8000.0,
    "remaining_ms": 808.7,
    "expired": false
  }
}
```

query_rewrite：status=skipped，1ms

```json
{}
```

rerank：status=success，6067ms

```json
{
  "rerank_mode": "single_query"
}
```

### RC-061

问题：头疼应该吃什么药？

- baseline Top-5：空
- previous Top-5：空
- current Top-5：baseline_structure_messy_2=0.3026

rag_eval_root：status=success，2177ms

```json
{
  "retrieval_budget": {
    "total_ms": 8000.0,
    "remaining_ms": 5822.9,
    "expired": false
  }
}
```

query_rewrite：status=skipped，1ms

```json
{}
```

rerank：status=success，2131ms

```json
{
  "rerank_mode": "single_query"
}
```

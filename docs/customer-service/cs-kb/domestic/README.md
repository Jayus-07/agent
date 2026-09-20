# 国内电商口径客服知识库（cs-kb / domestic）

【模拟数据 · 内容虚构，仅供演示与评测，不得对客输出】

> 版本：v1.0　生效日期：2026-09-20　维护：演示店铺客服部
> 用途：为 6 个对客知识库（`audience=customer`）提供可直接入库的国内电商口径语料，
> 覆盖「售前 → 下单支付 → 物流发货 → 售后 → 发票 → 活动价格 → 投诉 → 话术」全链路。

## 一、为什么必须按 kb_id 拆分

客服知识问答链路（`backend/customer_service/knowledge/service.py`）在检索时**按 kb_id 过滤**，
kb_ids 来自 `backend/customer_service/router/intents.py` 的 20 个意图画像。
**文档放错库 = 永远检索不到**，因此本目录的文件严格按目标 kb_id 分目录存放：

```
domestic/
├── cs_faq/          → kb_id = cs_faq
├── cs_product/      → kb_id = cs_product
├── cs_policy/       → kb_id = cs_policy
├── cs_aftersales/   → kb_id = cs_aftersales
├── cs_complaint/    → kb_id = cs_complaint
└── cs_scripts/      → kb_id = cs_scripts
```

## 二、文件清单与落点映射

| 文件 | 目标 kb_id | 覆盖内容 |
|---|---|---|
| `cs_faq/faq-presale-ordering.md` | `cs_faq` | 售前 12 问（有货/发货/快递/正品/尺码/材质/色差/适用人群/优惠/无理由/发票）+ 下单支付 7 问 + 动作类分工 |
| `cs_faq/faq-logistics-account.md` | `cs_faq` | 发货时效、物流查询与异常、改址、驿站/快递柜、丢件破损、签收未收到 + 账号地址问题 |
| `cs_product/product-guide.md` | `cs_product` | 尺码选择规则、材质工艺与掉色起球判定、色差、适用人群、正品授权、保修范围、演示 SKU |
| `cs_policy/policy-return-exchange.md` | `cs_policy` | 七天无理由、不支持无理由品类、运费承担矩阵、运费险、换货规则、超期与例外、红线 |
| `cs_policy/policy-invoice.md` | `cs_policy` | 开票基础规则、电子普票、增值税专票、改抬头/补开/退款红冲、红线 |
| `cs_policy/policy-promotion-delivery.md` | `cs_policy` | 保价与降价退差、大促规则、券叠加与满减、限购/秒杀/补货、赠品、配送时效汇总 |
| `cs_aftersales/aftersales-process.md` | `cs_aftersales` | 退款时效与到账时间、退货流程、换货、维修保修、少发错发漏发、破损、过敏不适、超 7 天 |
| `cs_complaint/complaint-handling.md` | `cs_complaint` | 投诉分级 L1-L4、典型场景应答、升级路径、评价管理、红线清单 |
| `cs_scripts/scripts-templates.md` | `cs_scripts` | 12 条通用话术、7 类主动触达、场景组合话术、话术红线 |

## 三、意图覆盖对照

| 意图（intents.py） | 检索 kb_ids | 命中本目录文件 |
|---|---|---|
| `k_faq` / `k_policy` | cs_faq, cs_policy, cs_product | faq-presale-ordering、policy-* |
| `k_product` | cs_product, cs_faq | product-guide、faq-presale-ordering |
| `k_promotion` | cs_policy, cs_faq | policy-promotion-delivery |
| `k_warranty` | cs_policy, cs_product | product-guide（保修范围） |
| `t_order_status` / `t_logistics` | cs_faq | faq-logistics-account |
| `t_delivery_estimate` | cs_policy, cs_faq | policy-promotion-delivery、faq-logistics-account |
| `as_refund` / `as_return` / `as_exchange` | cs_policy, cs_aftersales | policy-return-exchange、aftersales-process |
| `as_repair` | cs_aftersales, cs_product | aftersales-process、product-guide |
| `as_quality_issue` | cs_aftersales, cs_policy | aftersales-process、policy-return-exchange |
| `a_password` / `a_address` / `a_login_issue` | cs_faq | faq-logistics-account |
| `c_complaint` | cs_complaint, cs_scripts | complaint-handling、scripts-templates |
| `c_feedback` | cs_scripts | scripts-templates |
| `h_handoff` / `h_supervisor` | （空，不走知识检索） | — |

## 四、知识可答 vs 动作类问题

本目录文档分为两类内容，**不可混用**：

| 类型 | 说明 | 示例 |
|---|---|---|
| ✅ 知识可答 | 政策、口径、流程、话术——由 RAG 检索 + 引用生成回答 | 「退货运费谁承担？」「能开专票吗？」「多久发货？」 |
| ⚠️ 动作类 | 需要订单/物流/售后系统的**实时数据或写操作** | 「我的包裹到哪了？」「帮我催发货」「帮我退款」 |

动作类问题在每份文档的「动作类问题」小节中给出了正确处理方式。
**原则：涉及订单状态、金额、退款结果的表述必须来自系统或知识证据，禁止推测填空**（对应 EvidenceGate 拒答要求）。

## 五、入库方式

本轮**只产出文档，尚未入库**。入库时：

1. 按上表映射，将文件上传到对应 `kb_id`，`department=customer`；
2. 上传后验证：对每个 kb 各抽 3-5 个问题跑 CS 知识问答，确认能召回并带引用；
3. 故意保留的拒答面（供证据门禁演示）：未覆盖地区政策、超政策承诺（如「无理由退货超过 30 天」）、无证据的订单状态。

## 六、与其他语料的关系

| 语料 | 关系 |
|---|---|
| `docs/customer-service/demo-kb/`（11 份，跨境） | **平行独立**，不可与本目录内容混入同一 kb 导致口径冲突；若需同库共存，需先统一数字口径 |
| `data/docs/rag_eval_kb/`（评测库） | 完全隔离，`audience=test` 不进对客授权集合 |
| `data/docs/policy_general/`（内部制度） | `audience=internal`，对客检索不可见 |

> ⚠️ 本目录口径为**国内电商**（48 小时发货、7 天无理由、99 元包邮、12315 等）；
> 跨境口径见 `../crossborder/README.md`，两者数字不同，禁止交叉引用造成口径冲突。

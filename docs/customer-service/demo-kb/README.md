# 演示知识库语料（Demo KB Corpus）

> 配套 `docs/customer-service/演示沙盒方案-2026-09-17.md` SB-2。
> 全部为**模拟政策/模拟商品**内容，仅用于客服演示沙盒，不代表任何真实商家政策。

## 用途

喂给 CS 知识库（RAG），支撑两个演示场景：

- **场景 C 跨境政策问答**：「美国订单退货需要满足什么条件？运费谁承担？」→ 命中 `policy-return-*.md` / `policy-shipping-fee.md`，回复带引用来源；「南极洲订单能退吗」之类应触发证据门禁拒答。
- **商品咨询导购**：「蓝牙耳机防水吗？适合跑步用吗？」→ 命中 `product-catalog.md`。

## 上传方式

走现有 CS KB 上传链路（`backend/customer_service/knowledge/service.py` → `CSKnowledgeService`），目标 KB 映射：

| 文件 | 目标知识库（config CS_KNOWLEDGE_BASES） |
|---|---|
| policy-return-*.md、policy-shipping-fee.md、policy-refund-timeline.md | `cs_policy` |
| policy-logistics-exception.md | `cs_policy` / `cs_aftersales` |
| product-catalog.md | `cs_product` |
| faq-after-sales-process.md | `cs_faq` / `cs_aftersales` |

## 文件清单

| 文件 | 内容 |
|---|---|
| policy-return-us.md | 美国订单退货政策 |
| policy-return-uk.md | 英国订单退货政策 |
| policy-return-de.md | 德国订单退货政策 |
| policy-return-jp.md | 日本订单退货政策 |
| policy-return-ca.md | 加拿大订单退货政策 |
| policy-shipping-fee.md | 退货运费承担规则 |
| policy-refund-timeline.md | 退款时限与到账时间 |
| policy-logistics-exception.md | 物流异常与催件条件 |
| faq-after-sales-process.md | 售后流程 FAQ |
| product-catalog.md | 演示商品目录（规格/材质/尺码/适用场景/售后保障） |

## 未覆盖地区（拒答演示）

知识库**故意不含**以下内容，用于演示证据门禁/拒答机制：

- 美国/英国/德国/日本/加拿大之外的国家政策（如澳大利亚、巴西）。
- 「无理由退货超过 30 天」等超出政策范围的承诺。

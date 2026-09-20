# 跨境口径客服知识库（cs-kb / crossborder）

【模拟数据 · 内容虚构，仅供演示与评测，不得对客输出】

> 版本：v1.0　生效日期：2026-09-20　维护：演示跨境店铺客服部
> 用途：补齐既有 `docs/customer-service/demo-kb/`（11 份）**未覆盖**的六块内容，
> 使跨境客服知识库覆盖「售前 → 下单支付 → 物流 → 售后 → 发票税务 → 活动价格 → 投诉 → 话术」全链路。

## 一、两个目录的分工（重要）

跨境知识库由**两处**语料共同构成，入库时需一并上传到对应的 `cs_*` 库：

```
docs/customer-service/demo-kb/          ← 既有 11 份（政策/售后/商品主体）
docs/customer-service/cs-kb/crossborder/ ← 本目录 7 份（本次补齐，含 FAQ/发票/活动/投诉/话术）
```

⚠️ 两者凡涉及同一数字口径（退货窗口、退款时限、运费承担）必须一致；
本目录的数值均已按 `demo-kb` 既有政策核对，**不得单方面修改任一侧**。

## 二、既有 demo-kb 已覆盖（本次不重复）

| demo-kb 文件 | 目标 kb_id | 内容 |
|---|---|---|
| `policy-return-us.md` / `-uk` / `-de` / `-jp` / `-ca` | `cs_policy` | 5 国退货政策（美国 30 天、质量问题 90 天、大促 45 天） |
| `policy-shipping-fee.md` | `cs_policy` | 退货运费承担矩阵 |
| `policy-refund-timeline.md` | `cs_policy` | 退款时限（审核 1-2 + 验收 2 工作日）与到账时间 |
| `policy-logistics-exception.md` | `cs_policy` / `cs_aftersales` | 物流异常判定（停滞 3 天 / 干线 5 天 / 丢失 15 天）与催件条件 |
| `faq-after-sales-process.md` | `cs_faq` / `cs_aftersales` | 售后流程 FAQ |
| `product-catalog.md` | `cs_product` | 5 个演示 SKU 的参数/材质/质保 |

## 三、本目录新增文件与落点映射

| 文件 | 目标 kb_id | 覆盖内容 |
|---|---|---|
| `cs_faq/faq-presale-ordering.md` | `cs_faq` | 售前（可配送范围/时效/尺码换算/正品/适用人群/优惠）+ 下单支付（国际支付方式/关税 DDP-DDU/改单/取消） |
| `cs_faq/faq-logistics-account.md` | `cs_faq` | 发货时效、轨迹停滞/清关/派送失败/丢件破损/签收未收到、改址、账号地址 |
| `cs_product/product-guide.md` | `cs_product` | 尺码换算表、材质工艺与起球掉色判定、色差、适用人群、正品资质、跨境保修与电压/插头/认证 |
| `cs_policy/policy-invoice-tax.md` | `cs_policy` | 商业发票/收据/电子发票/税务凭证、专票资料、关税凭证索取、补开重开与红冲 |
| `cs_policy/policy-promotion-price.md` | `cs_policy` | 保价与降价退差、黑五/圣诞/双11 规则、券叠加、限购闪购补货、赠品、跨境区域定价与汇率说明 |
| `cs_complaint/complaint-handling.md` | `cs_complaint` | 投诉分级 L1-L4、场景应答、升级路径、评价管理、**Chargeback（拒付）**专项、红线 |
| `cs_scripts/scripts-templates.md` | `cs_scripts` | 14 条通用话术、8 类主动触达（含清关滞留提醒）、场景组合话术、话术红线 |

## 四、口径锚点（两侧共用，改动需同步）

### 各国退货窗口（**按收货地判定，禁止套用单一国家数值**）

| 国家/地区 | 无理由窗口 | 质量问题窗口 | 大促延长 | 退款处理 | 出处 |
|---|---|---|---|---|---|
| 美国 | 30 天 | 90 天 | 45 天 | 验收后 3-5 工作日 | `policy-return-us.md` |
| 英国 | 14 天 | 见国别政策 | — | 验收后 14 天内（含原始标准运费） | `policy-return-uk.md` |
| 德国 | 14 天 | 见国别政策 | — | 收到退货/凭证后 14 天内 | `policy-return-de.md` |
| 日本 | 7 天 | 30 天 | — | 验收后 5 工作日 | `policy-return-jp.md` |
| 加拿大 | 21 天 | 60 天 | — | 验收后 3-7 工作日 | `policy-return-ca.md` |

> ⚠️ **禁止在话术或 FAQ 中笼统回复「支持 30 天退货」** —— 只有美国是 30 天，日本仅 7 天。
> 跨地区回答必须先确认收货地，或统一表述为「按收货地政策」。

### 其余共用口径

| 项目 | 数值 | 出处 |
|---|---|---|
| 退款处理（统一口径） | 审核 1-2 工作日 + 验收入库 2 工作日 | `policy-refund-timeline.md` |
| 退款到账 | 信用卡 5-7 工作日 / 电子钱包 1-3 工作日 / 本地支付 3-5 工作日 | `policy-refund-timeline.md` |
| 物流停滞判定 | 3 天（跨境干线 5 天） | `policy-logistics-exception.md` |
| 确认丢失 | 停滞超 15 天，自动全额退款 | `policy-logistics-exception.md` |
| 停滞 7 天处理 | 补发或全额退款（二选一） | `policy-logistics-exception.md` |
| 发货时效 | 现货 48 小时内（大促 72 小时） | 本目录 FAQ |
| 保价窗口 | 付款后 15 天，上限实付 30% | 本目录促销政策 |

> ⚠️ **既有 demo-kb 内部口径不一致（本次未修改，入库前需处置）**：
> `policy-return-us.md` 写「验收合格后 3-5 个工作日退回」，而 `policy-refund-timeline.md` 拆为
> 「审核 1-2 + 验收 2 + 渠道 5-7/1-3」。两者并存会在检索时召回两条不同时效，**建议统一为后者（拆分口径）**，
> 或在前者补一句「各环节时限见退款时限政策」。本目录新增文档已统一采用拆分口径。


## 五、入库方式

本轮**只产出文档，尚未入库**。入库时：

1. 本目录 7 份 + `demo-kb/` 11 份按映射表一并上传；
2. `department=customer`，kb_id 按上表；
3. 上传后跑通场景验证：
   - 「美国订单退货需要满足什么条件？」→ `cs_policy` 命中 `policy-return-us.md`
   - 「能开专票吗？」→ `cs_policy` 命中本目录 `policy-invoice-tax.md`
   - 「我刚买就降价了怎么办？」→ `cs_policy` 命中本目录 `policy-promotion-price.md`
   - 「南极洲订单能退吗」→ 应触发证据门禁拒答（知识库故意不含）

## 六、与国内套的关系

| | 国内套 `../domestic/` | 跨境套（本目录 + demo-kb） |
|---|---|---|
| 无理由窗口 | 7 天（大促 15 天） | 30 天（美国，大促 45 天） |
| 退款到账 | 1-3 工作日（信用卡 3-7） | 信用卡 5-7 / 电子钱包 1-3 |
| 支付方式 | 支付宝/微信/花呗/信用卡 | 信用卡/PayPal/本地支付/支付宝 |
| 关税 | 不涉及 | DDP / DDU 两套口径 |
| 维权渠道 | 12315 / 平台介入 | 监管机构 / 平台 / **Chargeback** |

> ⚠️ **两套数字不同，禁止混入同一 kb**（会造成检索到冲突答案 → 触发证据冲突或错误回答）。
> 若同一 kb 需同时承载两套口径，必须先做口径分层（按 `department` 或文档前缀区分）再入库。

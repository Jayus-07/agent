# 跨境电商业务领域设计文档

> **文档版本**: v0.1 (Phase 1 — Business Domain)
> **状态**: 等待业务方确认后进入数据库设计阶段
> **公司画像**: Meridian Global Commerce — 一家中型跨境电商公司，自营品牌为主，5 个销售平台，3 个海外市场，~150 名员工，年营收 30-50M USD，活跃 SKU ~8000，月订单 ~5 万

---

## 1. 公司业务流程

### 1.1 公司画像

**Meridian Global Commerce** 是一家总部位于深圳的跨境电商公司，主营家居用品和小型电子产品。运营模式：自有品牌设计 → 国内代工生产 → 多平台销售（Amazon / Shopify / TikTok Shop / eBay / Walmart）。

| 维度 | 数值 |
|---|---|
| 员工数 | ~150 |
| 年营收 | 30-50M USD |
| 活跃 SKU | ~8,000 |
| 月订单量 | ~50,000 |
| 销售平台 | Amazon (US/EU/JP), Shopify, TikTok Shop, eBay, Walmart |
| 销售市场 | 美国 (60%), 欧洲 (25%), 日本 (10%), 其他 (5%) |
| 海外仓 | Amazon FBA (US/EU/JP), 海外 3PL (美西 / 美东 / 德国 / 日本) |
| 工厂供应商 | ~30 家（深圳、东莞、宁波） |
| 客服团队 | ~20 人（多语种） |
| 广告投放 | Amazon Ads, Google Ads, Meta Ads, TikTok Ads |

### 1.2 核心业务模块

```
┌─────────────────────────────────────────────────────────────────┐
│                    跨境电商业务全景                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │  商品     │  │  采购     │  │  库存     │  │  仓储     │        │
│  │  Product │→ │ Purchase │→ │Inventory │→ │Warehouse │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
│       ↑                                            │           │
│       │                                            ↓           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │  运营     │  │  销售     │  │  订单     │  │  物流     │        │
│  │Operation │← │  Channel │← │  Order   │← │Logistics │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
│       │                                            │           │
│       ↓                                            ↓           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │  广告     │  │  客户     │  │  售后     │  │  客服     │        │
│  │  Ads     │→ │ Customer │→ │After-Sales│→ │  Support │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────┐        │
│  │   企业知识库 (Amazon SOP / Listing 规范 / 培训)        │        │
│  └─────────────────────────────────────────────────────┘        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────┐        │
│  │   经营分析 (日报 / 周报 / 月报 / KPI Dashboard)         │        │
│  └─────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 关键业务流程（7 个）

#### 流程 1：商品生命周期（核心）

```
选品调研 → 供应商匹配 → 样品评估 → 决定开发
   ↓
SPU 创建（设计 + 类目 + 定位） → 工厂打样
   ↓
SKU 规划（颜色 × 尺寸 × 包装）→ 工厂量产
   ↓
Listing 编写（多语言标题 / 五点描述 / A+ / 关键词）→ 多平台发布
   ↓
定价策略（成本 + 头程 + 平台费 + 广告费 + 利润）→ 售价
   ↓
库存分配（首批 FBA / 3PL / 国内仓）→ 持续补货决策
   ↓
上架销售 → 评论积累 → 排名优化 → 成熟 / 衰退 / 淘汰
```

#### 流程 2：采购流程

```
需求预测（基于销售 + 库存 + 在途）→ 采购计划
   ↓
供应商比价（多供应商 PO）→ PO 创建
   ↓
生产跟踪（交期 / 质量 / 验货）→ 工厂发货
   ↓
入库（国内仓收货 → QC → 上架）→ 头程发货
   ↓
对账 → 付款
```

#### 流程 3：订单履约流程

```
买家下单（Amazon / Shopify / TikTok Shop）
   ↓
OMS 拉取订单（API 轮询 / Webhook）
   ↓
风控检查（黑名单 / 拒付风险 / 异常地址）
   ↓
库存分配决策：
  - 优先 FBA（FBA 订单自动从 FBA 发）
  - 自发货：从 3PL 或 国内仓发
   ↓
打单 + 拣货 + 包装
   ↓
物流分配（USPS / FedEx / UPS / DHL / 本地邮政）
   ↓
出库 + 上传追踪号
   ↓
物流跟踪 → 签收确认
   ↓
结算（平台回款 / 退款扣款）
```

#### 流程 4：物流流程

```
┌─ 头程 ──────────────────────────┐
│  工厂 → 国内仓 → 国际运输 → 清关 → 海外仓入库
└─────────────────────────────────┘
                                    ↓
┌─ 尾程 ──────────────────────────┐
│  海外仓 → 拣货 → 包裹 → 当地物流 → 买家
└─────────────────────────────────┘
                                    ↓
┌─ 退货 ──────────────────────────┐
│  买家 → 海外退货地址 → 检测 → 重新入库 / 销毁 / 退回国内
└─────────────────────────────────┘
```

#### 流程 5：广告投放流程

```
年度预算分配（按渠道 / 按品类）→ 月度预算
   ↓
Campaign 创建（关键词 / 商品定向 / 受众）→ 上线
   ↓
每日监控（花费 / ACoS / ROAS / TACoS）
   ↓
优化（调价 / 加词 / 否定词 / 暂停低效）
   ↓
复盘（每周 / 每月）→ 调整预算
```

#### 流程 6：客服与售后流程

```
买家咨询（站内信 / 邮件 / 平台 IM）
   ↓
客服分类（售前 / 物流 / 退货 / 投诉）
   ↓
查询订单状态 / 政策匹配 → 回复
   ↓
如需退货：授权 → 退货地址 → 验收 → 退款 / 重发
   ↓
如需投诉：升级 → 工单 → 跟进 → 关闭
   ↓
评价邀请 / 差评应对
```

#### 流程 7：经营分析流程

```
每日：销售额 / 订单数 / 库存预警 / 广告花费
   ↓
每周：品类表现 / SKU 排行 / 退货率 / 利润率
   ↓
每月：渠道利润核算 / 现金流 / 库存周转
   ↓
每季：选品方向 / 战略调整 / 供应商评估
```

---

## 2. 业务领域（Domain）

### 2.1 领域划分

跨 9 个核心领域（外加 1 个贯穿型领域"集成"）：

| 领域 | 英文 | 职责 | 数据特点 |
|---|---|---|---|
| 商品 | Product | SPU/SKU/Listing/分类/品牌 | 大量读、偶发写、跨平台 |
| 供应商 | Supplier | 工厂管理、PO、到货跟踪 | 中等量、流程性 |
| 库存 | Inventory | 多仓库存、批次、调拨、在途 | 高频更新、强一致性 |
| 订单 | Order | 跨平台订单统一、履约 | 高频写入、状态机 |
| 物流 | Logistics | 头程、尾程、追踪、异常 | 事件驱动、状态机 |
| 客户 | Customer | 终端用户画像、终身价值 | 大量读、跨平台合并 |
| 广告 | Advertising | 跨平台投放、归因、KPI | 高频同步、性能敏感 |
| 知识 | Knowledge | SOP / 制度 / FAQ / 培训 | 文档为主、版本控制 |
| 报表 | Report | 经营分析、KPI、可视化 | 聚合查询、周期任务 |

### 2.2 领域关系图

```
                       ┌─────────────┐
                       │  知识        │
                       │ Knowledge    │
                       │ (SOP/FAQ)   │
                       └─────────────┘
                             ↑ 读
                             │
┌──────────┐  写  ┌─────────┴──┐  读  ┌──────────┐
│  供应商  │ ←── │  采购管理  │ ←── │  商品     │
│ Supplier │     │(PO 跟踪)  │     │ Product  │
└──────────┘     └─────────┬──┘     │(SPU/SKU) │
   ↑                     │ 触发     └──────────┘
   │                     ↓             ↑ ↓
   │                  ┌─────────┐     │ │
   │                  │  库存   │ ←───┘ │
   │                  │Inventory│       │
   │                  └────┬────┘       │
   │                       ↓ 上架        │
   │                  ┌─────────┐       │
   │                  │  物流   │ ←─ 头程 │
   │                  │Logistics│       │
   │                  └────┬────┘       │
   │                       ↓ 出库        │
   │                  ┌─────────┐  下单  │
   └─ 售后/退货 ──→   │  订单   │ ←──────┘
                       │ Order   │
                       └────┬────┘
                            ↓ 售出
                       ┌─────────┐
                       │  客户   │ ←─── 客服 ──── 售后
                       │Customer │      Support    After-Sales
                       └────┬────┘
                            ↓ 行为
                       ┌─────────┐
                       │  广告   │ ←── 商品 + 库存（库存决定能否投）
                       │  Ads   │
                       └────┬────┘
                            ↓ 归因
                       ┌─────────┐
                       │  报表   │ ←── 全部领域
                       │ Report  │
                       └─────────┘
```

**关键关系**：
- **商品** 是核心，所有领域围绕 SKU 联动
- **库存** 和 **订单** 是高频写入的两个领域，通过 SKU 关联
- **物流** 是订单履约的物理执行者
- **广告** 消耗商品 + 库存 + 客户数据
- **知识** 是只读支撑，被客服、运营、广告、Listing 等环节消费
- **报表** 是只读聚合，跨所有领域
- **客户** 是终点，串联营销、销售、客服

---

## 3. 数据模型

> 接下来是**领域模型（Domain Model）**——核心实体 + 关键字段 + 实体间关系。**不写 SQL、不写 ORM**。

### 3.1 商品域（Product）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **Product (SPU)** | product_id, code, name, brand_id, category_id, status, lifecycle_stage, target_market, owner_user_id | 1:N → SKU；N:1 → Brand；N:1 → Category |
| **SKU** | sku_id, product_id, variant_attrs(JSON), barcode, weight_g, length_mm, width_mm, height_mm, hs_code, country_of_origin, status | N:1 → Product；1:N → Listing；1:N → InventoryLevel；1:N → OrderItem |
| **Listing** | listing_id, sku_id, channel, channel_listing_id, locale, title, bullet_points, description, images, keywords, status, currency, price | N:1 → SKU；N:1 → Channel |
| **Brand** | brand_id, name, trademark_no, owner | 1:N → Product |
| **Category** | category_id, parent_id, name, channel_category_map(JSON) | self-ref；N:1 → Product |

**关键约束**：
- SPU（产品） vs SKU（库存单位）严格分离。一个 SPU 可有 N 个 SKU（颜色 × 尺寸 × 包装）
- Listing 是 SKU 在某个平台的具体上架商品。同一个 SKU 可在多平台上架
- SKU 编码规则：`<SPU>-<变体序号>`（如 `MK202-RED-L`）

### 3.2 供应商域（Supplier）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **Supplier** | supplier_id, name, type(MANUFACTURER/WHOLESALER), country, contact_name, contact_email, contact_phone, payment_terms, cooperation_status, rating | 1:N → PurchaseOrder；1:N → ProductSupplier |
| **ProductSupplier** | product_id, supplier_id, cost_price, currency, moq, lead_time_days, is_primary, valid_from, valid_to | N:1 → Product；N:1 → Supplier |
| **PurchaseOrder (PO)** | po_id, supplier_id, status(DRAFT/SENT/PRODUCING/SHIPPED/RECEIVED/CLOSED/CANCELLED), order_date, expected_date, received_date, total_amount, currency, incoterm | 1:N → PurchaseOrderItem |
| **PurchaseOrderItem** | po_id, line_id, sku_id, quantity, unit_cost, quantity_received | N:1 → PO；N:1 → SKU |

**关键约束**：
- ProductSupplier 是 SKU 与 Supplier 的多对多桥（同一产品可多供应商）
- PO 是核心流程单据，状态机驱动
- PO 关闭后，InventoryLevel 才会反映新库存

### 3.3 库存域（Inventory）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **Warehouse** | warehouse_id, code, name, type(DOMESTIC/FBA/3PL/TRANSIT/VIRTUAL), country, region, address, is_active, partner(用于 3PL API) | 1:N → InventoryLevel；1:N → Shipment |
| **InventoryLevel** | warehouse_id, sku_id, qty_on_hand, qty_reserved, qty_available(派生), qty_in_transit, last_updated, sync_source | 复合主键(warehouse, sku) |
| **InventoryTransaction** | txn_id, warehouse_id, sku_id, type(INBOUND/OUTBOUND/TRANSFER/ADJUSTMENT/RESERVE/RELEASE), quantity, ref_type, ref_id, occurred_at, operator_id | N:1 → Warehouse；N:1 → SKU |
| **InventoryHealth** | sku_id, warehouse_id, days_of_supply, sell_through_rate, age_bucket(0-30/31-90/91-180/180+), last_sale_at, status(HEALTHY/SLOW/AGED/DEAD) | N:1 → SKU；N:1 → Warehouse |
| **StockReservation** | reservation_id, sku_id, warehouse_id, quantity, ref_type, ref_id, expires_at, status | N:1 → SKU；N:1 → Warehouse |

**关键约束**：
- InventoryLevel 是 SKU 在某仓的"当前快照"（聚合查询）
- InventoryTransaction 是不可变流水（append-only）
- `qty_available = qty_on_hand - qty_reserved`（派生，不存）
- 多仓 + 在途 = 公司总库存

### 3.4 订单域（Order）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **Channel** | channel_id, code(AMAZON/SHOPIFY/...)，name, country, default_currency, api_credentials_id, status | 1:N → Order；1:N → Listing |
| **Order** | order_id, channel_id, channel_order_id, customer_id, status(PENDING/PAID/ALLOCATED/PICKING/SHIPPED/DELIVERED/CANCELLED/REFUNDED), order_total, currency, placed_at, paid_at, fulfilled_at, refunded_at | N:1 → Channel；N:1 → Customer；1:N → OrderItem；1:N → Shipment |
| **OrderItem** | order_id, line_id, sku_id, quantity, unit_price, line_total, status | N:1 → Order；N:1 → SKU |
| **OrderEvent** | event_id, order_id, from_status, to_status, reason, occurred_at, operator_id | N:1 → Order |

**关键约束**：
- Order 是**统一订单**模型（不管哪个平台，都用同一份 schema）
- `channel_order_id` 保留原平台订单号（用于对账）
- OrderItem 数量跨 SKU，**一个订单可以拆单发**（多个 Shipment）
- 状态机：`PENDING → PAID → ALLOCATED → PICKING → SHIPPED → DELIVERED`，可逆向 `CANCELLED` / `REFUNDED`

### 3.5 物流域（Logistics）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **FreightBooking (头程)** | booking_id, supplier_id, origin_warehouse_id, dest_warehouse_id, carrier, mode(SEA/AIR/EXPRESS/TRUCK), etd, eta, status, container_no, tracking_no, cost | N:1 → Supplier；N:1 → Warehouse |
| **Shipment (尾程)** | shipment_id, order_id, warehouse_id, carrier, service_level, tracking_no, weight_g, length_mm, width_mm, height_mm, declared_value, status, shipped_at, delivered_at, cost | N:1 → Order；N:1 → Warehouse；1:N → ShipmentItem；1:N → TrackingEvent |
| **ShipmentItem** | shipment_id, line_id, order_item_id, sku_id, quantity | N:1 → Shipment；N:1 → OrderItem；N:1 → SKU |
| **TrackingEvent** | event_id, shipment_id, status_code, description, location, occurred_at, source | N:1 → Shipment |
| **ReturnAuthorization** | ra_id, order_id, customer_id, reason, status(REQUESTED/APPROVED/REJECTED/RECEIVED/INSPECTED/REFUNDED), requested_at, received_at, refund_amount | N:1 → Order；N:1 → Customer |
| **ReturnItem** | return_id, line_id, order_item_id, sku_id, quantity, condition(NEW/DAMAGED/DEFECTIVE), disposition(RESTOCK/DISPOSE/RETURN_TO_SUPPLIER) | N:1 → ReturnAuthorization |
| **CustomsDeclaration** | declaration_id, freight_booking_id, hs_codes(JSON), declared_value, currency, status, cleared_at | N:1 → FreightBooking |

**关键约束**：
- **头程（FreightBooking）** 和 **尾程（Shipment）** 是两个不同生命周期：头程是供应商→海外仓，尾程是订单→买家
- 一个 Order 可拆成多个 Shipment（多包裹）
- TrackingEvent 是从承运商 webhook / API 轮询获取的标准化轨迹

### 3.6 客户域（Customer）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **Customer** | customer_id, channel_id, channel_user_id, name, email, phone, country, locale, segment, lifetime_value, ltv_tier, first_order_at, last_order_at, order_count | 1:N → Order；1:N → CustomerAddress；1:N → Review |
| **CustomerAddress** | address_id, customer_id, line1, line2, city, state, postcode, country, is_default | N:1 → Customer |
| **CustomerInteraction** | interaction_id, customer_id, channel, type(INQUIRY/MESSAGE/COMPLAINT/REVIEW), content, sentiment, occurred_at, agent_user_id | N:1 → Customer |
| **CustomerSegment** | segment_id, name, description, criteria(JSON) | 1:N → Customer |
| **Review** | review_id, customer_id, channel, channel_review_id, sku_id, order_id, rating(1-5), title, content, language, posted_at | N:1 → Customer；N:1 → SKU；N:1 → Order |

**关键约束**：
- **Customer 是终端买家**（不是企业客户 B2B）
- Customer 可能跨平台重复购买（一个买家在 Amazon 和 Shopify 都有账户）——目前按 channel 分开存储，由 customer_id 唯一识别
- 终极合并（identity resolution）需要后期 ETL 或单独模块，Phase 2 再做
- Review 是客户域的特殊实体，跨 Order 和 SKU

### 3.7 广告域（Advertising）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **AdAccount** | ad_account_id, channel, account_id, currency, status, credentials_id | 1:N → Campaign |
| **Campaign** | campaign_id, ad_account_id, channel, name, type(SP/PRODUCT_DISPLAY/BRAND/VIDEO), status, daily_budget, total_budget, start_date, end_date, target_market | N:1 → AdAccount；1:N → AdGroup |
| **AdGroup** | ad_group_id, campaign_id, name, default_bid, targeting(JSON), status | N:1 → Campaign；1:N → Ad |
| **Ad** | ad_id, ad_group_id, type(KEYWORD/PRODUCT/ASIN/AUTO), target_id, status, bid | N:1 → AdGroup；1:N → SpendRecord；1:N → PerformanceMetric |
| **Keyword** | keyword_id, ad_group_id, text, match_type(EXACT/PHRASE/BROAD), bid, status | N:1 → AdGroup |
| **SpendRecord** | spend_id, ad_id, date, spend, impressions, clicks, conversions, sales | N:1 → Ad |
| **PerformanceMetric** | metric_id, ad_id, sku_id, date, attributed_units, attributed_sales, cpc, ctr, acos, tacos, roas | N:1 → Ad；N:1 → SKU |
| **AttributionWindow** | window_id, channel, click_window_days, view_window_days | — |

**关键约束**：
- 各平台数据 schema 差异大（Amazon Ads / Google Ads / Meta Ads），通过 `channel` 字段统一
- PerformanceMetric 区分 **平台报告指标** (SpendingReport) 和 **归因指标** (Attribution)——前者是广告花费，后者是销售归因
- 关注指标：ACoS（广告花费/广告订单销售额）、TACoS（广告花费/总销售额）、ROAS（销售额/花费）

### 3.8 知识域（Knowledge）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **KnowledgeCategory** | category_id, parent_id, name, code(AMAZON_SOP/LISTING/AD/CUSTOMER_SERVICE/WAREHOUSE/PRODUCT/TRAINING/POLICY) | self-ref |
| **KnowledgeDoc** | doc_id, category_id, title, content, content_type(MARKDOWN/PDF/HTML), source(URL/INTERNAL/IMPORT), version, language, valid_from, valid_to, is_active, author_id, last_updated_at, embedding_status | N:1 → Category；1:N → KnowledgeChunk；N:1 → Author(User) |
| **KnowledgeChunk** | chunk_id, doc_id, chunk_index, content, embedding(vector), token_count | N:1 → KnowledgeDoc |
| **KnowledgeFeedback** | feedback_id, doc_id, query, helpful(Y/N), comment, user_id, created_at | N:1 → KnowledgeDoc |

**关键约束**：
- 知识有版本（Amazon 政策经常变）
- 知识有有效期（某些政策在某日期后失效）
- 知识按 category 分类（多平台/多领域）

### 3.9 报表域（Report）

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **ReportDefinition** | report_id, name, description, category(SALES/INVENTORY/AD/FINANCE/OPERATIONS), sql_template, params_schema(JSON), owner_id, schedule_cron | — |
| **ReportExecution** | execution_id, report_id, params(JSON), status(RUNNING/SUCCESS/FAILED), started_at, finished_at, result_url, error | N:1 → ReportDefinition |
| **ReportSubscription** | sub_id, report_id, user_id, channel(EMAIL/SLACK), schedule | N:1 → ReportDefinition |
| **Dashboard** | dashboard_id, name, owner_id, layout(JSON) | 1:N → DashboardWidget |
| **DashboardWidget** | widget_id, dashboard_id, report_id, position, config(JSON) | N:1 → Dashboard；N:1 → ReportDefinition |

**关键约束**：
- ReportDefinition 存**模板 + 参数 schema**，实际 SQL 不直接存，由 SQL Agent 动态生成
- ReportExecution 存历史
- Dashboard 是 Report 的可视化容器

### 3.10 集成域（Integration）— 贯穿

| 实体 | 关键字段 | 关系 |
|---|---|---|
| **ChannelCredential** | credential_id, channel, account_id, access_key, secret_key, refresh_token, expires_at | 1:1 → Channel；1:1 → AdAccount |
| **ApiSyncJob** | job_id, source, resource_type, schedule_cron, last_run_at, last_status, last_error | — |
| **ApiSyncLog** | log_id, job_id, started_at, finished_at, records_fetched, records_written, error | N:1 → ApiSyncJob |

**说明**：集成域不暴露给业务用户，只在数据同步时使用。每个外部系统（Amazon / Shopify / TikTok Shop / Google Ads 等）的 API 凭证独立存储，凭证轮换有审计日志。

### 3.11 实体关系总图

```
                ┌──────────┐
                │ Channel  │ ←─ 1:N ─→ ChannelCredential
                └────┬─────┘
                     │ 1:N
                     ↓
┌──────────┐  N:1  ┌────────┐  1:N  ┌────────┐
│ Customer ├─────→│  Order  ├─────→│OrderItem│
└────┬─────┘      └────┬─────┘      └───┬────┘
     │ 1:N            │ 1:N             │ N:1
     ↓                ↓                 ↓
┌──────────┐      ┌──────────┐      ┌────────┐
│  Review  │      │ Shipment ├─────→│  SKU   │ ←─┐
└──────────┘      └────┬─────┘      └───┬────┘   │
                      │ 1:N            │ 1:N    │ 1:N
                      ↓                ↓        │
                 ┌────────┐      ┌────────┐   ┌────────┐
                 │Tracking│      │Listing │   │Product │
                 │ Event  │      └────────┘   │ (SPU)  │
                 └────────┘                   └────┬───┘
                                                   │ 1:N
                                                   ↓
                                              ┌────────┐
                                              │  PO    │ N:1 → Supplier
                                              │  Item  │
                                              └────────┘

所有 SKU 都涉及: InventoryLevel（多仓） + PerformanceMetric（广告归因）
```

---

## 4. 企业知识库设计

### 4.1 知识分类（8 大类）

| 类别 | 代码 | 内容示例 | 文档量级 |
|---|---|---|---|
| **Amazon SOP** | AMAZON_SOP | 账号注册流程、Listing 提交政策、Buy Box 规则、类目审批、品牌备案 | 200-500 篇 |
| **Listing 编写规范** | LISTING | 标题公式、五点描述模板、A+ 内容指南、关键词策略、禁词表 | 50-100 篇 |
| **广告规范** | AD | Amazon Ads 投放规则、Google Ads 政策、Meta 创意规范、ACoS 优化方法 | 100-200 篇 |
| **客服 FAQ** | CUSTOMER_SERVICE | 物流时效、退货政策、尺寸咨询、产品使用、投诉处理 | 300-800 篇 |
| **仓储制度** | WAREHOUSE | 入库流程、拣货 SOP、库位管理、盘点制度、FBA 发货要求 | 50-100 篇 |
| **产品资料** | PRODUCT | 产品规格、材质说明、使用手册、保养指南、故障排查 | 500-2000 篇 |
| **培训文档** | TRAINING | 新人手册、岗位 SOP、晋升路径、绩效考核 | 30-50 篇 |
| **公司制度** | POLICY | 报销、考勤、差旅、绩效、薪酬福利 | 30-50 篇 |

**总量估算**：1500-4000 篇文档

### 4.2 存储分层

| 内容类型 | 推荐存储 | 理由 |
|---|---|---|
| **客服 FAQ + 培训 + SOP** | 向量库（pgvector / ChromaDB） | 语义检索、按问题找答案 |
| **Listing 编写规范** | 向量库 + 结构化字段 | 既要语义匹配，也要按品类/平台结构化匹配 |
| **产品资料** | 向量库 + 关系数据库 metadata | 全文语义 + 规格参数化 |
| **Amazon SOP / 政策** | 向量库（按时效过滤） | 版本控制 + 有效期 |
| **公司制度** | 关系数据库 + 向量库 | 编号索引（HR-001）+ 全文检索 |
| **产品 SKU 规格表** | **关系数据库（Product / SKU）** | 严格结构化，向量检索语义不准 |
| **订单状态** | **关系数据库（Order）** | 强一致性需求 |
| **库存数量** | **关系数据库（InventoryLevel）** | 强一致性需求 |
| **广告花费** | **关系数据库（AdSpend）** | 需要精确聚合 |
| **客户订单历史** | **关系数据库（Customer / Order）** | 严格结构化 |

### 4.3 知识元数据

每篇文档应包含：

```yaml
- doc_id          唯一 ID
- category        类别
- title           标题
- content         原文（Markdown / PDF）
- source          URL / INTERNAL / IMPORT
- version         版本号
- language        zh / en / ja / de
- valid_from      生效日期
- valid_to        失效日期（永久可用则 null）
- is_active       是否启用
- author_id       作者
- last_updated_at
- tags            标签（多值）
- channel         关联平台（Amazon / Shopify / All）
- related_skus    关联 SKU（可空）
```

### 4.4 检索策略

- **粗排**：pgvector cosine similarity（top 20）
- **精排**：CrossEncoder（top 5）—— **只用于客服场景**（质量要求高）
- **过滤**：按 category / channel / validity 时段
- **Fallback**：检索不到时返回"我不知道"（不幻觉）

---

## 5. AI 可调用的数据源

### 5.1 统一抽象：`DataSource` 接口

不直接实现，但**接口契约**长这样：

```python
# 仅概念示意，不写实现

class DataSource(Protocol):
    """AI 可调用的数据源统一抽象"""
    
    name: str                                  # 唯一标识
    description: str                           # 人类可读描述
    source_type: Literal["RELATIONAL", "FILE", "API", "EVENT", "KNOWLEDGE"]
    
    async def query(self, spec: QuerySpec) -> QueryResult: ...
    async def health_check(self) -> bool: ...
    async def schema(self) -> Schema: ...
```

### 5.2 数据源类型

| 类型 | 实现 | 数据源 | 用途 |
|---|---|---|---|
| **RELATIONAL** | `RelationalDataSource` | PostgreSQL（业务库） | SQL Agent 分析 |
| | | PostgreSQL（数据仓库） | 历史数据 / 跨域分析 |
| | | 内部系统数据库（ERP / WMS） | 库存 / 财务 |
| **FILE** | `FileDataSource` | CSV（导出报表） | 离线分析 |
| | | Excel（运营上传） | 临时分析 |
| | | JSON / Parquet | 数据集 |
| **API** | `MarketplaceDataSource` | Amazon SP-API | 订单 / 商品 / 库存 |
| | | Shopify Admin API | 订单 / 商品 / 客户 |
| | | TikTok Shop Open API | 订单 / 商品 |
| | | Google Ads API | 广告数据 |
| | | Meta Marketing API | 广告数据 |
| | | TikTok Ads API | 广告数据 |
| **EVENT** | `EventDataSource` | 物流 webhook | 轨迹推送 |
| | | 站内消息 webhook | 客服事件 |
| | | 平台消息 webhook | Amazon / Shopify 消息 |
| **KNOWLEDGE** | `KnowledgeDataSource` | ChromaDB / pgvector | SOP / FAQ / 培训 |
| | | 文件存储（PDF / Word / MD） | 上传资料 |

### 5.3 元数据

每个 DataSource 必须注册元数据：

```yaml
name: amazon_orders
description: Amazon 订单数据，包含订单状态、金额、地址
source_type: API
schema:
  order_id: str
  order_total: float
  currency: str
  status: enum
  placed_at: datetime
update_frequency: realtime  # or 5min, 1h, 1d
freshness: T+0  # 数据延迟
reliability: SLA 99.9%
auth: OAuth2
rate_limit: 1000 req / hour
```

### 5.4 权限与隔离

- **按角色**（RBAC）：运营 / 客服 / 财务 / 高管看到的 DataSource 不同
- **按场景**（用途）：数据导出 vs 实时查询 vs AI 推理
- **审计日志**：所有 AI 查询必须记录（谁问的、问的什么、返回什么）
- **敏感数据脱敏**：客户邮箱、手机号、信用卡号在 AI 路径上脱敏

### 5.5 抽象层价值

- AI 不用关心数据源是 SQL 还是 API
- 新增数据源只需注册，不用改 AI 代码
- 限流 / 重试 / 缓存 / 降级在抽象层统一处理
- 可观测性统一

---

## 6. AI 能力映射

### 6.1 AI 能力分类

| 能力 | 描述 | 典型技术 |
|---|---|---|
| **Retrieval** | 从知识库 / 文档中检索 | RAG (vector + rerank) |
| **Data Analysis** | 跨表 SQL 查询 / 统计 | SQL Agent + LLM 拆解问题 |
| **Content Generation** | 文案 / 标题 / 描述生成 | LLM + 模板 |
| **Report Generation** | 数据 + 模板 + LLM 润色 | SQL + Template + LLM |
| **Workflow** | 多步复杂流程编排 | LangGraph Multi-Agent |

### 6.2 业务场景 vs 能力矩阵

| 业务场景 | Retrieval | Data Analysis | Content Gen | Report Gen | Workflow |
|---|---|---|---|---|---|
| **客服 FAQ 自动回复** | ★★★ | ○ | ★★ | — | — |
| **订单状态查询** | ○ | ★★★ | ★ | — | — |
| **Listing 标题 / 五点优化** | ★★ | ★ | ★★★ | — | — |
| **Listing 多语言翻译** | ★ | — | ★★★ | — | — |
| **产品描述 / A+ 内容生成** | ★ | — | ★★★ | — | — |
| **选品调研报告** | ★★ | ★★★ | ★★ | ★★★ | ★★ |
| **竞品分析** | ★ | ★★★ | ★★ | ★★ | ★ |
| **库存预警** | — | ★★★ | ★ | ★★ | — |
| **补货建议** | ★ | ★★★ | ★ | ★★ | ★ |
| **广告效果分析** | ★ | ★★★ | ★ | ★★ | — |
| **广告关键词建议** | ★★ | ★★ | ★★★ | — | — |
| **广告创意文案** | ★★ | ○ | ★★★ | — | — |
| **退货分析** | ★ | ★★★ | ★ | ★★★ | ★ |
| **客户评论分析** | ★ | ★★ | ★★ | ★★ | — |
| **差评应对话术** | ★★ | — | ★★★ | — | — |
| **经营周报** | ★ | ★★★ | ★★ | ★★★ | ★★ |
| **品类复盘** | ★ | ★★★ | ★★ | ★★★ | ★★ |
| **供应链风险预警** | ★ | ★★★ | ★★ | ★★ | ★ |
| **新员工培训问答** | ★★★ | — | — | — | — |
| **合同 / 政策查询** | ★★★ | — | — | — | — |

> ★★★ = 强依赖 ｜ ★★ = 中等依赖 ｜ ★ = 弱依赖 ｜ ○ = 偶尔 ｜ — = 不用

### 6.3 关键业务场景的 Agent 角色分配

| 业务场景 | 推荐 Agent 角色 | 核心能力 |
|---|---|---|
| **客服** | CustomerServiceAgent | Retrieval + Content Gen |
| **Listing** | ListingAgent | Retrieval + Content Gen（多语言） |
| **选品** | SourcingAgent | Retrieval + Data Analysis + Report Gen |
| **运营监控** | OperationsAgent | Data Analysis + Report Gen + Alerting |
| **广告** | AdsAgent | Retrieval + Data Analysis + Content Gen |
| **供应链** | SupplyChainAgent | Data Analysis + Workflow + Report |
| **培训 / 知识** | KnowledgeAgent | Retrieval + Content Gen |
| **经营分析** | ExecutiveAgent | Data Analysis + Report Gen + Workflow |

> **注**：这是**能力映射**，不是具体实现。后续 Phase 2 / 3 才进入 Agent 设计。

### 6.4 跨能力组合示例

**例：选品调研**（多能力组合）
1. **Retrieval**：从知识库检索"市场需求 + 趋势"相关报告
2. **Data Analysis**：SQL 查询该品类历史销售、毛利率、退货率
3. **Content Generation**：LLM 综合生成"该品类可不可做"的分析报告
4. **Report Generation**：套用模板输出 PDF / Markdown
5. **Workflow**：拆 5 步 → Planner → Retrieval → SQL → LLM 综合 → Template

---

## 7. 后续开发路线（PR-by-PR）

### 7.1 总体原则

- **每个 PR 独立可发布**：合并后系统仍可运行，不破坏现有功能
- **每个 PR 独立可测试**：有单元测试 + 集成测试
- **按依赖顺序**：底层 → 上层 → 业务 → 优化
- **数据先行**：先建 schema（domain model → SQL），再做业务逻辑
- **真实数据**：每个 PR 用真实业务场景验证（不用合成数据）

### 7.2 PR 路线（12 步）

| 阶段 | PR # | 标题 | 关键交付 | 预计工时 |
|---|---|---|---|---|
| **基础设施** | PR-1 | **领域模型 + Master Data 持久化** | Product/SKU/Brand/Category/Channel/Customer/Supplier 的 schema + Repository + 基础 CRUD | 1 周 |
| | PR-2 | **数据源抽象层** | `DataSource` 协议 + 5 个基础实现（Postgres / CSV / Amazon SP-API / Google Ads / pgvector）；注册中心 + 元数据 | 1.5 周 |
| | PR-3 | **企业知识库 + RAG 引擎** | KnowledgeDoc/Chunk schema + 文档导入工具 + Retrieval Service（粗排 + 精排） | 1.5 周 |
| **业务核心** | PR-4 | **Inventory 域（多仓 + 流水 + 调拨）** | InventoryLevel/Transaction schema + 调拨流程 + 健康度计算 | 1.5 周 |
| | PR-5 | **Order 域（统一订单 + 状态机）** | Order/OrderItem/OrderEvent schema + 状态机 + Amazon/Shopify 接入 | 2 周 |
| | PR-6 | **Logistics 域（头程 + 尾程 + 追踪）** | FreightBooking/Shipment/TrackingEvent + 物流商 API 接入 | 1.5 周 |
| | PR-7 | **Advertising 域（Campaign + Spend + 归因）** | AdAccount/Campaign/Ad/Spend/PerformanceMetric + 各平台同步 | 2 周 |
| **AI 能力** | PR-8 | **SQL Agent 增强** | 跨域 SQL 查询 + 报表自动生成 + 接入 PostgreSQL + 文件数据源 | 1 周 |
| | PR-9 | **Report Agent 增强** | ReportDefinition 模板 + 调度 + Dashboard + 订阅 | 1 周 |
| | PR-10 | **Listing Agent**（首个业务 Agent） | Listing 生成 + 多语言 + 优化建议 | 1.5 周 |
| **业务化** | PR-11 | **Customer Service Agent** | FAQ 自动回复 + 订单状态查询 + 工单升级 | 1.5 周 |
| | PR-12 | **Ads Agent** | 广告报告 + 关键词建议 + 创意文案 | 1.5 周 |

**总工时估算**：~17 周（4 个月）

### 7.3 PR 依赖图

```
PR-1 (领域 + Master Data)
   ↓
PR-2 (数据源抽象层) ←─── PR-3 (知识库 + RAG)
   ↓                         ↓
PR-4 (Inventory)            ↓
   ↓                         ↓
PR-5 (Order) ←─────────── PR-6 (Logistics) ←─── PR-7 (Advertising)
   ↓                         ↓                         ↓
PR-8 (SQL Agent 增强) ←─── PR-9 (Report Agent 增强)
   ↓
PR-10 (Listing Agent) ────→ 业务 Agent 模板（可复用）
   ↓
PR-11 (Customer Service Agent)
   ↓
PR-12 (Ads Agent)
```

### 7.4 PR 详细说明（每步可独立 PR）

#### PR-1: 领域模型 + Master Data 持久化

**目标**：建底层数据表（领域模型 → SQL），不写任何业务逻辑。

**包含**：
- SQL schema（Product / SKU / Brand / Category / Channel / Customer / Supplier / Warehouse）
- SQLAlchemy ORM 模型
- Alembic 迁移脚本
- 基础 Repository 类（CRUD，无业务校验）
- 种子数据脚本（10 个测试 SKU + 3 个供应商 + 1 个客户）

**验收**：
- 迁移可执行
- 种子数据可导入
- 基础 CRUD 单元测试通过（80%+ 覆盖）
- 不影响现有任何模块

---

#### PR-2: 数据源抽象层

**目标**：让所有 AI 数据访问走统一接口。

**包含**：
- `DataSource` Protocol + `QuerySpec` / `QueryResult` 模型
- `RelationalDataSource`（PostgreSQL，支持任意 SQL）
- `FileDataSource`（CSV / Excel / JSON）
- `MarketplaceDataSource`（Amazon SP-API / Shopify / TikTok Shop 的统一封装）
- `KnowledgeDataSource`（pgvector / ChromaDB）
- 注册中心（DataSourceRegistry）
- 元数据管理（健康检查 / 限流 / 重试）

**验收**：
- 5 个数据源类型可注册
- 健康检查端点可用
- 限流 / 重试在抽象层统一
- 现有 SQL Agent 仍能工作（通过 RelationalDataSource 走 Postgres）

---

#### PR-3: 企业知识库 + RAG 引擎

**目标**：知识库可上传、检索、版本管理。

**包含**：
- KnowledgeDoc / KnowledgeChunk schema
- 文档导入工具（支持 PDF / Word / Markdown / HTML）
- 文档分块（按章节 + token count）
- 向量化（Embedding 服务）
- Retrieval Service（粗排 + 精排）
- 元数据过滤（按 category / channel / validity）

**验收**：
- 上传 1 篇 Amazon SOP 文档，可成功检索
- 版本控制可用（上传 v2 后检索返回 v2）
- 失效日期过滤生效
- RAG 质量测试集（50 问 50 答）

---

#### PR-4: Inventory 域

**目标**：多仓库存 + 流水 + 调拨 + 健康度。

**包含**：
- InventoryLevel / InventoryTransaction / InventoryHealth schema
- 仓库 CRUD（含类型：DOMESTIC / FBA / 3PL / TRANSIT）
- 库存事务（append-only 流水）
- 跨仓调拨流程
- 健康度计算（每日定时任务）
- 库存预警（低于安全库存报警）

**验收**：
- 跨 3 个仓库调拨完整流程跑通
- 健康度计算正确
- 预警在 Slack / 邮件触达
- 流水与快照对账

---

#### PR-5: Order 域

**目标**：跨平台订单统一 + 状态机。

**包含**：
- Order / OrderItem / OrderEvent schema
- 订单状态机（`PENDING → PAID → ALLOCATED → PICKING → SHIPPED → DELIVERED`）
- Amazon / Shopify / TikTok Shop 订单接入
- 库存分配逻辑（优先 FBA）
- 拆单 / 合单
- 退款 / 取消流程

**验收**：
- 真实 Amazon 测试订单可拉取并落库
- 状态机转换正确（可逆 + 不可逆）
- 拆单 1 个订单 → 2 个 Shipment 跑通

---

#### PR-6: Logistics 域

**目标**：头程 + 尾程 + 轨迹追踪 + 退货。

**包含**：
- FreightBooking / Shipment / TrackingEvent / ReturnAuthorization / CustomsDeclaration schema
- 头程流程（订舱 → 起运 → 到港 → 清关 → 入库）
- 尾程流程（打单 → 出库 → 揽收 → 运输 → 派送 → 签收）
- 物流商 API 接入（顺丰国际 / DHL / FedEx / UPS）
- 退货授权 + 检测 + 重新入库

**验收**：
- 真实货代 API 接入跑通
- TrackingEvent 自动更新
- 退货流程完整跑通

---

#### PR-7: Advertising 域

**目标**：跨平台广告投放 + 同步 + 归因。

**包含**：
- AdAccount / Campaign / AdGroup / Ad / Keyword / SpendRecord / PerformanceMetric schema
- Amazon Ads / Google Ads / Meta Ads / TikTok Ads 同步
- 每日花费 / 展示 / 点击 / 转化 落库
- 归因窗口（点击 / 观看）
- 关键指标计算（ACoS / TACoS / ROAS）

**验收**：
- 4 个平台 API 接入跑通
- 归因数据准确
- 关键指标与平台报告对账

---

#### PR-8: SQL Agent 增强

**目标**：跨域 SQL 查询 + 报表自动生成。

**包含**：
- SQL Agent 接入 PR-2 的 RelationalDataSource
- 跨多表查询（join 跨域）
- 自然语言 → SQL 增强（schema 感知）
- 自动生成 Markdown / 表格结果
- 异常 SQL 重试 + 修复

**验收**：
- 50 个自然语言查询全部生成正确 SQL
- 错误率 < 5%
- 跨域查询（Order + Customer + Product）跑通

---

#### PR-9: Report Agent 增强

**目标**：报表模板 + 调度 + Dashboard + 订阅。

**包含**：
- ReportDefinition / ReportExecution schema
- 模板（日报 / 周报 / 月报）
- 调度（cron）
- Dashboard（widget 配置）
- 订阅（邮件 / Slack）

**验收**：
- 10 个内置报表可运行
- Dashboard 可视化
- 订阅触发

---

#### PR-10: Listing Agent（首个业务 Agent）

**目标**：商品 Listing 自动生成 + 多语言。

**包含**：
- Listing Agent 实现（基于多 Agent 框架）
- 标题公式（Amazon 字符限制 + 关键词布局）
- 五点描述模板
- A+ 内容生成
- 多语言翻译（中 → 英 / 德 / 日）
- 关键词建议

**验收**：
- 100 个 SKU 自动生成 Listing
- 多语言翻译准确率 > 90%
- 关键词建议被运营采纳率 > 30%

---

#### PR-11: Customer Service Agent

**目标**：客服 FAQ 自动回复 + 订单查询 + 工单升级。

**包含**：
- Customer Service Agent 实现
- FAQ 检索（接入 RAG）
- 订单状态查询（接入 Order 域）
- 情绪检测 + 升级人工
- 多语言支持

**验收**：
- 70% 客服对话可自动回复
- 复杂问题升级准确
- 平均响应时间 < 3 秒

---

#### PR-12: Ads Agent

**目标**：广告报告 + 关键词建议 + 创意文案。

**包含**：
- Ads Agent 实现
- 每日广告报告
- 关键词建议（基于销量 + 竞品）
- 创意文案生成
- 异常告警（ACoS 突增等）

**验收**：
- 每日报告自动生成
- 关键词建议有效（采纳率 > 20%）
- 创意文案被使用

---

### 7.5 风险与缓解

| 风险 | 缓解 |
|---|---|
| 领域模型不完整，PR-1 后频繁改表 | Phase 2 之前充分调研 + 业务方确认；用 Alembic 迁移可平滑演进 |
| 数据源接入成本高 | 优先接入 1-2 个核心平台；其他平台后续按需 |
| AI 准确率不达预期 | 人工 review 闭环 + A/B 框架 + 持续优化 |
| 跨域数据量大，SQL 性能差 | 数据仓库分层 + 预聚合表 |
| 业务流程变更频繁 | 状态机抽象 + 配置化（而非硬编码） |

---

## 8. 后续 Phase 规划

| Phase | 内容 | 依赖 |
|---|---|---|
| **Phase 1（本阶段）** | 业务领域模型 | — |
| **Phase 2** | 数据库设计（SQL schema 详细化） | Phase 1 确认 |
| **Phase 3** | Master Data API + 数据源适配器 | Phase 2 |
| **Phase 4** | 核心 Agent 框架增强 | Phase 1 现有 LangGraph |
| **Phase 5** | 业务 Agent 渐进实现 | Phase 3-4 |
| **Phase 6** | 性能优化 + 监控 + 安全 | 全阶段并行 |

---

## 9. 等待确认

请确认以下问题，确认后进入 Phase 2（数据库设计）：

1. **9 个领域划分** 是否完整？是否需要新增 / 拆分 / 合并？
2. **核心实体 + 关系** 是否准确？特别是：
   - Product / SKU / Listing 三层关系
   - Order 统一模型 + channel_order_id 双轨
   - Logistics 头程 / 尾程分离
   - Customer 按 channel 分离（identity resolution 留 Phase 2+）
3. **知识库分类**（8 类）是否合理？分类粒度需要更细或更粗？
4. **数据源抽象**（5 大类）是否完整？有没有遗漏的数据源类型？
5. **AI 能力映射** 的 20 个业务场景是否覆盖核心？有没有遗漏？
6. **PR 路线**（12 步，~17 周）是否合理？优先级是否需要调整？
7. **公司画像**（Meridian，150 人，30-50M USD 营收）是否反映真实业务？如果不一致请告知。

---

**等待业务方确认后，进入 Phase 2：详细 SQL schema 设计（pgvector / 索引 / 约束 / 迁移）。**

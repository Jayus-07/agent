# Data Collection Center

> 企业级数据采集与治理平台。Pipeline 编排采集/解析/清洗/分析/写入全链路，策略模式支持多数据源热插拔，LangGraph Tool 接入 Multi-Agent 工作流。

## 1. 总览

```
data_collection/
├── pipeline.py               # 主编排器 Fetcher→Parser→Cleaner→Analyzer→Writer
├── scheduler.py              # 统一调度入口 (register + run_now)
├── skill.py / tool.py        # LangGraph 集成 (BaseSkill + @tool)
├── visualizer.py             # matplotlib 图表自动生成
├── config.py / models.py     # 模块配置 + SQLAlchemy ORM
│
├── fetchers/                 # 数据获取层
│   ├── base.py               # AbstractFetcher + RawData
│   ├── static_fetcher.py     # 读本地 datasets/*.json
│   └── http_fetcher.py       # requests.get() HTTP 采集
│
├── parsers/                  # 数据解析层
│   ├── base.py               # AbstractParser + ParsedData
│   ├── json_parser.py        # JSON 解析 + 嵌套展平 + schema 类型强制
│   └── csv_parser.py         # CSV 解析 (pandas)
│
├── cleaners/                 # 数据清洗层
│   ├── base.py               # AbstractCleaner + CleanedData
│   └── default_cleaner.py    # 去重 + 类型转换 + 缺失值填充
│
├── analyzers/                # 数据分析层
│   ├── base.py               # AbstractAnalyzer + AnalyzedData
│   └── stats_analyzer.py     # describe + groupby + 缺失值诊断
│
├── writers/                  # 数据写入层
│   ├── base.py               # AbstractWriter + WriteResult
│   └── sqlalchemy_writer.py  # SQLAlchemy Engine 统一写入 (PG/MySQL)
│
├── mock_api/server.py        # FastAPI Mock 数据源 (端口 8001)
├── datasets/                 # 5 个中文电商 JSON (57 条记录)
│   ├── products.json         # 12 条商品
│   ├── orders.json           # 15 条订单
│   ├── shops.json            # 8 条店铺
│   ├── inventory.json        # 12 条库存
│   └── suppliers.json        # 10 条供应商
│
├── demo_data_collection.py   # 一键演示：5 数据集全链路采集+分析
└── demo_e2e_agent.py         # Agent 全链路：采集→交叉分析→洞察→集成地图
```

**设计原则**：

- 采集是确定的 ETL 流水线，用 Pipeline 而非 Agent（不需要 LLM 推理）
- 策略模式：Fetcher / Parser / Writer 每层独立接口，新增数据源不改其他层
- 每阶段产出强类型 dataclass，阶段间零耦合，失败立即返回
- 同步接口 + `asyncio.to_thread`：兼容 `BaseSkill.execute()` 的异步调用链
- 与 LangGraph 集成遵循项目 BaseSkill / ToolRegistry 契约，零侵入

## 2. Pipeline 流程

```
Scheduler.run_now("采集商品数据")
         │
         ▼
CollectionPipeline.run(source, table, dedup_keys, analysis_config)
         │
         ├─ ① Fetcher.fetch(source)  ─────────────────→  RawData
         │     ├── StaticDataFetcher   datasets/*.json
         │     └── HttpFetcher         requests.get(url)
         │
         ├─ ② Parser.parse(raw)  ────────────────────→  ParsedData
         │     ├── JsonParser           json.loads + 嵌套展平 + schema 类型强制
         │     └── CsvParser            pd.read_csv
         │
         ├─ ③ Cleaner.clean(records)  ───────────────→  CleanedData
         │     └── DefaultCleaner       去重(drop_duplicates) + 类型转换(pd.to_numeric) + 缺失值填充(fillna)
         │
         ├─ ④ Analyzer.analyze(cleaned)  ────────────→  AnalyzedData
         │     └── StatsAnalyzer         describe + groupby + 缺失值诊断
         │
         └─ ⑤ Writer.write(records, table)  ─────────→  WriteResult
               └── SQLAlchemyWriter      pandas.to_sql / upsert

         ─────────────────────────────────────────────→  CollectResult
                                                         .to_markdown()
```

**失败语义**：每个阶段包在 `try/except` 中，失败立即返回 `CollectResult(status="failed", error="[stage] ...")`。只有 Analyze 步骤失败非致命（降级为无分析数据继续执行）。

**空数据语义**：`parsed.records` 为空数组视为正常（"今日无新数据"），返回 `status="success"` 而非 `"failed"`。

## 3. 数据类型（6 个强类型 dataclass）

| 类型 | 产出方 | 核心字段 |
|------|--------|---------|
| `RawData` | Fetcher | `source`, `format` (json/csv/html), `content` (str), `metadata` (dict) |
| `ParsedData` | Parser | `records` (list[dict]), `record_count`, `parse_errors` |
| `CleanedData` | Cleaner | `records`, `row_count`, `dedup_removed`, `null_filled`, `type_converted` |
| `AnalyzedData` | Analyzer | `records` (透传), `summary` (describe), `aggregations` (groupby), `missing_report` |
| `WriteResult` | Writer | `table`, `inserted`, `skipped`, `errors`, `elapsed_ms` |
| `CollectResult` | Pipeline | 以上 5 种的并集 + `task_id`, `status`, `elapsed_ms`, `error`, `.to_markdown()` |

**设计意图**：阶段间传递强类型对象而非裸 dict，每个阶段的产出可独立测试、独立 mock。下游（Reporter/SQL Agent）按需取用任意阶段的数据。

## 4. 各层详解

### 4.1 Fetcher 层

```
AbstractFetcher
├── StaticDataFetcher    读 datasets/ 目录 JSON/CSV 文件
└── HttpFetcher          requests.Session GET 请求（含超时重试 + Session 复用）
    └── Phase 2: SeleniumFetcher  浏览器自动化采集
```

**source 标识**：
- `static://datasets/products.json` → StaticDataFetcher 自动解析路径
- `http://localhost:8001/mock/products` → HttpFetcher GET 请求
- 简写 `"products"` → 自动展开为 `static://datasets/products.json`

**HttpFetcher 重试策略**：最多 2 次重试，指数退避 (1.5^attempt)，区分 Timeout / RequestException。

### 4.2 Parser 层

```
AbstractParser
├── JsonParser    json.loads → 嵌套展平 → schema 类型强制/字段重命名
└── CsvParser     pd.read_csv → 列名 strip → NaN→None
```

**JsonParser.schema 双用途**：
- **类型映射**：`{"售价": float, "数量": int}` → 解析时强制转换，失败记录到 `parse_errors`
- **字段映射**：`{"old_name": "new_name"}` → 重命名后输出

**嵌套展平**：`{"parent": {"child": "val"}}` → `{"parent_child": "val"}`，列表值序列化为 JSON 字符串。

### 4.3 Cleaner 层

**DefaultCleaner 三步清洗**：

| 步骤 | 方法 | 配置项 |
|------|------|--------|
| ① 去重 | `df.drop_duplicates(subset=dedup_keys)` | `rules["dedup_keys"]`，无则全字段去重 |
| ② 类型转换 | `pd.to_numeric(col, errors="coerce")` | `rules["type_map"]`，默认映射中文电商字段 |
| ③ 缺失值填充 | `df.fillna(median)` (数值) / `fillna("未知")` (分类) | `rules["fill_values"]` 可指定自定义值 |

**默认类型映射**（中文）：售价/成本/金额/单价/不良率→float，数量/库存量/安全库存/预留量/商品数/交期天数/起订量→int。

### 4.4 Analyzer 层

**StatsAnalyzer 三步分析**：

| 步骤 | 输出 | 存储位置 |
|------|------|---------|
| ① describe | 数值字段的均值/标准差/四分位数 | `AnalyzedData.summary` |
| ② groupby | 按指定维度的 count+sum 聚合 | `AnalyzedData.aggregations` (含 `_count` 后缀的计数版) |
| ③ 缺失值诊断 | 字段缺失率 + 填补策略建议 | `AnalyzedData.missing_report` |

**自动分组维度**：根据 `dataset_name` 自动选择（如 products→品类/平台/状态，orders→渠道/地区/状态）。

**填补策略**：缺失率 > 50% → 建议删除；数值字段 → 中位数填充；分类字段 → "未知"填充。

### 4.5 Writer 层

**SQLAlchemyWriter** — Engine 抽象，传 DATABASE_URL 自动适配 PostgreSQL / MySQL。

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `append` | `df.to_sql(if_exists="append")` | 持续追加 |
| `replace` | `DELETE FROM` + `to_sql` | 全量替换 |
| `upsert` | PG: `INSERT ON CONFLICT DO NOTHING` / MySQL 退化为 append | 增量去重写入 |

**异常安全性**：写入失败时 `WriteResult.inserted = 0`，而非错误地返回 `len(records)`（P0 修复）。

**分批写入**：`_batch_size = 500`，大数据集自动分片。

## 5. LangGraph 集成

### 5.1 注册链

```
ToolRegistry.CAPABILITY_MAP["data.collect"] = "data_collection_skill"
        ↓
Skill Registry: DataCollectionSkill().capabilities = ["data.collect"]
        ↓
BaseSkill.execute(state, step_capability="data.collect")
        ↓
data_collection_tool.invoke(params)
        ↓
CollectionPipeline.run(source, table, ...)
```

### 5.2 循环导入修复

**问题**：`data_collection.skill → multi_agent.skills.base → ... → registry → data_collection.skill` 形成循环。

**修复**：`skills/registry.py` 内置 Skill 直接加载，外部 Skill 通过 `_register_external_skills()` 惰性加载（首次调用 `get()` 时触发）。

### 5.3 Planner 调度示例

```
用户输入:  "采集最新商品数据并分析库存预警"
           ↓
Planner:   Step 1 ── data.collect  "采集 products + inventory 数据"
           Step 2 ── sql.query     "关联 stg_products 和 stg_inventory 查预警项"
           Step 3 ── report.generate "生成库存健康报告"
           ↓
Supervisor: 依次调度 DataCollectionSkill → SQLSkill → ReportSkill
```

## 6. 可视化 (`visualizer.py`)

`DataVisualizer` 从 `AnalyzedData.aggregations` 自动提取分组数据渲染图表。

- **≤ 6 个分类** → 饼图 (带图例 + 百分比)
- **7+ 个分类** → 柱状图 (带数值标签)
- **数值字段均值** → 水平柱状图
- 中文字体支持 (Microsoft YaHei / SimHei 自动探测)
- 配色与 `report_agent/chart_generator.py` 保持一致
- 图表保存到 `data_collection/charts/`

```bash
./.venv/Scripts/python.exe data_collection/demo_data_collection.py --chart
# → charts/ 目录生成 19 张 PNG
```

## 7. Mock API

独立的 FastAPI 应用 (`mock_api/server.py`)，模拟第三方电商平台 API。

```
端口 8001
GET /mock/products           → 12 条 (支持 ?category=电子产品)
GET /mock/orders             → 15 条 (支持 ?channel=Amazon)
GET /mock/shops              → 8 条
GET /mock/inventory          → 12 条 (支持 ?status=偏低)
GET /mock/suppliers          → 10 条
GET /mock/health             → 状态 + 数据集概览
GET /mock/datasets           → 所有可用数据集及字段名
```

**设计目的**：验证 HttpFetcher → JsonParser → Cleaner → Analyzer 的完整 HTTP 链路。后续替换真实 API 时只需改 source URL。

## 8. 业务数据集

5 套中文电商数据集，共 57 条记录，模拟一家跨境品牌商（TechGleam/EcoLiving/PetPal/BabyJoy/OutdoorPro）在 Amazon/Shopify/eBay 多平台运营场景。

| 数据集 | 记录 | 关联键 | 典型分析场景 |
|--------|------|--------|-------------|
| **商品** | 12 | `sku` | 品类毛利分析、平台定价对比 |
| **订单** | 15 | `sku` → 商品 | 渠道营收排名、地区分布、订单状态占比 |
| **店铺** | 8 | 独立 | 平台店铺数量、评分健康度 |
| **库存** | 12 | `sku` → 商品 | 断货预警、安全库存缺口、仓库分布 |
| **供应商** | 10 | `品牌` → 商品 | 质量排名、交期对比、品类覆盖度 |

**数据外键**：`orders.sku → products.sku`, `inventory.sku → products.sku`, `suppliers.品牌 → products.品牌`。

## 9. 配置

所有配置通过环境变量覆盖，默认值在 `data_collection/config.py`。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DC_DEFAULT_FETCHER` | `static` | 默认采集器类型 |
| `DC_HTTP_TIMEOUT` | `30` | HTTP 请求超时 (秒) |
| `DC_MOCK_API_PORT` | `8001` | Mock API 监听端口 |
| `DC_DATABASE_URL` | `postgresql://.../demo` | 数据库连接 |
| `DC_BATCH_SIZE` | `500` | 批量写入行数 |
| `DC_ANALYSIS_ENABLED` | `true` | 是否执行分析 |

## 10. 演示脚本

### `demo_data_collection.py` — 一键采集

```bash
# 全部 5 数据集采集+分析
./.venv/Scripts/python.exe data_collection/demo_data_collection.py

# 含 matplotlib 图表
./.venv/Scripts/python.exe data_collection/demo_data_collection.py --chart

# HttpFetcher 模式 (需先启 Mock API)
./.venv/Scripts/python.exe -m data_collection.mock_api.server &
./.venv/Scripts/python.exe data_collection/demo_data_collection.py --http
```

输出：每数据集一条进度线 + 解析/清洗/分组统计 + 最终汇总表。

### `demo_e2e_agent.py` — Agent 全链路演示（面试核心入口）

```bash
./.venv/Scripts/python.exe data_collection/demo_e2e_agent.py
./.venv/Scripts/python.exe data_collection/demo_e2e_agent.py --chart
```

四 Phase 推进：
1. **数据采集** — Pipeline 采集 5 数据集，输出进度
2. **交叉分析** — Pandas merge/groupby/pivot：销售全景、库存预警、供应商评估、店铺健康度
3. **业务洞察** — 自动生成 🔴🟡🟢 三级优先级行动建议
4. **集成地图** — ASCII 架构图展示 DCC→SQL Agent→Reporter→LangGraph 对接点 + Planner 示例对话

## 11. 测试

```bash
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -m pytest tests/test_data_collection.py -v
```

27 条测试，覆盖 Fetcher (4) / Parser (4) / Cleaner (3) / Analyzer (3) / Pipeline (4) / Scheduler (3) / Tool (3) / Skill (3)。

## 12. 扩展路线

| 阶段 | 能力 | 方式 |
|------|------|------|
| **Phase 2** | SeleniumFetcher 浏览器采集 | 新增 `fetchers/selenium_fetcher.py`，实现 AbstractFetcher |
| **Phase 2** | MySQL 兼容验证 | SQLAlchemyWriter 已支持，需测试 `mysql+pymysql://` URL |
| **Phase 2** | APScheduler 定时调度 | `scheduler.py` 预留 `start()/stop()` 接口 |
| **Phase 3** | 模糊去重 (fuzzywuzzy) | 新增 `cleaners/fuzzy_dedup.py` |
| **Phase 3** | MergeAnalyzer 多表关联分析 | 新增 `analyzers/merge_analyzer.py` |
| **Phase 3** | 采集质量 Prometheus 监控 | 接入 `utils/prometheus_metrics.py` |

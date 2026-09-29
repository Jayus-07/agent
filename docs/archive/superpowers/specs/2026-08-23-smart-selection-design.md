# 智能选品与竞品分析功能设计

> 日期：2026-08-23
> 状态：已评审（完整性 / 技术准确性 / 前后端接口一致性核验通过）
> 承接文档：[方案 3 / 方案 4 落地实施计划对比报告](../plans/2026-08-21-recommendation-and-competitor-analysis-implementation-plan.md)（本设计为"方案 4：Agent 竞品分析系统"的具体工程落地）

---

## 1. 背景与目标

现有能力：

- `backend/competitor/`：抓取 → LLM 抽取 → SQLite append-only 快照（price / rating / review_count / highlights / promo_text / in_stock），watchlist、防封闸门、扫码登录
- `backend/rag/`：BM25 + 向量 + RRF + Rerank 检索管线
- `backend/business_report/`：DataFetcher 注册表 + 模板引擎 + 图表生成 + LLM 润色（含事实锁定校验）
- `backend/data_collection/`：Fetcher → Parser → Cleaner → Analyzer → Writer 五阶段流水线
- 前端 `/competitors` 页：监控列表、价格趋势弹窗、分析查询 Tab

目标（已与需求方确认的三个决策）：

1. **数据源两阶段**：先基于现有 watchlist 快照做潜力评估与对比，再新增品类榜单采集扩充候选池
2. **算法形态**：规则加权打分 + LLM 增强（推荐理由 / 风险提示，数字经事实锁定校验）
3. **前端形态**：新建 `/selection` 选品页 + 扩展 `/competitors` 页对比能力

---

## 2. 总体架构

新增 `backend/selection/` 模块作为选品引擎：

```
竞品快照 (competitor_snapshots) ──┐
榜单采集候选池 (Phase 2) ─────────┤→ scoring.py 规则打分 ─┐
                                 │                       ├→ recommender.py ─→ 推荐结果 JSON
卖点/文案文本 → market_index.py ──→│  Chroma 独立 collection ─→ 语义趋势 ─┘
                                 └→ trends.py SQL 聚合 ────→ 结构趋势
```

| 单元 | 职责 | 依赖 |
|---|---|---|
| `scoring.py` | 纯函数加权评分（无 I/O，可单测） | 快照字段、权重配置 |
| `market_index.py` | 快照 → 文本文档 → ChromaDB 独立 collection；语义趋势检索 | langchain_chroma、BGE embedding |
| `trends.py` | 快照 SQL 聚合（价格分位时序、评价增速、卖点词频） | CompetitorStore |
| `recommender.py` | 编排：候选 → 打分 → 趋势 → LLM 理由 → 组装 | 上述三者 + `backend.infra.llm` |
| `store.py` | SelectionStore（SQLite `data/selection.db`），缓存评分与权重 | sqlite3 |

对话入口：后续通过轻量 Skill 调用同一引擎（不在本期范围）。

---

## 3. 数据模型

### 3.1 新增 SQLite 存储 `data/selection.db`（SelectionStore，镜像 CompetitorStore 模式）

```sql
-- 评分结果缓存（快照无更新时直接读缓存）
CREATE TABLE selection_scores (
    url         TEXT PRIMARY KEY,
    score_json  TEXT NOT NULL,      -- {"total": 82.5, "breakdown": {...}, "notes": [...]}
    snapshot_id INTEGER,            -- 计算所依据的最新快照 id（缓存失效判断）
    computed_at TEXT NOT NULL
);

-- 评分权重配置（可调）
CREATE TABLE selection_weights (
    key        TEXT PRIMARY KEY,    -- reputation / heat / price / differentiation / stability
    value      REAL NOT NULL,
    updated_at TEXT NOT NULL
);

-- Phase 2：榜单采集候选池
CREATE TABLE product_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_task   TEXT,             -- data_collection task_id
    platform      TEXT,
    category      TEXT,
    keyword       TEXT,             -- 采集时的搜索词
    url           TEXT NOT NULL UNIQUE,
    title         TEXT, price REAL, rating REAL, review_count INTEGER,
    highlights    TEXT, rank_position INTEGER,
    first_seen_at TEXT, last_seen_at TEXT
);
```

### 3.2 Chroma collection `competitor_market`

**修正说明**：现有 `ChromaKnowledgeStore` 构造 `Chroma` 时未指定 `collection_name`，市场索引**不得**共用主知识库 persist 目录。由 `market_index.py` 自建独立实例：

```python
Chroma(collection_name="competitor_market",
       persist_directory="data/chroma_market",
       embedding_function=<复用全局 BGE embedding 实例，保证向量空间一致>)
```

- 文档文本：`{title}｜平台:{platform}｜价格:{price}{currency}｜卖点:{highlights}｜促销:{promo_text}`
- 文档 id：`snap-{snapshot_id}`（每快照一条，保留时序，与现有版本快照哲学一致）
- metadata：`url / platform / category / price_band(low|mid|high，按池内分位数分桶) / crawled_at / snapshot_id`
- 历史回填：一次性脚本 `backend/scripts/backfill_market_index.py`

---

## 4. 市场趋势分析（RAG + 竞品 pipeline）

### 4.1 索引挂钩子

`backend/competitor/pipeline.py` `analyze_url` 第 3 步快照入库成功后调用：

```python
market_index.index_snapshot(snap)   # try/except 包裹，失败仅记日志，不影响主流程
```

### 4.2 双轨趋势

| 轨道 | 实现 | 用途 |
|---|---|---|
| 结构趋势（精确数字） | `trends.py` 直接对 `competitor_snapshots` SQL 聚合：价格 p25/p50/p75 时序、Δ评价数/Δ天、卖点关键词词频 | 图表数据，数字必须来自数据库，不经 LLM |
| 语义趋势（定性归纳） | `market_index.search_trends(query, k, metadata_filter={"platform": ..., "crawled_at": {"$gte": ...}})` → LLM 归纳热卖卖点变化 / 促销节奏 / 关注点 | 趋势摘要文本 |

**修正说明**：语义检索不调用 `RAGSystem.retrieve()`（其绑定主知识库 collection），由 `market_index` 独立封装 `similarity_search_with_score`；同义词扩展可选复用 `backend/rag` 的查询扩展函数。

### 4.3 定期报告

在 `business_report/data_fetcher.py` `REPORT_REGISTRY` 注册 `market_trends` 类型。现有 source 以 `type: "sql"` 为主，需扩展一个自定义 source type（如 `type: "selection"`），由选品引擎直接产出 JSON 数据喂给 TemplateEngine；图表与 LLM 润色（含事实锁定）直接复用。

---

## 5. 产品潜力评估模型

### 5.1 规则打分（各维度归一化 0–100）

| 维度 | key | 计算方式 | 默认权重 |
|---|---|---|---|
| 口碑分 | `reputation` | rating 线性映射：`clip((rating-4.0)/0.8, 0, 1)*100`；无评分取中性 50 | 0.25 |
| 热度分 | `heat` | `0.7*量级分(log10(review_count+1) 池内归一) + 0.3*增速分(Δ评价数/Δ天 归一)` | 0.25 |
| 价格竞争力 | `price` | `0.5*折扣力度(1-price/original_price) + 0.5*池内价格分位反向`；语义为"跟随热销价位 + 促销力度"，非毛利视角（自家成本数据缺失） | 0.20 |
| 卖点差异度 | `differentiation` | Phase 1：卖点关键词与池内其他商品平均 Jaccard 重合率反向；Phase 2：BGE embedding 余弦 | 0.15 |
| 稳定性 | `stability` | `0.5*(1-价格变异系数) + 0.5*有货率(in_stock 快照占比)` | 0.15 |

`potential_score = Σ wᵢ · scoreᵢ`，输出分维度 breakdown（前端雷达图）。

**边界处理**（评分函数必须在单测覆盖）：

- 单品池（无同类对比对象）：differentiation / price 分位取中性 50，notes 标注 `single_item_pool`
- 快照数 < 2：stability 取中性 50，notes 标注 `insufficient_history`
- 字段缺失：该维度取中性 50，notes 标注 `data_insufficient`，LLM 理由须提示数据缺口
- 权重和 ≠ 1 时自动归一化

### 5.2 LLM 增强

打分 breakdown + 最新快照字段 → LLM 生成：① 推荐理由 ② 风险提示。
复用 `llm_polisher` 的**事实锁定模式**（提取数值 token → LLM 输出 → 逐条比对，不符即回退），防止编造数字。

### 5.3 缓存策略

评分写入 `selection_scores`；当 `store.latest_snapshot(url).id == score.snapshot_id` 时命中缓存，否则重算。`POST /selection/score` 强制重算。

---

## 6. 前端展示

### 6.1 新建 `/selection` 页（`frontend/src/app/selection/page.tsx`）

- **推荐列表**：表格 + 潜力分徽章、子分数 Recharts RadarChart 迷你图、LLM 推荐理由折叠展示、"加入监控"按钮（调现有 `POST /competitor/watchlist`）、数据新鲜度列（`latest_crawled_at`，应对防封限频导致的数据滞后）
- **品类趋势区**：价格 p25/p50/p75 面积图、卖点词频条形图、评价增速折线、语义趋势摘要卡片

### 6.2 扩展 `/competitors` 页

- 监控表格新增"潜力分"列（读 `/selection/score` 批量缓存接口）
- 多选勾选 → CompareModal 对比表格：价格/评分/评价数/促销/库存/卖点并排，差异单元格高亮

### 6.3 服务层与代理约定（一致性关键点）

- 新增 `frontend/src/services/selection.ts`，沿用 `import { request } from '@/lib/fetcher'` + 相对路径 `BASE = '/selection'`
- **禁止**使用绝对路径 `NEXT_PUBLIC_API_URL`——会绕过 `next.config.js` rewrite（`/api/:path*` → `localhost:8000/:path*`），此为此前竞品页踩过的坑
- 侧边导航新增"智能选品"入口

---

## 7. 后端 API 设计

新增 `backend/app/api/routes/selection.py`（`APIRouter(prefix="/selection", tags=["智能选品"])`），在 `backend/app/api/router.py` 加一行 `api_router.include_router(selection.router)`。

| 端点 | 方法 | 说明 |
|---|---|---|
| `/selection/recommendations?category=&platform=&limit=10&min_score=0` | GET | 推荐列表（潜力分降序 + LLM 理由） |
| `/competitor/recommendations` | GET | 别名端点（competitor 路由内定义，直接调选品引擎，不重复实现逻辑） |
| `/selection/trends?category=&days=30` | GET | 趋势聚合数据 |
| `/selection/score` | POST | 单品潜力评估（body `{url, force_refresh}`，强制重算并写缓存） |
| `/selection/scores/batch?urls=a&urls=b` | GET | 批量读评分缓存（供监控表潜力分列） |
| `/selection/compare?urls=a&urls=b&urls=c` | GET | 多品对比数据 |
| `/selection/weights` | GET / PUT | 读取 / 更新评分权重 |
| `/selection/report` | POST | 选品报告（business_report 管线，同步返回 markdown，与 `/competitor/scan` 行为一致） |
| `/selection/discover` *(Phase 2)* | POST | body `{keyword, platform, top_n}`，触发榜单采集任务 |
| `/selection/candidates` *(Phase 2)* | GET | 候选池列表 |

### 响应契约示例

`GET /selection/recommendations`：

```json
{
  "items": [{
    "url": "https://item.taobao.com/item.htm?id=...",
    "title": "...", "platform": "taobao",
    "latest_price": 129.0, "currency": "CNY",
    "rating": 4.8, "review_count": 12000,
    "score": {
      "total": 82.5,
      "breakdown": {"reputation": 90, "heat": 75, "price": 80, "differentiation": 70, "stability": 88},
      "notes": ["single_item_pool"]
    },
    "llm_reason": "...", "llm_risks": "...",
    "latest_crawled_at": "2026-08-23T08:00:00", "scored_at": "2026-08-23T10:00:00"
  }],
  "total": 10, "generated_at": "2026-08-23T10:00:00"
}
```

`GET /selection/trends`：

```json
{
  "category": "...", "days": 30,
  "price_quantiles": [{"date": "2026-08-20", "p25": 99.0, "p50": 129.0, "p75": 159.0}],
  "review_growth": [{"url": "...", "name": "...", "daily_delta": 34.5}],
  "highlight_freq": [{"keyword": "无线", "count": 12}],
  "semantic_summary": "LLM 生成的趋势摘要",
  "sources": {"snapshot_count": 120, "rag_hits": 15}
}
```

统一约定：列表响应均含 `{items, total, generated_at}`；错误走全局异常处理（404 资源不存在 / 422 参数校验 / 500 引擎异常）。

---

## 8. 需要补充的数据源与算法模型（Phase 2+ 路线图）

| 类别 | 内容 | 落地方式 |
|---|---|---|
| 数据源 | 品类榜单 / 搜索结果页 Top N | `data_collection` 流水线新增 `RankingFetcher` + 解析器，dedup 写入 `product_candidates` |
| 数据源 | 评价区文本（情感 / 差评点） | `extractor.py` 扩展抓取评论区，灌入 `competitor_market` collection |
| 数据源 | 自家销售 / 库存 | 现有 Postgres 业务库（SQLSkill 已有通路），支撑"对标自家 SKU"（watchlist 已有 `my_sku` 字段） |
| 数据源 | 行业报告 / 资讯 | 上传至 RAG 主知识库，趋势分析时引用 |
| 算法 | 销量代理预测 | 评价数增速作为销量代理 → 线性 / Prophet 时序外推 |
| 算法 | 竞争度 | 价格带密度 + 卖点重合度（HHI 思想），候选池建成后启用 |
| 算法 | 卖点聚类 | BGE embedding + KMeans，发现卖点主题簇 |

---

## 9. 实施步骤

**Phase 1（MVP）**

1. `backend/selection/` 骨架：`store.py`（SelectionStore）+ `scoring.py` + 权重默认值与单测
2. `market_index.py` + `analyze_url` 挂钩子 + 历史回填脚本
3. `trends.py` SQL 聚合
4. `recommender.py` + LLM 理由生成（事实锁定校验）
5. `routes/selection.py` + `/competitor/recommendations` 别名 + router.py 注册
6. 前端：`services/selection.ts`、`/selection` 页、competitors 页潜力分列与 CompareModal、导航入口
7. 测试与 E2E 验证

**Phase 2**

8. `RankingFetcher` + 榜单解析器 + `product_candidates` + discover/candidates 端点
9. 竞争度计算（价格带密度 + 卖点重合度）
10. `market_trends` 报告类型注册 + 定时选品报告
11. 销量代理预测 / 卖点聚类模型

---

## 10. 测试计划

遵循项目测试组织约定（镜像模块结构、`unittest.mock.patch` 隔离外部依赖、`TestClient` 验证路由契约）：

| 测试 | 位置 | 要点 |
|---|---|---|
| 评分单测 | `backend/tests/test_selection_scoring.py` | 参数化边界：字段缺失、单品池、快照不足、权重归一化、评分区间 [0,100] |
| 索引单测 | `backend/tests/test_market_index.py` | mock Chroma，验证文档文本 / metadata / id 格式 |
| 路由契约 | `backend/tests/api/test_selection_routes.py` | 最小 FastAPI app + TestClient，mock recommender，验证 422/404 与响应 schema |
| 事实锁定 | `backend/tests/test_selection_reason.py` | mock LLM 输出篡改数字 → 断言回退 |
| E2E | 浏览器验证 | 推荐列表 / 趋势图 / 对比表格 / 加入监控联动 |

---

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 防封闸门限制采集频率，推荐数据滞后 | 前端展示 `latest_crawled_at` 新鲜度列；推荐结果标注评分时间 |
| 冷启动快照少，评分失真 | 中性分 + notes 显式标注数据缺口；LLM 理由提示 |
| LLM 编造数字 | 事实锁定硬校验，不符即回退原始 breakdown |
| watchlist 样本量小，池内归一不稳定 | Phase 2 候选池扩充后自动改善；单品池走中性分 |
| SQLite → PG 迁移（Roadmap 既有缺口） | SelectionStore 与 CompetitorStore 同构，迁移时同步处理 |

---

## 12. 相关文件索引

- 竞品管线：`backend/competitor/pipeline.py`、`backend/competitor/store.py`
- 竞品路由：`backend/app/api/routes/competitor.py`
- 路由聚合：`backend/app/api/router.py`
- RAG 向量库：`backend/rag/vectorstore/knowledge_store.py`
- 报告管线：`backend/business_report/data_fetcher.py`、`llm_polisher.py`
- 采集流水线：`backend/data_collection/pipeline.py`
- 前端：`frontend/src/services/competitor.ts`、`frontend/src/app/competitors/page.tsx`、`frontend/next.config.js`

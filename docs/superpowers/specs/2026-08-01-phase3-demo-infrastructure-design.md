# Phase 3 — 演示基础设施设计 Spec

> 关联文档：[workflow-engine.md](../../architecture/workflow-engine.md) · [workflow-phase1.md](../../architecture/workflow-phase1.md)
> 分支：`feat/workflow-phase1`
> 状态：Phase 1+2 已完成（243 tests），Phase 3 待实施

---

## 1. 核心决策

### 1.1 不做独立 `/demo` 页

4 个场景映射到真实使用入口：

| 场景 | 真实入口 | 改动 |
|------|---------|------|
| 1. 经营日报 | `/reports` — 报告中心 | 新建 |
| 2. 库存预警 | `/alerts` — 告警中心 | 新建 |
| 3. 销量异常分析 | `/agent` — 已有 Chat | 不改 |
| 4. 商品优化建议 | `/agent` — 已有 Chat | 不改 |

### 1.2 业务域路由，动态类型

不按具体类型建子路由（`/reports/daily`、`/reports/weekly`），而是用 **Workflow Registry 动态驱动**：

```
/reports        ← 报告中心，?type=daily_report（从 Registry 动态拉取）
/alerts         ← 告警中心，?type=inventory_alert（从 Registry 动态拉取）
```

新增业务流 = 注册新 Workflow → 页面自动出现新 Tab/筛选项。不改前端代码。

### 1.3 Workflow Registry 作为类型元数据源

利用现有 `WorkflowRegistry.list_metas()` 返回的 `WorkflowMeta` 判断：

- `objects` 含 "日报"/"报告" 关键词 → 出现在 `/reports` Tab
- `objects` 含 "库存"/"预警"/"告警" 关键词 → 出现在 `/alerts` Tab

或更干净的方式：`WorkflowMeta` 增加一个可选 `category` 字段（`"report"` / `"alert"`），由 `@workflow` 装饰器声明。Registry 启动时自动分组。

---

## 2. `/reports` — 报告中心

### 2.1 布局

```
┌──────────────────────────────────────────────────┐
│  报告中心                                         │
├──────────────────────────────────────────────────┤
│  📊 今日 KPI 摘要行                                │
│  ┌──────────┬──────────┬──────────┬───────────┐  │
│  │ 商品总数  │ 异常库存  │ 日销售额  │ 活跃活动  │  │
│  │    10    │    3     │ ¥12,450  │    2     │  │
│  └──────────┴──────────┴──────────┴───────────┘  │
├──────────────────────────────────────────────────┤
│  Tab: 经营日报 | (周报) | (供应商报告) ...         │  ← 动态
├──────────────────────────────────────────────────┤
│  历史报告列表（日期 + 关键指标摘要 + 状态 + 操作）   │
│  ┌─────────────────────────────────────────────┐ │
│  │ 7/31 · 周三  │  ⚠ 2异常  │ ✅ success  │ → │ │
│  │ 7/30 · 周二  │  ✅ 正常  │ ✅ success  │ → │ │
│  │ 7/29 · 周一  │  ❌ 失败  │ ❌ failed  │ → │ │
│  └─────────────────────────────────────────────┘ │
│  [上一页] [下一页]                                │
└──────────────────────────────────────────────────┘
```

### 2.2 数据来源

**日报独立存储（方案 B）：**

在 `send_email` step 写邮件前，同时写一份到 `daily_reports` 表：

```sql
CREATE TABLE IF NOT EXISTS daily_reports (
    id              TEXT PRIMARY KEY,              -- = workflow run_id
    report_date     TEXT NOT NULL,                -- 日报日期
    status          TEXT DEFAULT 'success',       -- success / failed
    kpi_summary     TEXT,                         -- JSON: {total_products, alert_count, sales_amount, ...}
    report_content  TEXT NOT NULL,                -- 完整报告 Markdown/HTML
    trace_id        TEXT,                         -- 跳 Trace 详情
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(report_date DESC);
```

> 为什么不用 `workflow_runs` 直接读？`workflow_runs.outputs` 是 blob JSON，每次查都要反序列化整份 output，且缺乏 KPI 摘要字段。独立表支持高效列表查询。

### 2.3 列表页

- **KPI 摘要行**：取最新一条 `daily_reports` 的 `kpi_summary` 渲染
- **列表**：按 `report_date DESC` 倒序，分页（每页 20）
- **每行展示**：日期（星期）+ 关键指标摘要（异常商品数/状态标签）+ 状态徽章 + "查看"链接
- **状态徽章**：success 绿色 / failed 红色 / partial 黄色
- **筛选**：按月份快速跳转

### 2.4 详情页 `/reports/{report_id}`

混合布局（方案 C）：

```
┌──────────────────────────────────────────────────┐
│  ← 返回列表          经营日报 · 2026-07-31         │
├──────────────────┬───────────────────────────────┤
│  📊 业务视图     │  📐 技术视图（可折叠）          │
│                  │                               │
│  ## 销售摘要    │  ┌─ Step 链路 ──────────────┐  │
│  KPI 卡片...    │  │ fetch_sales   0.8s ✅     │  │
│                  │  │ fetch_inventory 0.6s ✅   │  │
│  ## 库存预警    │  │ rag_query      2.1s ✅    │  │
│  异常商品列表   │  │ agent_analyze  4.2s ✅    │  │
│                  │  │ generate_report 1.5s ✅   │  │
│  ## Agent 分析  │  │ send_email     0.3s ✅    │  │
│  分析段落...    │  └──────────────────────────┘  │
│                  │  [查看完整 Trace →]             │
│  ## 行动建议    │                               │
│  ...             │  RAG 引用：3 条（analytics KB） │
├──────────────────┴───────────────────────────────┤
│  生成时间: 2026-07-31 09:00:05 · 总耗时: 9.8s    │
└──────────────────────────────────────────────────┘
```

- **业务视图**（左/上）：渲染 `report_content`（Markdown → HTML，用 `react-markdown`）
- **技术视图**（右/下，默认折叠）：Step 列表（从 `workflow_runs` 读取 outputs 的 key + 耗时推断）+ "查看完整 Trace"链接（跳 `/observability/traces/{trace_id}`）+ RAG 引用
- 如果 status = failed，显示 error 信息

---

## 3. `/alerts` — 告警中心

### 3.1 布局

```
┌──────────────────────────────────────────────────┐
│  告警中心                                         │
├──────────────────────────────────────────────────┤
│  🚨 统计卡片行                                    │
│  ┌──────────┬──────────┬──────────┬───────────┐  │
│  │ 🔴 紧急  │ 🟡 警告  │ 🔵 提醒  │ ✅ 已解决 │  │
│  │    2     │    1     │    0     │    12    │  │
│  └──────────┴──────────┴──────────┴───────────┘  │
├──────────────────────────────────────────────────┤
│  [● 活跃告警] [○ 历史告警]                         │  ← 切换
│  过滤: [类型 ▾] [级别 ▾] [时间范围 ▾]               │
├──────────────────────────────────────────────────┤
│  告警列表                                         │
│  ┌──────────────────────────────────────────────┐│
│  │ 🔴 华为 Mate 60 Pro+  │ critical │ OPEN  │ → ││
│  │ 🔴 MAC Ruby Woo       │ critical │ OPEN  │ → ││
│  │ 🟡 iPhone 15 Pro      │ warning  │ ACK   │ → ││
│  └──────────────────────────────────────────────┘│
│  [上一页] [下一页]                                │
└──────────────────────────────────────────────────┘
```

### 3.2 数据来源

已有 `inventory_alert_cases` + `inventory_alert_events`（`backend/orchestration/inventory/store.py`）。

- **统计卡片**：`SELECT COUNT(*) FROM inventory_alert_cases WHERE status='open' GROUP BY current_level`
- **活跃告警列表**：`WHERE status IN ('open', 'acknowledged') ORDER BY CASE current_level WHEN 'critical' THEN 0 ... END`
- **历史告警列表**：`WHERE status IN ('resolved', 'closed') ORDER BY last_detected_at DESC`

### 3.3 列表页

- **默认视图**：活跃告警（status = open / acknowledged）
- **切换**：活跃告警 / 历史告警
- **过滤**：类型（库存预警 / 价格异常 … 从 Registry 动态）、级别（critical / warning / info）、时间范围
- **每行展示**：级别图标 + 商品名 + 当前状态徽章 + 库存量 + 可售天数 + 首次检测时间 + 操作
- **排序**：critical 在前，然后按检测时间倒序
- **刷新**：手动刷新按钮（不做实时推送 / SSE）

### 3.4 详情页 `/alerts/{case_id}`

混合布局（方案 C）：

```
┌──────────────────────────────────────────────────┐
│  ← 返回列表      Mate 60 Pro+ · 库存预警          │
├──────────────────┬───────────────────────────────┤
│  📊 业务分析     │  📋 工单操作区                  │
│                  │                               │
│  ┌─ 时间线 ────┐│  当前状态：🔴 CRITICAL          │
│  │ CREATE       ││  库存量：3 件                  │
│  │ ↓ (2h later)││  可售天数：0.4 天               │
│  │ UPGRADE      ││  日均销量：8 件                │
│  │ ↓            ││                               │
│  │ (now) 持续中 ││  [✓ 确认告警] [🔧 已解决]      │
│  └─────────────┘│  [🚫 忽略]                      │
│                  │                               │
│  ┌─ Agent 分析 ┐│  操作人：anonymous              │
│  │ 销售趋势图  ││  上次通知：2026-07-31 14:00     │
│  │ 补货建议    ││  RAG 引用：补货规则 §2.3        │
│  │ RAG 引用    ││                               │
│  └─────────────┘│                               │
├──────────────────┴───────────────────────────────┤
│  ┌─ 事件日志 ───────────────────────────────────┐ │
│  │ 2026-07-31 14:00 UPGRADE → CRITICAL         │ │
│  │ 2026-07-31 10:00 CREATE → WARNING           │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

- **时间线**（左上）：从 `inventory_alert_events` 查事件链，渲染垂直时间线
- **Agent 分析**（左下）：`workflow_runs` 中对应 inventory_alert workflow 的 `agent_analyze` step output
- **工单操作区**（右）：当前状态 + 库存量 + 可售天数 → 操作按钮（确认/已解决/忽略） → 调用已有的 `PATCH /api/alerts/{case_id}` 接口
- **事件日志**（底部）：完整事件列表（event_type + from→to state + reason + timestamp）
- "查看完整 Trace"链接 → `/observability/traces/{trace_id}`

---

## 4. 后端新增

### 4.1 `backend/demo/runner.py` — 演示编排器

不新建 `backend/demo/` 目录，直接在 `backend/seed/demo/` 扩展 `run.py`：

```
backend/seed/demo/
├── data.py           ✅ 已存在
├── run.py            ✅ 已存在（数据导入）
└── runner.py         新建（场景编排 + API）
```

或更干净的：

```
backend/seed/demo/
├── data.py           ✅
├── run.py            ✅ 数据导入 CLI
├── runner.py         新建：场景编排逻辑
└── api.py            新建：Demo API 端点
```

**runner.py 职责：**

```python
class DemoRunner:
    """编排 4 个 Demo 场景（不新建类，直接调已有 API/模块）"""

    async def seed_data(self) -> dict
        # 调 run.run_seed() 导入 demo 数据

    async def run_daily_report(self) -> dict
        # 1. 调 WorkflowScheduler.run_now("daily_report", inputs={})
        # 2. 读 daily_reports 表拿结果
        # 3. 返回 {run_id, trace_id, report_id, kpi_summary, ...}

    async def run_inventory_alert(self) -> dict
        # 1. 调 WorkflowScheduler.run_now("inventory_alert", inputs={})
        # 2. 读 inventory_alert_cases 表拿结果
        # 3. 返回 {run_id, trace_id, case_count, alerting_products, ...}

    async def run_scenario(self, scenario_id: str) -> dict
        # 分发到 run_xxx 方法
```

### 4.2 Demo API 端点（`backend/app/api/routes/demo.py` 或扩展现有 workflows API）

最低限度 2 个端点：

```python
@router.post("/demo/seed")
async def seed_demo_data():
    """导入 demo 数据 → 返回导入量"""

@router.post("/demo/run/{scenario_id}")
async def run_demo_scenario(scenario_id: str):
    """触发单个场景 → 返回 run_id + 结果跳转链接"""
    # scenario_id: daily_report | inventory_alert | sales_anomaly | product_optimization
    # 前两个调 workflow，后两个返回 /agent?prompt=... 的链接
```

> 不做 SSE 流式（YAGNI）—— workflow 执行是秒级，前端轮询或用 run_id 查结果即可。

### 4.3 `daily_reports` 存储（新建或扩展 persistence）

选项 A：独立 `backend/seed/demo/report_store.py`
选项 B：扩展 `backend/orchestration/workflow/persistence.py` 加 `daily_reports` 表

**推荐 B**——和 `workflow_runs` 同属持久化层，放一起。

`send_email` step 改造：发邮件前调用 `report_store.save_daily_report(...)`。

### 4.4 WorkflowMeta 增加 category 字段

```python
@dataclass
class WorkflowMeta:
    name: str
    description: str
    objects: list[str]
    actions: list[str]
    examples: list[str]
    default_kbs: list[str]
    category: str = ""  # 新增："report" / "alert" / ""（通用/chat）
```

`daily_report` → `category="report"`、`inventory_alert` → `category="alert"`。
前端 `/reports` 和 `/alerts` 用 `category` 过滤已有 workflows，动态生成 Tab 和筛选选项。

---

## 5. 前端新增

### 5.1 路由

```
frontend/src/app/
├── reports/
│   ├── page.tsx              报告中心主页（列表 + KPI + 动态 Tab）
│   └── [id]/
│       └── page.tsx           报告详情（混合布局）
├── alerts/
│   ├── page.tsx               告警中心主页（统计卡片 + 列表 + 过滤）
│   └── [id]/
│       └── page.tsx           告警详情（时间线 + Agent 分析 + 工单操作）
├── agent/
│   ├── page.tsx               ✅ 已有，不改
│   ├── tasks/
│   │   └── page.tsx           ✅ 已有
│   └── reports/
│       └── page.tsx           ✅ 已有
```

### 5.2 `/reports` 页面

**page.tsx（列表页）：**
- `'use client'`（useState / useEffect）
- 初始化：`GET /api/reports?type=daily_report` 拉列表
- KPI 摘要行：`GET /api/reports/latest?type=daily_report` 拉最新一条
- 列表渲染：`react-markdown` 不参与列表；KPI 摘要用简单卡片组件；每行只显示日期 + 指标摘要 + 状态徽章
- 动态 Tab：`GET /api/workflows` → 过滤 `category == "report"` 的 workflows → 渲染 Tab

**[id]/page.tsx（详情页）：**
- `'use client'`
- 拉 `GET /api/reports/{id}` → report_content + kpi_summary + trace_id
- 左/上：Markdown 渲染 `report_content`
- 右/下（折叠）：Step 列表 + Trace 链接 + RAG 引用
- "查看完整 Trace" → `router.push('/observability/traces/' + traceId)`

### 5.3 `/alerts` 页面

**page.tsx（列表页）：**
- `'use client'`（useState / useEffect）
- 统计卡片：`GET /api/alerts/stats` → critical/warning/info/resolved 计数
- 列表：`GET /api/alerts?status=open&level=&page=1`
- 默认活跃告警 Tab，"历史告警"切换：`status=resolved,closed`
- 级别图标：critical 🔴 / warning 🟡 / info 🔵
- 状态徽章：OPEN / ACK / RESOLVED / CLOSED
- 过滤条：类型下拉 + 级别下拉 + 时间范围
- 排序：critical 在前

**[id]/page.tsx（详情页）：**
- `'use client'`
- 数据来源：
  - `GET /api/alerts/{case_id}` → case 详情 + events
  - `GET /api/workflows/runs?workflow_name=inventory_alert` → 找最近相关 run → agent output
- 左侧：时间线组件（垂直 Timeline）+ Agent 分析面板
- 右侧：工单操作区（当前状态 + 库存量 + 可售天数 + 操作按钮）
- 操作按钮：调 `PATCH /api/alerts/{case_id}` → {status: "acknowledged"} / {status: "resolved", resolution_type: "MANUAL_RESOLVED"} / {status: "closed", resolution_type: "MANUAL_IGNORED"}
- 底部：完整事件日志表
- 加载状态 + Toast 错误处理

### 5.4 前端服务层

`frontend/src/services/reports.ts`（新建）：
```typescript
getReports(params) → {reports, total, page}
getLatestReport(type) → {report}
getReport(id) → {report}
```

`frontend/src/services/alerts.ts`（新建）：
```typescript
getAlertStats() → {critical, warning, info, resolved}
getAlerts(params) → {alerts, total, page}
getAlert(id) → {case, events}
patchAlert(id, body) → {updated_case}
```

---

## 6. 数据流

### 6.1 日报完整链路

```
用户点击 "生成日报"
  ↓
POST /api/workflows/daily_report/trigger
  ↓
Executor.run(DailyReport)
  ├─ fetch_sales → SQL
  ├─ fetch_inventory → SQL
  ├─ fetch_promotions → SQL
  ├─ rag_query_template → RAG (analytics KB)
  ├─ agent_analyze → InventoryAnalyzer Skill
  ├─ generate_report → Report Skill
  └─ send_email → Email Skill + 写 daily_reports 表  ← 新增
  ↓
WorkflowRunStore.save(ctx)  → workflow_runs 表（已有）
  ↓
GET /api/reports → 读 daily_reports + workflow_runs
```

### 6.2 告警完整链路

```
用户点击 "扫描库存预警"
  ↓
POST /api/workflows/inventory_alert/trigger
  ↓
Executor.run(InventoryAlert)
  ├─ scan_inventory + fetch_sales_history (并行)
  ├─ calculate_inventory_health → dynamic threshold eval
  ├─ evaluate_thresholds → state machine decisions
  ├─ alert_state_machine → upsert case + write events
  ├─ create_event + load_notification_policies (并行)
  └─ send_alert_email → plan() + call_email()
  ↓
GET /api/alerts → 读 inventory_alert_cases + events
```

---

## 7. Scope 边界

### 范围外（明确不做）

- ❌ 独立 `/demo` 页面
- ❌ SSE 实时进度流（workflow 秒级，不需要）
- ❌ `/agent` 页面改造（已可用）
- ❌ 销量异常分析独立页面（走 `/agent` chat）
- ❌ 商品优化建议独立页面（走 `/agent` chat）
- ❌ Slack / 钉钉 / 飞书通知
- ❌ RBAC 权限控制
- ❌ 实时推送 / WebSocket
- ❌ 可视化 DAG 编辑器

### 预估代码量

| 模块 | 代码量 |
|------|--------|
| 后端：daily_reports 存储 + `send_email` 改造 | ~100 行 |
| 后端：Demo API（seed + run 端点） | ~150 行 |
| 后端：WorkflowMeta.category 字段 | ~30 行 |
| 前端：`/reports` 列表 + 详情 | ~400 行 |
| 前端：`/alerts` 列表 + 详情 | ~500 行 |
| 前端：services 层 | ~100 行 |
| **合计** | **~1,280 行** |

---

## 8. 验证清单

- [ ] POST /api/demo/seed → 演示数据导入成功
- [ ] POST /api/demo/run/daily_report → workflow 跑完，daily_reports 写入了
- [ ] GET /api/reports → 列表返回（含 KPI 摘要行数据）
- [ ] GET /api/reports/{id} → 详情返回（含 trace_id）
- [ ] POST /api/demo/run/inventory_alert → workflow 跑完，cases + events 写入了
- [ ] GET /api/alerts/stats → 统计数字正确
- [ ] GET /api/alerts → 活跃告警列表
- [ ] PATCH /api/alerts/{case_id} → 确认/解决/忽略 状态变更
- [ ] 前端 `/reports` 页：KPI 摘要 + 列表 + 分页正常
- [ ] 前端 `/reports/{id}` 页：业务视图渲染 + Trace 跳转
- [ ] 前端 `/alerts` 页：统计卡片 + 活跃/历史切换 + 过滤
- [ ] 前端 `/alerts/{id}` 页：时间线 + 操作按钮
- [ ] npx tsc --noEmit 通过
- [ ] Workflow Registry list_metas 返回 category 正确
- [ ] 现有 243 tests 无回归

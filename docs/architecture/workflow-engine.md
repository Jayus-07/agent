# 企业智能运营平台 — Demo 计划

> 文档目的：把"确定性业务流程（Workflow）+ 智能决策（Agent）+ 企业知识（RAG）"三者
> 整合到本项目，以 **业务方演示** 为目标，覆盖 4 个企业级场景。
> 范围：Phase 1（日报 Workflow）+ Phase 2（库存预警 Workflow）+ Agent 场景（销量分析、商品优化）+ 演示基础设施。

## 核心定位

### RAG 不是独立模块，是「企业知识大脑」

```text
                用户
                  |
                  ↓
           Agent / Workflow
                  |
       +----------+----------+
       |                     |
       ↓                     ↓
    业务数据               企业知识
    SQL                    RAG
       |                     |
 销售/库存/订单        规则/经验/文档
       |                     |
       +----------+----------+
                  |
                  ↓
            Agent 推理
                  |
                  ↓
       建议 / 报告 / 自动动作
```

**RAG 参与 6 个环节**：Agent 决策 / Workflow 执行 / 数据分析解释 / 内容生成 / 业务规范约束 / Planner 思考。

**错误示范**（传统 RAG）：
```text
用户问问题 → 搜知识库 → 返回答案
```

**正确示范**（企业知识层）：
```text
Agent/Workflow 决策时 → RAG 注入规则与经验 → 决策符合企业规范
```

### Workflow vs Agent

| 维度 | Workflow | Agent |
|------|----------|-------|
| 管 | 确定性业务流程 | 不确定性决策 |
| 例 | 每天 9 点跑日报 | 分析销量下降原因 |
| 调 | Skills + RAG | SQL + RAG + Report |
| 触发 | APScheduler / API | 用户自然语言 |

---

## 决策对齐（2026-07-30）

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | Demo 目标受众 | **业务方**（运营、采购、客服、CEO） |
| 2 | 演示数据 | **真实品类名**（手机/服装/美妆，替代 SKU-001） |
| 3 | 邮件发送 | **真发 SMTP**（演示时配 mock SMTP 收件人 + 显示前端镜像） |
| 4 | 架构图 | **静态图**（见本文档"架构图"章节） |
| 5 | 前端入口 | **C**：/chat 主入口 + /workflows 卡片（点 = 生成自然语言进 chat） |
| 6 | Workflow DSL | **显式 depends_on**，Runtime 自动算并行 |
| 7 | Workflow Metadata | **强类型 Schema**（不是 dict，@workflow 装饰器声明） |
| 8 | Router 评分 | **0.3 Object + 0.2 Action + 0.5 WorkflowMatch** |
| 9 | Router 兜底 | 规则 confidence < 0.7 → LLM |
| 10 | 业务对象词典 | **由 Workflow Registry 自动生成**（不硬编码） |
| 11 | Capability 抽象 | **BaseCapability** → BaseSkill / BaseAgentSkill 双继承 |
| 12 | Workflow Step 类型 | **3 大类**：capability / function / control |
| 13 | Workflow 嵌套 | **第一版禁止**（只允许 Workflow → Capability → Skill） |
| 14 | LLM 调用 | 统一走 `infra/llm/proxy.py` |

> 详细实施计划见 [workflow-phase1.md](workflow-phase1.md)。

---

## 4 个 Demo 场景

### 场景 1：经营日报自动生成（Workflow）

- **触发**：APScheduler 每天 9:00（演示手动 trigger）
- **价值**：替代人工整理 1-2 小时
- **RAG 角色**：查「日报模板」+「上月同期对比维度」

### 场景 2：销量异常智能分析（Agent）

- **触发**：用户在 chat 输入"上周销量为什么下降？"
- **价值**：主动发现问题
- **RAG 角色**：查「运营规则文档」+「活动影响因素」+「季节性判断」

### 场景 3：库存风险预警（Workflow + Agent）

- **触发**：每天扫描 + 阈值命中
- **价值**：避免断货损失
- **RAG 角色**：查「补货规则」+「爆款判定标准」

### 场景 4：商品运营优化建议（Agent）

- **触发**：用户输入"如何优化销量下降的商品？"
- **价值**：替代数据分析师 30 分钟
- **RAG 角色**：查「商品标题规范」+「内容营销规则」+「平台限制」

---

## 架构图（静态）

```text
                       业务方 / 运营人员
                              |
                              ↓
                    ┌─────────────────┐
                    │  智能运营入口    │
                    │   /demo 页面    │
                    └────────┬────────┘
                             |
            ┌────────────────┼────────────────┐
            |                |                |
         Workflow         Agent            API
            |                |                |
    ┌───────┴───────┐   ┌────┴────┐   ┌───────┴──────┐
    |               |   |         |   |              |
  调度器          Step  Planner  Reporter  手动 Trigger
    |               |   |    |    |              |
    |               |   |    |    |              |
  APScheduler        |   |    |    |              |
    |               |   |    |    |              |
  daily_report  ──RAG──┼─RAG┼─SQL┼─Report     inventory_alert
  inventory_alert     |   |    |    |              |
                      ↓   ↓    ↓    ↓              ↓
              ┌─────────────────────────────────────┐
              │   企业知识大脑（4 库）             │
              │   + 业务数据库（PostgreSQL）       │
              └─────────────────────────────────────┘
```

---

## RAG 知识库分层（4 库）

```
backend/data/docs/
├── product/                    # 商品知识库（kb_id=product）
│   ├── 商品资料/                  # 真实品类：手机参数、服装尺码表
│   ├── 卖点库/                    # 各品类爆款卖点提炼
│   └── 标题规范/                  # 商品标题撰写规范
│
├── operations/                  # 运营知识库（kb_id=operations）
│   ├── 爆品方法论/                # 爆款判定标准
│   ├── 活动规则/                  # 618/双11 等大促规则
│   ├── 季节性判断/                # 服装/美妆的季节性经验
│   └── 异常处理经验/              # 销量下降排查方法论
│
├── policies/                    # 企业制度库（kb_id=policies）
│   ├── 售后规则/                  # 发货/退换货政策
│   ├── 审核规则/                  # 商品发布规范
│   ├── 补货规则/                  # 库存管理 + 爆款补货阈值
│   └── 权限规范/                  # 操作权限矩阵
│
└── analytics/                   # 数据分析知识库（kb_id=analytics）
    ├── 指标定义/                  # GMV/转化率/客单价定义
    ├── 分析方法/                  # 同比/环比/异常检测
    └── 日报模板/                  # 经营日报章节结构
```

### RAG 调用模式

| 场景 | RAG 角色 | 查询示例 |
|------|---------|---------|
| 销量异常分析 | **规则注入** | "销量下降的可能原因" "季节性影响" |
| 商品运营优化 | **规范约束** | "标题规范" "禁用词" |
| 库存预警 | **决策支持** | "补货规则" "爆款判定" |
| 经营日报 | **模板引导** | "日报章节结构" "对比维度" |
| 商品审核 | **合规校验** | "发布规范" "广告法" |

---

## Phase 1 — Workflow 引擎 + 日报（Week 1，~600 行）

### 改动清单

```
backend/
├── data_collection/scheduler.py        升级：接入 APScheduler
├── orchestration/
│   ├── workflow/                       新建
│   │   ├── engine.py                   Step / Workflow
│   │   ├── context.py                  WorkflowContext
│   │   ├── decorator.py                @workflow / @step
│   │   └── runner.py                   workflow_runs 持久化
│   └── workflows/
│       ├── daily_report.py             ← 含 RAG Step
│       └── inventory_alert.py          (Phase 2)
├── rag/
│   └── workflows/                      新建：Workflow 用 RAG 检索
│       └── workflow_rag.py             按 kb_id 检索企业知识
└── app/api/routes/workflows.py        新建
```

### daily_report.py（含 RAG）

```text
fetch_sales ─┐
             ├─→ rag_query_template ─→ agent_analyze
fetch_inventory ─┘                          │
                                              ↓
                                       generate_report
                                              ↓
                                       send_email
```

- **fetch_sales / fetch_inventory**：SQL Skill
- **rag_query_template**：查「日报模板」+「对比维度」（kb_id=analytics）
- **agent_analyze**：Planner 节点，结合 SQL 数据 + RAG 模板
- **generate_report**：Report Skill
- **send_email**：真发 SMTP（演示时配 mock 收件人）

### 关键 API

```python
class Step:
    def __init__(self, name, fn, *,
                 on_error="abort",       # abort / skip / agent_degrade
                 timeout_sec=60, retries=0,
                 parallel_with_prev=False,
                 when=None): ...

class Workflow:
    def add(self, step) -> "Workflow": ...
    async def run(self, inputs=None) -> WorkflowContext: ...

# scheduler.py 升级
class Scheduler:
    def register_workflow(self, wf): ...
    def register_daily(self, name, hour, minute=0): ...
    def register_interval(self, name, seconds): ...
    async def run_now(self, name, inputs=None): ...
```

---

## Phase 2 — 库存预警 + 告警 Dashboard（Week 2，~900 行）

### 改动清单

```
backend/
├── data/
│   ├── inventory_thresholds.db        新建
│   ├── inventory_alerts.db            新建
├── orchestration/workflows/
│   └── inventory_alert.py             含 RAG Step（查补货规则）
├── rag/preprocessing/threshold_engine.py
└── app/api/routes/inventory_alerts.py

frontend/src/app/workflows/inventory/
├── page.tsx                           阈值规则 + 告警列表
└── alerts/[id]/page.tsx               告警详情（含 RAG 规则引用）
```

### inventory_alert.py（含 RAG）

```text
scan_inventory → evaluate_thresholds → dedup
                                          ↓
                              agent_analyze ←─┘
                              ├─ sql: 销售历史
                              ├─ rag: 补货规则（kb_id=policies）
                              └─ planner: 综合判断
                                          ↓
                              persist_alerts
                                          ↓
                              send_email
```

---

## Phase 3 — 演示基础设施（Week 3，~500 行）

### 改动清单

```
backend/
├── seed/
│   └── demo_data.py                    演示种子数据（真实品类名）
└── demo/
    └── runner.py                       一键演示 4 个场景

frontend/src/app/demo/
├── page.tsx                            演示主页
└── [scenario]/
    └── page.tsx                        单场景详情
```

### seed/demo_data.py（真实品类）

```python
# 商品：手机/服装/美妆/家电（不用 SKU-001）
products = [
    {"name": "iPhone 15 Pro 256G", "category": "手机", "price": 8999,
     "supplier_grade": "A", "current_qty": 3},   # ← 触发预警
    {"name": "华为 Mate 60 Pro", "category": "手机", "price": 6999,
     "supplier_grade": "A", "current_qty": 45},
    {"name": "优衣库摇粒绒外套", "category": "服装", "price": 299,
     "supplier_grade": "B", "current_qty": 200},
    # ... 50 个商品
]

# 销售历史：90 天数据（含趋势 + 异常）
sales_history = [
    {"product": "iPhone 15 Pro", "date": "2026-07-01", "qty": 35},
    # ... 跨 90 天
]

# 知识库种子文档
seed_kb("product/标题规范/iPhone标题规范.md", """
iPhone 类商品标题规范：
1. 品牌 + 型号 + 存储 + 颜色
2. 禁止："最便宜""全网最低"等极限词
3. 推荐：突出 A15 芯片、ProMotion 等卖点
""")
```

### 演示主页（`/demo`）

```
┌─ 电商智能运营平台 Demo ───────────────────┐
│  🎬 一键演示（约 3 分钟）                  │
│  [▶ 开始演示]                              │
│  ─────────────────────────────────────    │
│  4 个企业级场景：                           │
│  1️⃣ 9:00  经营日报自动生成（Workflow）    │
│  2️⃣ 10:00 销量异常智能分析（Agent）       │
│  3️⃣ 14:00 库存风险预警（Workflow+Agent）  │
│  4️⃣ 16:00 商品运营优化建议（Agent）       │
└──────────────────────────────────────────┘
```

### 邮件：真发 SMTP + 前端镜像

```python
# backend/config/email.py
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.mock.local")  # 演示用 mock SMTP
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
DEMO_RECIPIENTS = ["ops@demo.local", "ceo@demo.local"]

# 前端 /demo/emails/[id] 显示邮件镜像（不依赖 SMTP 收信）
```

---

## 演示故事线（4 幕剧）

```text
[第一幕] 9:00 经营日报自动跑
  触发：手动 trigger（演示时）
  流程：扫销售+库存 → RAG 查日报模板 → Agent 找异常 → 报告 → 邮件
  展示：邮件样例 + Trace DAG

[第二幕] 10:00 运营问"上周销量为什么下降？"
  触发：chat 输入自然语言
  流程：Planner 先 RAG 查运营规则 → SQL 查销售 → 综合分析
  展示：Planner DAG 可视化 + RAG 引用 + RAG 规则文档

[第三幕] 14:00 库存预警触发
  触发：手动改某商品库存为 3（阈值 10）
  流程：扫库存 → 阈值评估 → Agent 查销售历史 + RAG 补货规则 → 邮件采购
  展示：邮件含 Agent 分析 + RAG 规则引用 + Trace

[第四幕] 16:00 运营问"如何优化销量下降的商品？"
  触发：chat 输入自然语言
  流程：Planner 先 RAG 查标题规范 + 活动规则 → SQL 数据 → Report 优化建议
  展示：多 Skill 协作 + RAG 规范约束
```

---

## 工作量与时间线

| Week | Phase | 任务 | 代码量 |
|------|-------|------|--------|
| 1 | P1 | Workflow 引擎 + 日报（含 RAG） | ~600 行 |
| 2 | P2 | 库存预警（含 RAG） + 告警 dashboard | ~900 行 |
| 3 | P3 | 演示数据 + Demo 脚本 + 邮件镜像 | ~500 行 |
| 4 | P4 | 打磨：演示数据可视化 + 录制脚本 | ~400 行 |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| APScheduler 与 asyncio 冲突 | 用 `AsyncIOScheduler` |
| 真发 SMTP 失败影响演示 | 默认 mock SMTP（演示时显示前端镜像） |
| Step 超时 trace span 残留 | `asyncio.wait_for` + try/finally |
| Agent 降级烧 token | 复用 `infra/llm/proxy.py` 频率限制 |
| 演示数据无真实感 | 用真实品类名 + 90 天连续数据 |
| RAG 检索慢影响演示 | 知识库种子数据预热 + 检索缓存 |

---

## Commit 拆分

| # | Commit | Phase |
|---|--------|-------|
| 1 | `feat(workflow): Workflow 引擎骨架 + WorkflowContext` | P1-1 |
| 2 | `feat(workflow): APScheduler 接入 + 持久化` | P1-2 |
| 3 | `feat(workflow): 日报 Workflow（含 RAG） + API` | P1-3 |
| 4 | `feat(workflow): 阈值规则引擎 + 库存告警表` | P2-1 |
| 5 | `feat(workflow): 库存预警 Workflow（含 RAG） + Dashboard` | P2-2 |
| 6 | `feat(demo): 演示种子数据 + /demo 主页 + 一键演示` | P3 |

---

## 范围外（明确不做）

- ❌ Temporal / Camunda 集成
- ❌ 可视化 DAG 编辑器
- ❌ 商品上新 Workflow（需新建商品模块）
- ❌ 短视频内容流水线（需新建内容生成模块）
- ❌ 真实邮箱 SMTP（演示用 mock）

---

## 参考

- 现有 Skill 列表：[backend/orchestration/skills/](backend/orchestration/skills/)
- 调度器 Phase 1 MVP：[backend/data_collection/scheduler.py](backend/data_collection/scheduler.py)
- Trace 系统：[backend/rag/tracer.py](backend/rag/tracer.py)
- RAG 入库：[backend/rag/indexing/indexer.py](backend/rag/indexing/indexer.py)
- 知识库管理：[docs/architecture/rag-system.md](rag-system.md)
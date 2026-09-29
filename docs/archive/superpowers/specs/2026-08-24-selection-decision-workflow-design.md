# 选品决策工作流改造可行性方案

> 日期：2026-08-24
> 状态：可行性评估完成，待实施排期
> 目标：将「用户输入选品需求 → 感知/分析/决策/验证/执行五层多 Agent 流程 → Go/No-Go 决策包」的目标流程落到现有系统

---

## 1. 背景与目标

目标流程（用户提供的流程图）包含五层：

1. **感知层**：数据采集 Agent 集群（市场体量/增长率/季节性、搜索趋势与供需比、品类集中度）
2. **分析层**：竞品分析 Agent 集群（TOP 竞品识别、五维数据抓取、差评 NLP 痛点、六维雷达图）
3. **决策层**：差异化分析 Agent（痛点热力图、需求缺口、Decision1）+ 财务测算 Agent（成本拆解、利润模型、风险缓冲金、Decision2，含优化→重算循环）
4. **验证层**：7 人 AI 评审团独立投票（Decision3）
5. **执行层**：竞品监控 Agent（定时抓取、智能调价、库存预警、异常告警）

本方案结论：**可以落地**。现有双层编排体系（Router → Planner → Supervisor → Skills 的 LangGraph 主图 + 确定性 Workflow 框架）天然适合承载该流程；但约一半能力目前缺失，且受"仅免费抓取"数据源约束，感知层须带降级方案。

## 2. 已确认的约束（来自需求澄清）

| 决策点 | 结论 |
|---|---|
| 本次范围 | 先产出可行性方案文档，不写实施代码 |
| 数据源 | 仅免费抓取（不接付费数据 API） |
| 目标平台 | 京东、淘宝、亚马逊，重点防封 |
| 财务测算 | 用户填参数 + 规则计算（数字可溯源，符合事实锁定原则） |
| 评审团 | 多角色独立评审投票，人数可配置（默认 7） |
| 入口形态 | 表单任务页：提交 → 异步执行 → 任务列表查看 → 决策报告页 |
| 架构载体 | 新增 `selection_decision` Workflow，DAG 分层并行 + 条件分支 + 有限循环，复用现有调度/持久化/告警/trace 设施 |

## 3. 现状盘点（现有资产 vs 目标流程）

| 图中能力 | 现状 | 对应代码 |
|---|---|---|
| 主管 Agent 任务拆解 | ✅ 已有 | `agents/planner/planner.py`、`orchestration/supervisor/scheduler.py`（Capability DAG） |
| 竞品抓取 pipeline | ✅ 已有（watchlist 手动模式） | `competitor/pipeline.py`、`competitor/store.py`、`competitor/adapters.py` |
| 防封体系 | ✅ 完整 | `competitor/anti_ban.py`（robots 闸门/限流熔断/拟人参数/风控反馈一票停） |
| 选品打分与推荐 | ✅ Phase 1 已实现 | `selection/scoring.py`（五维）、`selection/recommender.py`、`/selection` 页面 |
| 市场趋势语义检索 | ✅ 已有 | `selection/market_index.py`（独立 Chroma 实例 `data/chroma_market`） |
| 持续监控/库存预警/告警 | ✅ 已有 | `orchestration/workflows/inventory_alert.py`、`orchestration/inventory/notification.py` |
| 报告生成 | ✅ 已有 | `agents/reporter/reporter.py`、`skills/report/skill.py`、`business_report/` |
| 确定性 Workflow 框架 | ✅ 已有（DAG 分层并行 + retry/timeout/on_error + trace + 持久化） | `orchestration/workflow/`（dag.py/executor.py/persistence.py/scheduler.py） |
| 市场宏观数据（体量/增长率/季节性/供需比） | ❌ 无数据源 | — |
| TOP 竞品自动识别（榜单爬取） | ❌ 未实现（Phase 2 规划项） | — |
| 评论/差评文本抓取与 NLP | ❌ 仅有评价数量抽取 | `competitor/adapters.py` 只抽数量 |
| 差异化分析 / 财务测算 / 评审团 / 决策包 | ❌ 不存在 | — |
| 智能调价引擎 | ❌ 不存在 | — |
| 流量维度、供应链维度 | ❌ 免费渠道不可得 | — |

**关键技术限制**：现有 `workflow/executor.py` 是纯 DAG 一次执行（Kahn 分层 → 逐层 gather），不支持条件分支与循环。目标流程的 Decision1/2/3 分支与财务优化循环需要小的框架扩展（见 §4.3）。

## 4. 总体架构设计

### 4.1 载体选型

新增确定性 Workflow `selection_decision`（`backend/orchestration/workflows/selection_decision.py`），表单任务页触发、异步执行。

对比过的备选方案及否决原因：

- **对话驱动（Planner 动态编排）**：入口是表单页而非对话；多阶段分支/循环靠动态拆解难以精确控制，稳定性风险高。否决。
- **独立 LangGraph 子图**：决策环表达力最强，但会重复建设进度/持久化/调度设施，违背"确定性流程走 Workflow"的现有架构约定。否决。

### 4.2 DAG 步骤映射（5 层）

```
Layer 0 感知层（并行）
├── market_scan      市场扫描：品类榜单/搜索下拉词/价格带分布（免费抓取，新增，Phase 2 接入）
└── competitor_data  竞品数据：复用 watchlist + 快照库（现有）

Layer 1 分析层（并行）
├── competitor_profile  五维聚合 + 雷达图数据（现有快照字段，流量/供应链降级）
├── review_pain         差评抓取 + NLP 痛点聚类（新增，三级降级，Phase 2 实证）
└── market_assess       Q1「要不要做」：价格带/集中度/供需代理指标（新增）

Layer 2 决策层（串行 + 条件分支）
├── differentiation     痛点热力图 + 需求缺口 → Decision1（新增）
└── finance_model       用户参数 + 规则测算，内部有界优化循环 ≤3 轮（新增）

Layer 3 验证层
└── review_panel        N 角色独立打分投票 → Decision3（新增，LLM×N）

Layer 4 产出
└── decision_report     Go/No-Go 决策包 Markdown + 落库 + 任务页展示（复用 reporter）
```

- **执行层不进入本 Workflow**：Go 决策后把目标商品加入 watchlist，即接入现有竞品定时扫描 + `inventory_alert` + notification 告警闭环；Phase 3 增加自动订阅联动。
- **主管 Agent 对应关系**：表单任务页的「提交」替代了图中 Supervisor 的任务拆解职责——确定性流程无需 Planner 动态拆解；对话入口（若未来需要）可由 Planner 路由到本 Workflow。

### 4.3 分支与循环的承载方式

- **Decision1/2/3 条件分支**：`StepConfig` 新增可选 `run_if` 谓词（对 `ctx.outputs` 求值）。executor 在调度前求值，不满足则标记 `skipped` 并在 trace span 记录跳过原因。改动集中在 `workflow/meta.py` + `workflow/executor.py`（约 20 行），不影响现有 `daily_report`/`inventory_alert`。
- **财务优化循环**（Decision2 否 → 优化 → 重算）：在 `finance_model` step 内部实现有界循环（≤3 轮：不达标 → 生成调价/换供应链建议 → 重算），超限输出 No-Go + 差距分析。不在 DAG 层画环，避免 CycleDetectedError。

## 5. 各层能力缺口与可行性评估

| 图中能力 | 可行性 | 关键结论 |
|---|---|---|
| 市场体量/增长率/季节性 | 🟡 中低 | 免费可抓：京东/亚马逊榜单页、搜索下拉词；体量类数据拿不到 → **降级为代理指标**（评价增速、同价位候选数、价格带分布），报告显式标注数据缺口 |
| 供需比/搜索趋势 | 🟡 中低 | 用下拉词覆盖率 + 评价增速近似；不接付费 API 不承诺精确值 |
| TOP 竞品自动识别 | 🟡 中 | 榜单爬取自动补候选；受登录态限制，Phase 1 保留手动 watchlist 兜底 |
| 竞品五维数据 | 🟡 中 | 价格/评分/评价数/卖点已有；**流量**用评价增速代理；**供应链**用户填写或 Phase 2 接 1688 |
| 差评 NLP 痛点 | 🔴 高风险 | 京东评论接口有风控、淘宝需登录、亚马逊评论页可抓。三级降级见 §6 风险 R3 |
| 六维雷达图 | 🟢 高 | 现有 `score_product` 五维直接画雷达图，维度名对齐即可 |
| 差异化分析 Agent | 🟢 高 | 纯 LLM 推理 + 规则门控，无外部数据依赖 |
| 财务测算 Agent | 🟢 高 | 用户填参数 + 规则公式；数字全程可溯源（延续 recommender 的事实锁定校验模式） |
| N 人 AI 评审团 | 🟢 高 | LLM×N 独立调用 + 投票聚合；人数可配置（默认 7）；并行调用控制耗时 |
| Go/No-Go 决策包 | 🟢 高 | 复用 reporter/report_skill 生成 Markdown，落库 + 任务页渲染 |
| 竞品监控/库存预警 | 🟢 高 | 直接复用，零新增 |
| 智能调价引擎 | 🔴 高风险 | 涉及真实经营动作；Phase 1 只做**异常告警 + 调价建议**，不做自动执行 |
| 防封 | 🟢 高 | 所有新增抓取（榜单/评论）必须过 anti_ban 闸门；淘宝风险最高，排最后接入 |

**总体结论**：决策层（差异化/财务/评审团）纯推理 + 规则，可行性最高，先做；感知层宏观数据与差评抓取受免费数据源限制，必须带降级方案，放 Phase 2。

## 6. 风险清单与对策

| # | 风险 | 等级 | 对策 |
|---|---|---|---|
| R1 | 淘宝强反爬 + QR 登录，抓取失败率高，风控可能连累同环境账号 | 🔴 | 淘宝最后接入；独立浏览器指纹配置；全部走 anti_ban 闸门；熔断即停并如实汇报跳过项 |
| R2 | 免费渠道拿不到真实市场体量/销量，Q1 结论置信度有限 | 🔴 | 全部用代理指标 + 报告显式标注「数据缺口」与置信度等级，不做无依据外推（延续事实锁定原则） |
| R3 | 评论抓取受风控限制，差评 NLP 可能无米下锅 | 🟡 | 三级降级：① 抓到评论 → NLP 实证；② 抓不到 → LLM 基于低评分率/卖点推断并标注「推断」；③ 都失败 → 跳过该环节，Decision1 仅用已有数据 |
| R4 | 评审团 LLM×N 成本与耗时放大，评审同质化 | 🟡 | 人数可配置（3/5/7，默认 7）；每角色独立 System Prompt + temperature 差异化 + 并行调用；投票规则（多数通过/一票否决项）可配置 |
| R5 | 财务优化循环不收敛 | 🟡 | 有界循环 ≤3 轮，超限直接输出 No-Go + 差距分析 |
| R6 | 流程长（抓取 + 多次 LLM），耗时 10 分钟级 | 🟡 | 异步任务 + 轮询/SSE 进度（复用 workflow run store + trace spans）；抓取失败不阻塞决策层（on_error=skip + 降级） |
| R7 | `run_if` 是对编排框架的侵入式修改 | 🟢 | 改动约 20 行，配套单测；不触碰现有 workflow 行为 |

## 7. 分阶段路线图

### Phase 1 —— 决策闭环 MVP（无新数据源）

1. Workflow 框架扩展：`StepConfig.run_if` 条件跳过 + trace 记录
2. `selection_decision` Workflow 骨架：复用现有 watchlist 快照数据
3. 决策层：`differentiation`（LLM 痛点/缺口推断，标注推断属性）+ `finance_model`（用户参数 + 规则测算 + ≤3 轮优化）
4. 验证层：`review_panel` N 角色投票
5. 产出：Go/No-Go 决策包 Markdown 落库
6. 前端：新增「选品决策」表单任务页（品类关键词/平台勾选/财务参数/评审人数）+ 任务列表 + 决策报告页

### Phase 2 —— 感知层增强（新数据源）

1. 榜单爬取适配器（京东 → 亚马逊 → 淘宝顺序，各自过防封评估）
2. 搜索下拉词采集 → 需求热度代理指标
3. `market_scan` / `market_assess` step 接入，Q1 从「纯推断」升级为「半实证」
4. 评论抓取（亚马逊优先，公开页面）→ 差评 NLP 实证链路

### Phase 3 —— 执行层联动

1. Go 决策自动加入 watchlist + 生成监控订阅
2. 价格异常波动告警 → 调价**建议**推送（复用 notification，不做自动调价）
3. 决策报告历史对比与复盘页

## 8. 入口与接口设计要点

- **前端**：新增 `/selection-decision` 页面（Sidebar 加入口）。表单提交 → `POST /api/selection-decision/tasks` 返回 task_id → 任务列表页轮询/SSE 展示各层进度（进度来自 workflow run store + trace spans）→ 完成后查看决策报告页。服务层使用相对路径，依赖 Next.js rewrite 代理（项目既有约定，禁止 NEXT_PUBLIC_API_URL 绝对路径）。
- **后端 API**：新增 `backend/app/api/routes/selection_decision.py`（提交任务 / 任务列表 / 任务详情 + 报告）；异步执行复用 workflow 调度机制。
- **数据落库**：决策包存 SQLite（selection_decision 表：输入参数 / 各层输出 / verdict / 报告 Markdown / trace_id）。
- **复用清单（零改造）**：anti_ban 闸门、competitor store、score_product、market_index、reporter、notification、workflow persistence/trace。

## 9. 不在本方案范围内（YAGNI）

- 付费数据 API 接入（卖家精灵/魔镜/5118 等）
- 自动调价执行（只做建议）
- 供应链成本自动抓取（1688）
- 对话式触发入口（未来可由 Planner 路由接入，非本期）

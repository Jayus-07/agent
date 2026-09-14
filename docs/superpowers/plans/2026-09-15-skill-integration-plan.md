# Skill 集成实施计划：智能选品与竞品分析扩展

> 日期：2026-09-15
> 状态：v2 — 已按评审意见修订（证据管线 / 评估决策职责拆分 / decision_log 数据模型 / 证据分型 / CloakBrowser 风控清单），待执行
> 来源：SkillHub 六个 skill 拆包评审结论（本会话完成，包体已安全审查）

---

## 0. 进度看板（每批次完成后更新本表）

| 批次 | 内容 | 状态 | 完成日期 | 备注 |
|---|---|---|---|---|
| 1 | Agently Mail 升级 EmailSkill | ✅ 基本完成 | 2026-09-15 | 已提交（d3f9f19）；CLI 已装 + OAuth 已通（mint1614@agent.qq.com）；真实搜索经包装层验证 ok:true。发送侧验收并入首个业务场景调用 |
| 2 | market_research Workflow（证据管线） | 🔄 代码完成 | 2026-09-15 | 五段式 DAG 7 step 已提交（e9dc554）；全量回归 0 failed（05:28，含批次1+2）；剩真实联网端到端验收 |
| 3 | 评估职责拆分 + decision_log | 🔄 代码完成 | 2026-09-15 | market_evidence_assess + selection_decision_gate 拆分、insufficient 硬门控、decision_log 十字段+不可变触发器；定向 21 + 关联面 422 passed。⚠️ evaluation/golden 因阿里云欠费（Arrearage）环境性失败，待充值后复验 |
| 4 | CPR 吸收 + 证据分型 + golden 评测 | ⬜ 未开始 | — | |
| 5 | 数据源欠账（榜单/评论/自家数据） | ⬜ 未开始 | — | |
| 6 | cloak 引擎开关 + 主动通知 | ⬜ 未开始 | — | 先过 CloakBrowser 风控清单 |

图例：⬜ 未开始 / 🔄 进行中 / ✅ 完成（记测试通过日期）/ ⏸ 暂停（记原因）

---

## 1. 背景与 skill 评审结论

对 SkillHub 六个 skill 拆包审查后的采纳决定：

| Skill | 采纳决定 | 理由 |
|---|---|---|
| `@user_99a25c17/dazhiruoyu`（市场调研报告生成器） | **采纳** → market_research Workflow | 12 章节品类调研框架 + reportlab PDF；补智能选品的品类级上游 |
| `@user_0de90603/competitive-product-research`（CPR 竞品调研） | **采纳（吸收）** | 八维体验对标 + SWOT/五力/PESTLE + SRC-xxx 证据溯源纪律；补竞品分析的分析层 |
| `@tencent-adm/agently-mail` | **采纳** → EmailSkill 升级 | QQ 邮箱 Agent 邮箱 CLI（agently-cli），OAuth，两阶段确认，能力远超现有 SMTP 单向发送 |
| `@user_7495c5ed/browser-automation-toolbox` | **拆块吸收** | 只要 CloakBrowser 引擎（源码级反检测 Chromium，Playwright drop-in）+ 平台经验文档；**不接 orchestrator**（与 anti_ban 熔断哲学冲突）；**禁用 browser-act 云模式**（数据外发风险） |
| `@clawhub_steipete/weather` | **放弃** | 项目已有腾讯 LBS 天气，wttr.in 国内不稳定 |
| playwright-scraper / playwright-cli | **放弃** | 相对 crawler_runtime 是降级；仅顺手吸收 `--disable-blink-features=AutomationControlled` 启动参数，`playwright codegen` 用作开发工具 |

### 功能归属（不新增功能名，扩两个既有域）

- **智能选品域**：market_research Workflow（品类级调研）→ 归入 selection 家族，注册 `category="selection"`，前端挂 `/selection` 族
- **竞品分析域**：CPR 对标框架 + cloak 引擎 → 增强 `backend/competitor/` 与 `competitor_analysis`
- **横切支撑**：Agently Mail（报告递送 / 收件监听 / 告警通道）

---

## 2. 分阶段实施

### 批次 1：Agently Mail 升级 EmailSkill（最先，半天量级）

- `backend/skills/email/`：新增 capabilities `email.search` / `email.read` / `email.watch`；`email.send` 加引擎开关（SMTP | agently-cli）
- 两阶段确认映射：CLI 返回 `ctk_xxx` → Skill 层返回 `pending_confirmation` 状态 → 前端二次确认后带 token 重放；**图节点内禁止自确认**
- 注册链五件套：`skills/registry.py::_instances` import → 包 `__init__.py` 调 `tool_registry.register_skill_node` → `orchestration/router/capabilities.yaml` 加条目 → `vector_router.ROUTE_EXAMPLES` 补示例 → `test_registry_consistency` 通过
- 依赖：服务器侧 `npm install -g @tencent-qqmail/agently-cli` + OAuth（agent.qq.com）；不进本地沙箱
- 验收：TestClient 契约测试；真实邮箱收发一次

### 批次 2：market_research Workflow（核心价值；v2 修订：证据管线优先，12 章节只是报告模板）

**五段式流水线**（不是逐章四层 DAG，12 章节不每章独立调 LLM）：

```
1. 采集公开数据          复用 WebSearchSkill / web_crawl；每条证据落 evidence 表
2. 清洗、去重、证据标准化  evidence_schema: {evidence_id, source_type(官方/媒体/UGC),
                          url, fetched_at, published_at, raw_text 摘录, numbers[], title}
3. 生成章节分析任务       按证据密度分组（不按章节 1:1）：
                          组A 市场规模+竞争格局 / 组B 用户需求+商业模式 / 组C 其余章节按需合并；
                          章任务只允许引用 evidence_id，禁止凭空引用
4. 统一事实锁定+引用校验  每条结论：数字 ⊆ 所引 evidence.numbers；无证据支撑的结论
                          降级为"推断"或剔除；参照 recommender 的"输出⊆输入"校验但升级为证据级
5. Markdown / PDF 报告    12 章节模板；每章尾 Source Index；含计算过程（如 TAM 公式与输入）
```

**已明确的设计决策**（评审问题逐条回答）：

- **章节并行与依赖**：组 A/B/C 三组并行，组间无依赖；"进入建议"章是唯一汇合点，必须等全部组完成
- **采集失败语义**：有效证据 < 最低阈值（初定 5 条）→ abort（fail-fast，不产残缺报告）；仅个别来源失败 → 降级继续但报告标注覆盖缺口
- **LLM 失败语义**：章任务重试 2 次（指数退避）→ 仍失败降级为"该章节 = 模板骨架 + 原始证据列表"，**不中止整体报告**；只有采集阶段失败才 abort
- **数字/日期/结论的证据绑定**：数字 → evidence_id + 原文摘录 + **计算过程**；日期 → 来源发布时间（published_at，非采集时间）；结论 → {结论类型, 证据列表, 置信度}（见批次 4 的统一结构）
- **⚠️ 核心认知**："输出数字必须来自输入"≠"数字真实"——事实锁定只防编造、不防错误来源。evidence 表必须保留 raw_text 原文摘录 + 采集时间，Source Index 可回溯原始 URL；来源类型分级（官方 > 媒体 > UGC）参与置信度计算
- 依赖 reportlab/PyPDF2 进 pyproject；workflow 注册 `category="selection"`

### 批次 3：评估职责拆分 + decision_log（v2 修订：两段式，证据门控先于决策）

**`market_assess` 拆成两个节点**（防止 LLM 仅凭"内容完整但证据不足"的报告给出"推荐进入"）：

1. **`market_evidence_assess`（市场证据评估）**：只回答"证据够不够格"——数据量是否充分、来源可靠性、关键维度缺失、时效是否合格 → 输出 `evidence_verdict: sufficient | partial | insufficient`
2. **`selection_decision_gate`（选品决策）**：输入 = 证据评估结果 + 成本 + 竞争 + 风险 + 用户偏好 → 推荐 / 谨慎 / 不推荐。**硬门控：`evidence_verdict == insufficient` 时 run_if 谓词层禁止输出"推荐"**，只能产出"证据不足，无法决策"报告

**decision_log 数据模型（实施前定稿；快照不可变）**：

| 字段 | 用途 |
|---|---|
| decision_id | 决策唯一标识 |
| candidate_id | 对应选品候选 |
| decision_version | 决策版本（同 candidate 可多次决策） |
| evidence_snapshot | 当时使用的证据快照（evidence_id 列表 + 全文 JSON） |
| score_snapshot | 当时的评分与权重 |
| recommendation | 推荐 / 谨慎 / 不推荐 |
| user_decision | 用户最终拍板 |
| decision_at | 决策时间 |
| actual_metrics | 后续真实表现 |
| feedback_at | 实际表现回填时间 |

不可变性：`evidence_snapshot` / `score_snapshot` 写入后**禁止 UPDATE**（应用层只读封装 + SQLite 触发器双保险）；后续改权重、重抓数据、更新报告一律产生**新的 decision_version 行**，不覆盖历史决策依据。
另外：痛点 Top5 → watchlist 监控关键词建议（写回 competitor watchlist）。

### 批次 4：CPR 吸收 + 证据分型 + 决策质量评测（v2 修订：框架 ≠ 事实）

**核心原则：CPR 是分析框架，不是事实来源；框架完整 ≠ 结论可靠。** 所有报告结论（不止 CPR 章节，全平台统一）采用同一结构：

```
结论
 ├── 结论类型：事实 / 推断 / 建议
 ├── 证据引用：SRC-xxx（事实级必挂）
 ├── 证据时间：来源发布时间
 ├── 置信度 / 证据充分性：高 | 中 | 低（由来源分级 + 证据数量决定）
 └── 生成依据：所用框架/方法说明
```

按内容类型的证据要求区分：

- 竞品价格 / 功能 / 评价 → 可来自公开页面或评论证据（事实级）
- SWOT → 逐条区分"事实项（挂 SRC）"与"分析推断项（标注推断）"
- 五力 / PESTLE → 默认"推断"级，框架输出不自动升为事实
- 体验对标（八维）→ **先定评分标准**（每维度的评分定义 + 最低证据要求）再打分，防主观打分

落地：上述结构进 `prompts/defaults/` 资产（注入 `differentiation`、competitor_analyze、market_research 章节分析），recommender 理由生成同步加"结论挂来源"约束。

**LLM 节点 golden 评测集**：`market_evidence_assess` / `selection_decision_gate` / `differentiation` / `review_panel` 各 10-20 条（输入 → 期望 verdict/结论类型），接 `evaluation/` 目录，防 prompt 改动回归。

### 批次 5：数据源欠账（设计文档 Phase 2 补课）

- 榜单采集：`RankingFetcher` + 解析器 + `product_candidates` 表 + `/selection/discover`、`/selection/candidates` 端点（2026-08-23 设计文档 §8 既定项）
- 评论区抓取 → 差评点/情感分析（`review_pain` 从 LLM 推断升级为评论实证）
- 自家数据接入：Postgres 业务库 → `finance_model` 成本参数；watchlist `my_sku` 对标自家 SKU

### 批次 6：抓取引擎与主动通知（收尾；v2 修订：CloakBrowser 生产引入风控清单）

**CloakBrowser 是本计划最大的外部依赖风险，"源码级反检测"不等于安全、稳定、合规。** 生产引入前逐项过：

1. 固定版本 + 完整性校验：PyPI 官方源安装 + hash 校验；关闭其后台自动更新检查
2. 许可审查：wrapper 是 MIT，但 Chromium 二进制受 BINARY-LICENSE.md 单独条款约束——**先确认商业使用限制再引入**
3. 兼容性验证：`cloakbrowser.launch_persistent_context` 与 `crawler_runtime` 现有 Playwright 启动参数做差异清单（Cookie 注入、humanize JS、平台覆盖项逐个确认可用）
4. 隔离运行：独立进程 + 网络白名单 + 文件系统/凭据访问限制；cloak profile 目录生命周期纳入数据留存策略
5. 合规：公开页抓取仍走 robots.txt 闸门与网站条款；速率/重试/熔断**沿用 anti_ban 五道防线，cloak 引擎不豁免熔断**
6. **验收标准 = 实测**：兼容性、抓取成功率、资源消耗、稳定性四项指标达标才可启用——`--disable-blink-features=AutomationControlled` 只是启动参数基线加固，**不作为反检测能力的验收依据**

其余不变：`crawler_runtime` 加 `engine` 配置开关（playwright | cloak）；平台经验文档充实适配层；主动通知闭环（价格异动/评价增速突变/报告过期 → email / automation 推送）。

---

## 3. 验收与约定

- 每批次收尾：`./.venv/Scripts/python.exe -m pytest backend/tests/ -q -p no:randomly --no-cov`（基线 3541 passed，不可破）
- 前端改动三件套：`npx tsc --noEmit` + `npx vitest run` + 全路由 curl 200 冒烟
- 新 capability 一律走注册链五件套 checklist（见批次 1）
- 数据纪律：所有 LLM 输出数值必须可溯源（事实锁定），报告结论挂来源

## 4. 风险

| 风险 | 缓解 |
|---|---|
| agently-cli OAuth 凭据在服务器侧 | 凭据不入库不入 git；部署清单记录状态项 |
| CloakBrowser 二进制供应链 | 钉版本 + 首次内核下载（200MB）留部署说明；仅内网采集场景启用 |
| browser-act 云模式数据外发 | 配置层硬禁用 |
| market_research 公开数据时效差 | 报告标注数据日期与来源；关键数字走事实锁定 |

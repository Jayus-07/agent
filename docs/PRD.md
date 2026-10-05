# PRD — 企业智能运营 Agent 平台

> **产品需求文档** + **技术需求说明（TRD）** 融合版
> 范围：业务背景、产品定位、能力边界、模块需求、后续规划
> 配套阅读：[ARCHITECTURE.md](ARCHITECTURE.md) / [DESIGN.md](DESIGN.md) / [RAG_DESIGN.md](RAG_DESIGN.md) / [AGENT_DESIGN.md](AGENT_DESIGN.md) / [DATABASE.md](DATABASE.md) / [API.md](API.md) / [ROADMAP.md](ROADMAP.md)；部署与编排细节见 [architecture/system-overview.md](architecture/system-overview.md) 与 [architecture/ai-runtime.md](architecture/ai-runtime.md)

---

## 1. 项目背景

### 1.1 业务现状

企业运营场景里每天都在生成和使用大量结构化与非结构化数据：

| 数据类型 | 例子 | 现状 |
|---|---|---|
| **商品信息** | SKU、类目、规格、价格、卖点 | 分散在 ERP / 表格 / 文档 |
| **库存数据** | 库存数、安全库存、仓库、调拨 | 仅 DBA 能在 SQL 里查 |
| **销售数据** | 订单、客单价、转化率、退货 | Excel 透视，依赖分析师 |
| **业务规则** | SOP、审批流、合规检查 | Word / PDF，靠人传话 |
| **运营报告** | 日报 / 周报 / 库存预警 | 人工统计 + 邮件发送 |
| **客户咨询** | 售前售后、订单查询、投诉 | 人工客服逐单应答，高峰积压 |

**当前信息获取方式**：

- 人工 SQL 查询（DBA 排期）
- 翻企业文档（SharePoint / 网盘）
- Excel 透视分析（依赖业务分析师）
- 定期人工生成报告（每天 1-2 小时）

### 1.2 核心问题

| 问题 | 业务影响 |
|---|---|
| **信息分散** | 跨系统查找，单次问题 30 分钟 |
| **非技术人员无法直接查询** | 业务方依赖 IT，决策链路长 |
| **文档知识无法快速复用** | 老员工经验流失，新人重复踩坑 |
| **数据分析依赖人工经验** | 同样的数据，不同人解读不同 |
| **日常报告重复劳动** | 日报 / 周报占团队 30% 时间 |
| **客服人力成本高** | 重复问题占比高，7×24 无法覆盖 |

### 1.3 解决方案

**RAG + Multi-Agent + 数据分析** 融合的企业级 AI 平台：

- **RAG** —— 让企业文档成为"可对话的知识库"
- **Multi-Agent** —— 把复杂任务自动拆解、自动调度、自动执行；垂直场景（客服 / 旅游 / 选品）走专属域图
- **数据分析** —— 自然语言查询业务数据库，自动生成报告
- **人机协作** —— AI 客服一线应答，复杂问题转人工坐席接管

**业务价值**：从「查数据要 30 分钟」缩短到「问一句话 5 秒出答案」。

---

## 2. 项目目标

### 2.1 总目标

构建面向企业运营场景的 AI Agent 平台，实现：

```
用户提出问题（自然语言）
        ↓
   系统自动理解（路由 / 域检测 / 任务规划）
        ↓
   调用业务能力（RAG / SQL / Report / 客服 / 旅游 / Workflow）
        ↓
   查询数据 + 分析结果（全程 Trace + 证据引用）
        ↓
   生成报告 / 输出结论
        ↓
   用户拿到可决策的答案（AI 兜不住时转人工）
```

### 2.2 五大核心能力

#### ① 企业知识问答

支持制度、产品资料、SOP、项目文档等。例如：

> 用户：退货流程是什么？
> 系统：RAG 检索 → 引用来源 → 标注可信度（证据不足主动拒答）

#### ② 数据智能分析

支持自然语言查询业务数据库。例如：

> 用户：最近 30 天销售下降超过 20% 的商品有哪些？
> Agent：NL2SQL → 6 层校验 → 执行 → 业务解释

#### ③ 自动运营报告

支持日报 / 周报 / 库存分析报告。例如：

```
定时任务 → SQL 查询 → Agent 分析 → Markdown 报告 → 邮件发送
```

#### ④ 智能客服（AI 一线 + 人工坐席）

客服窗口内 AI 按 5 专家（知识 / 查询 / 行动 / 投诉 / 转接）分工应答；
涉及退款、改单等敏感操作生成待确认动作；AI 兜不住时转人工，
坐席在独立工作台（:3300）接管会话、查工单、看统计。

#### ⑤ 垂直场景域图（旅游 / 选品）

旅游行程规划：表单或对话提交需求 → 槽位补全 → 多专家编排 → 四轴校验（时间/地理/体力/预算）→ 结构化行程 + ICS 日历导出。
选品决策：市场评估 → 差异化分析 → 财务测算 → AI 评审团，漏斗指标在管理端看板跟踪。

---

## 3. 用户角色

| 角色 | 入口 | 需求 | 平台权限 |
|---|---|---|---|
| **普通用户 / 运营人员** | 用户端 :3100 | 知识问答、数据查询、旅游规划、客服咨询 | 默认 `viewer`（注册兜底） |
| **管理员** | 管理端 :3200 | 知识库 / 模型 / 预算 / RBAC / 审批 / 可观测 | `platformRole` 分级（`super_admin` 最高） |
| **客服坐席** | 坐席工作台 :3300 | 接管 AI 转人工会话、工单流转、统计 | `cs_agents` 名单制（`csRole`） |
| **未登录访客** | 统一门户 `/` | 三端入口导航 | 仅门户页 |

**当前实现状态**（2026-09-29）：

- ✅ 自建 JWT 鉴权（`backend/security/local_jwt.py`，auth.sessions 设备登录 + sid claim），APISIX 网关 `gateway-auth` 插件统一验签并注入 X-User-Id 身份头
- ✅ RBAC：`rbac.py` 平台角色 + 坐席角色 + 乐观锁，注册兜底 viewer
- ✅ 多租户为默认口径（JWT 携带 tenant claim，网关注入租户头）
- ✅ 三端均有全局 AuthGate；超级管理员账号走受控本机提权脚本

---

## 4. 系统功能需求

### 4.1 Agent 对话中心（FR-001 ~ FR-002）

**功能**：统一聊天入口，一个问题框内按语义自动分流（知识问答 / 数据查询 / 报告 / 客服 / 旅游）。

| ID | 需求 |
|---|---|
| FR-001 | 用户输入自然语言问题，流式返回答案与执行过程 |
| FR-002 | 系统自动识别任务类型并路由：直接执行 / Workflow / 任务规划 / 垂直域图 |

**当前实现**：

- ✅ [frontend/src/app/agent/page.tsx](../frontend/src/app/agent/page.tsx) — 聊天主界面（`/agent`）
- ✅ SSE 流式响应（`POST /chat/stream`，meta → status/log/delta → done/error，支持断线恢复）
- ✅ 中断生成（发送键兼任停止键）、历史会话加载
- ✅ 客服抽屉（右上角，走客服锁域链路）
- ✅ RoutingEngine 统一路由（domain → intent → capability → policy，六阶段固定序）+ 三域预过滤（客服 > 旅游 > 选品）+ 灰度放量开关

### 4.2 RAG 知识库（FR-RAG-001 ~ FR-RAG-003）

**功能**：企业文档智能检索，证据不足主动拒答。

支持文件：PDF / DOCX / Markdown / TXT（含 OCR 兜底）。

| ID | 需求 |
|---|---|
| FR-RAG-001 | 多知识库按 kb_id 隔离，支持 fixture 评测集管理 |
| FR-RAG-002 | 文档元数据治理：doc_type / business_domain / summary / chunk_keywords（规则 + LLM 统一抽取双链） |
| FR-RAG-003 | 回答必须带内联引用 `[1][2]` + 参考文献列表；检索质量不足时三层拒答 |

**当前实现**：

- ✅ 类型感知切片 + Metadata 治理（含灰度与影子一致率链路）
- ✅ 混合检索：Vector + BM25 → RRF 融合 → 同文档扩展 → CrossEncoder Rerank
- ✅ Evidence Gate 三层主动拒答（Retrieval / Rerank / Faithfulness NLI）+ META 尾拒答判定
- ✅ 管理端知识库页面：上传 / 关键词 / 待审队列 / 运营指标 / 索引 trace / 重索引
- ⚠️ 评测基线：现行模型栈（DB 治理）下评测需先重建 fixture 库（历史基线数字与现行栈不可比）

详细设计：[RAG_DESIGN.md](RAG_DESIGN.md)

### 4.3 SQL Agent（FR-SQL-001 ~ FR-SQL-003）

**功能**：自然语言查询业务数据库。

| ID | 需求 |
|---|---|
| FR-SQL-001 | 自然语言问题自动生成 SQL 并执行，返回业务解释 |
| FR-SQL-002 | SQL 执行前必须经过 6 层验证 |
| FR-SQL-003 | 禁止危险 SQL（DROP / DELETE / UPDATE / TRUNCATE / 写函数） |

**当前实现**：

- ✅ 链路：SQLSkill → SQLAgent → Router → Generator → Validator（6 层）→ RowSecurity → Executor（连接池）→ PostgreSQL
- ✅ 6 层安全：① SELECT 校验 ② 表名白名单 ③ 敏感列拒绝 ④ 函数黑名单 ⑤ LIMIT 强制 ⑥ `agent_readonly` 只读角色
- ✅ 8 种 SQLStatus 状态机 + 行级安全（sqlglot AST 重写 + 参数化）
- ✅ 数据协议 SQLResult / BusinessInsight，供 Supervisor 跨步骤注入

**已知限制**：

- 仅支持 PostgreSQL（业务库 7 schema × 18 表）
- 关键词快路硬编码（新增业务域需手动维护）

### 4.4 Multi-Agent 编排（FR-AGENT-001 ~ FR-AGENT-005）

**功能**：复杂任务自动拆解、调度、执行。

| ID | 需求 |
|---|---|
| FR-AGENT-001 | 主图 9 核心节点编排；LLM 决策节点仅 4 个（Planner / Critique / Reporter / general_chat） |
| FR-AGENT-002 | Capability 声明式注册（现 17 个，其中 3 个内部能力不对路由开放） |
| FR-AGENT-003 | Capability DAG 依赖调度 + Send[] 并行执行 |
| FR-AGENT-004 | 降级链（sql 空 → rag；rag 空 → sql） |
| FR-AGENT-005 | 实时 SSE 事件（status / log / delta / done / error） |

**当前实现**：

- ✅ direct / workflow / plan 三条执行支线；plan 支线 Planner → Critique → Supervisor（纯规则 DAG 调度，recursion_limit 80）
- ✅ 12 Skill / 34 Tool / 4 Workflow 声明式注册，启动期一致性测试门禁（registry/layer/adr0001/base_output 四测试）
- ✅ 5 个垂直域图（客服 / 旅游 / 选品漏斗 / 旅游商务 / 旅游预订），代码默认全关、由环境开关放量
- ✅ MCP 双 Server（5 Tool）作为 Tool 层第二出口

**数量口径纪律**：以根 [README.md](../README.md)「系统规模」表为唯一权威，本文不复抄数字。

详细设计：[AGENT_DESIGN.md](AGENT_DESIGN.md)

### 4.5 自动报告与 Workflow（FR-REPORT-001 ~ FR-REPORT-003）

**功能**：自动生成 + 推送运营报告；多步业务流程自动化。

| ID | 需求 |
|---|---|
| FR-REPORT-001 | 内置报告模板（日销售 / 商品表现 / 库存健康 等） |
| FR-REPORT-002 | 模板引擎（Jinja2 沙箱）+ 图表 + LLM 润色 + 数值硬校验 |
| FR-REPORT-003 | 定时任务 + 邮件推送 |

**当前实现**：

- ✅ Workflow 引擎（@workflow / @step + DAG + Scheduler），4 个 Workflow：日报 / 库存预警 / 市场调研（12 章节证据管线报告）/ 选品决策
- ✅ 邮件发送走 PG 幂等账本（重复触发不重发）
- ✅ 长任务走 Celery 异步队列（agent ｜ rag_index 双队列），状态权威在 PG tasks 表，失败从最近 checkpoint 自愈续跑

### 4.6 智能客服与人工坐席（FR-CS-001 ~ FR-CS-004）

**功能**：AI 客服一线应答，复杂场景人机协作。

| ID | 需求 |
|---|---|
| FR-CS-001 | 客服窗口内 AI 按专家分工应答（知识 / 订单查询 / 行动办理 / 投诉 / 转接） |
| FR-CS-002 | 敏感操作（退款 / 改单）生成待确认动作，用户确认后才执行（确认链接线 + 幂等账本） |
| FR-CS-003 | AI 兜不住时转人工（handoff），坐席工作台接管 |
| FR-CS-004 | 坐席工作台：会话列表 / 会话详情 / handoff 流转 / 工单 / 统计 |

**当前实现**：

- ✅ 客服域图：supervisor（handoff 拦截 / 循环上限 / LLM 兜底）+ 5 专家 + pending 动作确认
- ✅ 坐席工作台 `frontend-cs`（:3300）：`/cs`、`/cs/conversations`、`/cs/handoff`、`/cs/tickets`、`/cs/stats`，坐席名单制鉴权
- ✅ 双向输入中指示器、会话历史徽章、2s 轮询（v1）

### 4.7 旅游行程规划（FR-TRAVEL-001 ~ FR-TRAVEL-003）

**功能**：多轮补全旅行需求，产出可执行的结构化行程。

| ID | 需求 |
|---|---|
| FR-TRAVEL-001 | 双通路：独立表单页（`/travel`）或主对话框对话式规划，共用同一旅游域 |
| FR-TRAVEL-002 | 行程必须通过四轴校验（时间 / 地理 / 体力 / 预算），不通过自动局部修复，用户点名必去条目不丢弃 |
| FR-TRAVEL-003 | 结构化行程（逐日时间轴 + 地图打点）+ ICS 日历导出 |

**当前实现**：

- ✅ 旅游域图：slot_filler → supervisor（纯规则）→ 5 专家（POI / 交通 / 预算 / 风险 / 天气）→ validator → repair
- ✅ 跨轮补槽（brief 指纹变更自动重置规划）、断点恢复
- ✅ 数据源已切实时检索（2026-10-02：POI 候选池 = 腾讯 LBS 实时检索，`TRAVEL_POI_SOURCE` 默认 live；车票查询 = 12306 MCP Tool；规划产物带版本链，历史规划可恢复）
- ⚠️ 预订 / 商务域图机制完成但无真实供应商接入（剩余缺口=预订/结算侧，查询侧地图/票务已接）

### 4.8 选品决策与漏斗（FR-SEL-001 ~ FR-SEL-002）

| ID | 需求 |
|---|---|
| FR-SEL-001 | 选品 Workflow：市场评估 → 差异化分析 → 财务测算 → AI 评审团 |
| FR-SEL-002 | 选品漏斗域图 + 管理端漏斗指标看板（趋势已接线） |

**当前实现**：✅ 选品漏斗域图（预过滤与旅游同层）、管理端选品决策 / 漏斗 / 竞品监控 / 预警 / 报告页面。

---

## 5. 非功能需求

### 5.1 性能目标

| 场景 | 目标 |
|---|---|
| 普通问答（单步 RAG） | < 5s |
| SQL 查询（NL2SQL + 6 层校验） | < 10s |
| 报告生成（含图表 + LLM 润色） | < 30s |
| 批量查询（多 skill 并行） | < 60s |

### 5.2 可扩展性

新增能力走声明式注册 + 启动期派生 + fail-fast（G1~G3 铁律）：

- 新增 Tool：`@tool` + 文件底部注册，2 处
- 新增 Skill + capability：5 处接线，漏 `capabilities.yaml` 启动即报错
- 契约回归门：registry / layer / adr0001 / base_output 四个一致性测试 + e2e 故障注入

完整模板见 [2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md](2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md)。

### 5.3 可观测性

每个请求产出完整 Trace 树：

- HTTP 层埋点（trace_middleware）+ LangGraph 节点级 Span + LLM 调用（token / cost / latency）
- 检索分数（向量 / BM25 / Rerank）、工具调用、任务执行全程贯穿（session_id = thread_id）
- 管理端分布式 Traces / 告警 / 网关日志 / Token 用量页面；Celery worker 独立指标端口

### 5.4 稳定性与可靠性

- ✅ 重试 + 降级 + 超时 + 限流（429）+ 熔断器（状态跨进程 Redis 共享）
- ✅ SSE backpressure（队列满不丢流式内容）+ 断线恢复协议
- ✅ 异步任务运行时：PG 状态权威 + 租约 / fencing + admission 控制 + 错误分类学重试，at-least-once 且单 owner
- ✅ 副作用幂等：`ai.idempotency_records` 复用账本 + IN_DOUBT 裁决 + 运维 CLI 与 Runbook
- ✅ 全量回归基线：7336 用例分块执行口径（2026-09 收官），生产冒烟脚本 production_smoke_v1

### 5.5 安全性

| 防护 | 层级 |
|---|---|
| 身份鉴权 | 自建 JWT（issuer=agent-platform）+ APISIX 网关验签 + X-User-Id 身份头契约 |
| 授权 | RBAC（平台角色 + 坐席名单制），注册兜底最低权限 |
| SQL 写操作 | 6 层硬校验 + 只读账号 + 事务级只读 |
| 副作用工具 | 写操作审批门（`tool_approval.ensure_approved()`，管理员经 `/api/approvals` 批准放行） |
| 多租户隔离 | JWT tenant claim + 网关注入租户头 + checkpoint 跨租户防护 |
| LLM 提示词注入 | Faithfulness NLI 校验 + Evidence Gate 拒答 |
| 数据越权 | Row Security 参数化行级注入（可信身份源已接入） |
| 审计 | 幂等账本 / 审批单 / Token 用量 / 网关日志全量落库 |

---

## 6. 当前开发状态

### 6.1 ✅ 已完成

| 阶段 | 内容 |
|---|---|
| **Phase 1 — 基础智能助手** | RAG / SQL Agent / Agent 框架 / 报告 / Memory 三层 / Trace 观测 |
| **Phase 2 — 运营自动化** | Workflow 引擎 / 库存预警 / 数据采集 / 邮件幂等 |
| **Phase 3 — 企业平台化** | JWT 鉴权 / RBAC / 多租户 / 审计落库 / SQLite→PG 全量收口 / Migration 治理（sql/migrations/，编号至 054） |
| **垂直域 + 生产收口（2026-09 下旬）** | 5 域图 / 坐席工作台 / 异步任务运行时 / 副作用幂等 / 记忆与上下文预算收口 / 模型治理 / 发布门禁（release.sh + 12 Gate）/ Final RC 全量回归（7336 用例）/ 生产冒烟七个 PASS |

### 6.2 🚧 进行中 / 已知缺口

| 项 | 状态 |
|---|---|
| RBAC super_admin 两级授权与撤权即时吊销 | 机制在库（migration 054），推广待实施 |
| RAG 评测栈 | 现行模型栈（DB 治理）下需重建 fixture 库后重跑基线 |
| 旅游预订真实供应商 | 域图 / 幂等 / 契约就绪，等真实供应商接入 |
| 灰度放量与真实故障演练 | 发布前门禁类遗留项 |
| 管理端重索引按钮 remote 断层 | 已登记待修 |

---

## 7. 后续规划

### 7.1 近期（生产补强）

- RBAC 两级授权收口 + 会话即时吊销
- RAG 评测基线在现行栈重建（fixture 重 ingest → 双跑 → 门禁刷新）
- 灰度放量机制落地（域开关从手动 env 走灰度面板）+ 一次真实故障演练
- 架构减法 STOP E / STOP M（遗留收尾）

### 7.2 中期（业务扩展）

- 三方系统集成（ERP / WMS / 财务系统）
- 旅游真实供应商接入（查询侧已落地：POI 腾讯 LBS 实时检索 + 12306 票务查询 MCP；剩余=预订/结算侧真实供应商，契约不变）
- 移动端 / 微信 / 钉钉入口

### 7.3 远期

- Agent 市场（Skill 共享 + 第三方集成）
- 可观测性接入 OTel 生态

### 7.4 关键风险（详见 [ROADMAP.md](ROADMAP.md)）

| 风险 | 缓解 |
|---|---|
| LLM 成本失控 | 预算闭环（租户级）+ Token 用量看板 + 模型价格双人审核 |
| LLM 幻觉 | Faithfulness NLI + Evidence Gate 三层拒答 |
| 域图误触发 | RoutingEngine 统一决策 + 灰度开关默认全关 + 路由守护测试 |
| 并行会话协作事故 | pathspec 提交纪律 + 阶段完成立即落库（见 AGENTS.md） |

---

## 验证

最后验证：2026-10-05 · 路由口径随 RoutingEngine 收口更新（验收清单 `docs/reports/2026-10-05-路由层企业级改造验收清单.md`）；其余结构与能力口径对照根 [README.md](../README.md)「系统规模」（2026-10-03 实测）与 [AGENTS.md](../AGENTS.md)；数量类信息以根 README 为唯一权威，本文不复抄。

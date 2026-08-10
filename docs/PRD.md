# PRD — 企业智能运营 Agent 平台

> **产品需求文档** + **技术需求说明（TRD）** 融合版
> 范围：业务背景、产品定位、能力边界、模块需求、后续规划
> 配套阅读：[ARCHITECTURE.md](ARCHITECTURE.md) / [RAG_DESIGN.md](RAG_DESIGN.md) / [AGENT_DESIGN.md](AGENT_DESIGN.md) / [DATABASE.md](DATABASE.md) / [API.md](API.md) / [ROADMAP.md](ROADMAP.md)

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

### 1.3 解决方案

**RAG + Multi-Agent + 数据分析** 融合的企业级 AI 平台：

- **RAG** —— 让企业文档成为"可对话的知识库"
- **Multi-Agent** —— 把复杂任务自动拆解、自动调度、自动执行
- **数据分析** —— 自然语言查询业务数据库，自动生成报告

**业务价值**：从「查数据要 30 分钟」缩短到「问一句话 5 秒出答案」。

---

## 2. 项目目标

### 2.1 总目标

构建面向企业运营场景的 AI Agent 平台，实现：

```
用户提出问题（自然语言）
        ↓
   系统自动理解
        ↓
   任务规划（拆解 + 排序）
        ↓
   调用业务能力（RAG / SQL / Report / Email）
        ↓
   查询数据 + 分析结果
        ↓
   生成报告 / 输出结论
        ↓
   用户拿到可决策的答案
```

### 2.2 三大核心能力

#### ① 企业知识问答

支持制度、产品资料、SOP、项目文档等。例如：

> 用户：退货流程是什么？
> 系统：RAG 检索 → 引用来源 → 标注可信度

#### ② 数据智能分析

支持自然语言查询业务数据库。例如：

> 用户：最近 30 天销售下降超过 20% 的商品有哪些？
> Agent：NL2SQL → 校验 → 执行 → 业务解释

#### ③ 自动运营报告

支持日报 / 周报 / 库存分析报告。例如：

```
定时任务 → SQL 查询 → Agent 分析 → Markdown 报告 → 邮件发送
```

---

## 3. 用户角色

| 角色 | 需求 | 当前权限 |
|---|---|---|
| **运营人员** | 查询商品 / 库存 / 销售 | 知识查询、数据分析、报告查看 |
| **管理人员** | 经营指标 / 趋势分析 | 报告生成、数据分析、决策辅助 |
| **管理员** | 知识库管理 / 文档上传 / 配置 | KB 管理、文档管理、系统配置 |

**当前实现状态**：

- 角色概念在代码中**未完整实现**（无 users / roles / permissions 表，无登录页）
- 现有"用户"是**未受约束的字符串入参**（如 `user_id="default"`）
- 鉴权仅靠单 API Key（默认空 = 全开放）—— 详见 [ROADMAP.md P0 项](ROADMAP.md)

---

## 4. 系统功能需求

### 4.1 Agent 对话中心（FR-001 ~ FR-002）

**功能**：统一聊天入口。

| ID | 需求 |
|---|---|
| FR-001 | 用户输入自然语言问题 |
| FR-002 | 系统自动识别任务类型：知识查询 / 数据查询 / 报告生成 / 复杂任务 |

**当前实现**：

- ✅ [frontend/src/app/agent/page.tsx](frontend/src/app/agent/page.tsx) — 聊天主页
- ✅ SSE 流式响应（`POST /chat/stream`）
- ✅ 中断生成（`POST /chat/abort`）
- ✅ 历史会话加载

### 4.2 RAG 知识库（FR-RAG-001 ~ FR-RAG-003）

**功能**：企业文档智能检索。

支持文件：PDF / DOCX / Markdown / TXT。

| ID | 需求 |
|---|---|
| FR-RAG-001 | 支持多知识库（库存 / 订单 / 商品 / 制度 等按 kb_id 隔离） |
| FR-RAG-002 | 支持文档元数据：`doc_type` / `business_domain` / `summary` / `chunk_keywords` |
| FR-RAG-003 | 回答必须包含引用（来源文档 + 匹配关键词） |

**当前实现**：

- ✅ 11 个端点（`/rag/*`）：上传 / 搜索 / 重索引 / 删除 / 操作日志
- ✅ 6 段流水线：HistoryAware → MultiQuery → ChunkLevel → Adaptive → Rerank → LLM Generate
- ✅ Hybrid 检索（向量 + BM25 + RRF 60）
- ✅ CrossEncoder Rerank + sigmoid 归一化（阈值 0.3）
- ✅ Citation 内联标注 `[1][2]` + Evidence Gate 三层拒答 + Faithfulness NLI 校验
- ✅ 11 个前端端点 + 4 个知识库管理页面

详细设计：[RAG_DESIGN.md](RAG_DESIGN.md)

### 4.3 SQL Agent（FR-SQL-001 ~ FR-SQL-003）

**功能**：自然语言查询业务数据库。

| ID | 需求 |
|---|---|
| FR-SQL-001 | 用户输入自然语言问题，自动生成 SQL 并执行 |
| FR-SQL-002 | SQL 执行前必须经过 6 层验证 |
| FR-SQL-003 | 禁止危险 SQL（DROP / DELETE / UPDATE / TRUNCATE / 写函数） |

**当前实现**：

- ✅ 6 层安全：① SELECT 类型校验 ② 表名白名单 ③ 敏感列拒绝 ④ 禁止函数黑名单 ⑤ LIMIT 强制 ⑥ agent_readonly 只读账号
- ✅ 双 API：旧 `ask()`（Markdown 字符串）/ 新 `ask_struct()`（SQLResult dataclass）
- ✅ 8 种 SQLStatus 状态（success / no_data / failed / timeout / syntax_error / permission_denied / validation_error / no_table）
- ✅ 行级安全（sqlglot 重写 AST + 参数化注入）
- ✅ 连接池（ThreadedConnectionPool min=2 max=10）

**已知限制**：

- 仅支持 PostgreSQL
- 关键词快路硬编码（新增业务域需手动维护）
- 仅 PG schema 白名单静态配置

### 4.4 Multi-Agent 编排（FR-AGENT-001 ~ FR-AGENT-005）

**功能**：复杂任务自动拆解、调度、执行。

| ID | 需求 |
|---|---|
| FR-AGENT-001 | 5 节点编排：Planner → Critique → Supervisor → Skills → Reporter |
| FR-AGENT-002 | 自动派发 9 个 Capability（sql / rag / report / email / export / web / data 等） |
| FR-AGENT-003 | DAG 依赖调度 + Send[] 并行执行 |
| FR-AGENT-004 | 降级链（sql 空 → rag；rag 空 → sql） |
| FR-AGENT-005 | 实时 SSE 事件（status / log / delta / done） |

**当前实现**：

- ✅ 9 个 Capability（[AGENT_DESIGN.md 第 4 节](AGENT_DESIGN.md)）
- ✅ Capability DAG（nodes + edges）由 LLM Planner 生成
- ✅ 4 层 JSON 修复管道 + 5min LRU 缓存
- ✅ 规则引擎 critique（0ms 规则 + anomaly 时 LLM 兜底）
- ✅ Supervisor 依赖检查 + 就绪派发 + 卡死检测（上限 10 轮）
- ✅ 降级链 + 引用来源合并 + LLM 一句话总结

详细设计：[AGENT_DESIGN.md](AGENT_DESIGN.md)

### 4.5 自动报告（FR-REPORT-001 ~ FR-REPORT-003）

**功能**：自动生成 + 推送运营报告。

| ID | 需求 |
|---|---|
| FR-REPORT-001 | 支持 6 种内置报告（日销售 / 商品表现 / 库存健康 / 广告表现 / 订单履约 / 客户分析） |
| FR-REPORT-002 | 模板引擎（Jinja2 沙箱）+ 图表（matplotlib Agg） |
| FR-REPORT-003 | 定时任务 + 邮件推送 |

**当前实现**：

- ✅ ReportSkill（生成）+ EmailSkill（推送）
- ✅ TemplateEngine 沙箱 + 6 个内置 .j2 模板
- ✅ ChartGenerator（bar / pie / line）+ LLM 润色 + 数值硬校验
- ✅ 30 天数据快照 + 用户偏好学习
- ✅ Workflow 引擎已集成（daily_report / inventory_alert 两个实例）

**已知限制**：

- 报告生成是同步阻塞（用 `asyncio.to_thread` 包装）
- 图表不支持 scatter / heatmap / dual-axis
- 无 PDF / HTML / Excel 导出（仅 Markdown）
- 无国际化（中文硬编码）

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

**新增能力**（即 Skill 注册）只需 3 步：

```
1. backend/skills/<name>/skill.py    创建 Skill 类
2. backend/skills/registry.py        import 注册
3. Planner 自动发现                  Capability DAG 自动可用
```

无需修改框架代码。

### 5.3 可观测性

每个请求产出完整 Trace 树：

- HTTP 层埋点（trace_middleware）
- LangGraph 节点级 Span
- LLM 调用（Token / cost / latency）
- 检索结果（向量 / BM25 / Rerank 分数）
- 工具调用（SQL / RAG / Report）
- 时间轴 + 火焰图 + 拓扑图 + 成本面板

### 5.4 稳定性

- ✅ 重试 + 降级 + 超时 + 限流（429）+ 熔断器
- ✅ SSE 队列满触发 backpressure（不丢流式内容）
- ✅ 错误兜底（业务异常 → Markdown 友好提示，不曝 500）

### 5.5 安全性

| 防护 | 层级 |
|---|---|
| SQL 写操作 | 6 层硬校验 + 只读账号 + 事务级只读 |
| LLM 提示词注入 | Faithfulness NLI 校验 + Evidence Gate 拒答 |
| 数据越权 | Row Security（参数化行级注入，**待鉴权接入可信源**） |
| API 暴露 | 单 API Key（**当前默认空 = 全开放，待补**） |

---

## 6. 当前开发状态

### 6.1 ✅ 已完成（Phase 1 — 基础智能助手）

| 模块 | 能力 |
|---|---|
| **RAG** | 文档上传 / 解析 / Chunk / Hybrid 检索 / Rerank / Citation / Evidence Gate |
| **Agent 框架** | LangGraph / Router / Planner / Supervisor / Skill 体系 |
| **SQL Agent** | NL2SQL / 6 层校验 / 连接池 / 数据分析 |
| **Memory** | L1 短期 / L2 会话 / L3 长期（pgvector） |
| **报告** | 6 种内置报告 / 模板引擎 / 图表 / 邮件 |
| **观测性** | Trace / Token / Span / 火焰图 / 拓扑图 |

### 6.2 🚧 进行中（Phase 2 — 运营自动化）

| 模块 | 状态 |
|---|---|
| **Workflow 引擎** | ✅ @workflow / @step / DAG / Scheduler；2 个实例（daily_report / inventory_alert） |
| **数据采集中心** | ✅ 5 阶段 Pipeline / 5 个本地数据集 / 通用清洗 |
| **库存预警** | ✅ 阈值规则 / 告警中心 / 通知策略 |
| **长期 Memory 衰减** | ✅ 衰减服务；🚧 cron 入口未接 |
| **Workflow LLM 兜底** | 🚧 `_llm_fallback` 是 stub |

### 6.3 📋 待启动（Phase 3 — 平台化）

| 优先级 | 模块 | 原因 |
|---|---|---|
| **P0** | 身份鉴权 / RBAC | 无 users / roles 表，所有 user_id 客户端自报 |
| **P0** | 多租户 / 数据隔离 | row_security 机制已就绪，缺可信身份源 |
| **P0** | 审计日志 | 0 写入点 |
| **P0** | 核心数据迁 PostgreSQL | 14 个 SQLite 文件散落（含告警 / 报告 / 链路） |
| **P1** | Migration 治理 | 裸 SQL + 编号冲突（003 重复） |
| **P1** | 密钥管理 | 只读库密码硬编码默认值 |
| **P1** | 前端数据层统一 | react-query 装但未用 / 两套 API 客户端 |
| **P2** | 可观测性接入 OTel | 当前自建 Tracer |
| **P2** | 测试覆盖 + E2E | 部分覆盖 |

详见 [ROADMAP.md](ROADMAP.md)

---

## 7. 后续规划

### 7.1 Phase 1 — 基础智能助手（已完成）

- ✅ RAG + SQL Agent + Agent 框架 + 报告生成
- ✅ 单一 Agent 入口（chat）
- ✅ 可观测性 + 稳定性基础

### 7.2 Phase 2 — 运营自动化（进行中）

- ✅ Workflow 引擎（@workflow / @step + DAG）
- ✅ 库存预警 + 告警中心
- ✅ 数据采集中心
- 🚧 Memory 衰减 cron
- 🚧 Workflow LLM 兜底

### 7.3 Phase 3 — 企业平台化（待启动）

- 🔴 身份鉴权 → RBAC → 多租户（**作为整体价值主张**）
- 🔴 审计日志
- 🔴 Agent 市场（Skill 共享 + 第三方集成）
- 🔴 三方系统集成（ERP / WMS / 财务系统）
- 🔴 移动端 / 微信 / 钉钉入口

### 7.4 关键风险（详见 [ROADMAP.md](ROADMAP.md)）

| 风险 | 缓解 |
|---|---|
| 鉴权缺失无法上线 | P0 立项，机制已就绪（row_security + JWT） |
| SQLite 数据层不可扩展 | P0 立项，迁移 PG backup 文件 |
| LLM 成本失控 | rate_limit + 成本埋点 + 缓存 |
| LLM 幻觉 | Faithfulness NLI + Evidence Gate 三层拒答 |

---

## 验证

最后验证：2026-08-10 · 与代码一致（[7 schema × 19 表](DATABASE.md) + 22 路由 / [API.md](API.md) + 9 Capability / [AGENT_DESIGN.md](AGENT_DESIGN.md)）。

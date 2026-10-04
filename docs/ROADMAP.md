# ROADMAP — 迭代规划与现状差距

> 本文档聚焦"现状距企业级还差什么 + 分 3 期怎么补"。
> 配套阅读：[PRD.md](PRD.md) / [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 1. 现状差距矩阵

按 P0/P1/P2 三档排序，**P0 = 不补不能上线**，**P1 = 影响规模化**，**P2 = 长期工程债务**。

### 🔴 P0 — 不补不能上线（✅ 已于 2026-09 全部关闭）

| 维度 | 原现状（2026-08） | 与企业级差距 | 落地结果 |
|---|---|---|---|
| **身份鉴权** | 单一全局 API Key（默认空 = 全开放） | OIDC / JWT + 刷新令牌 + 会话管理 | ✅ 自建 JWT（`security/local_jwt.py`，auth.sessions 设备登录 + sid claim + 60s 宽限）+ APISIX 网关验签 |
| **RBAC / 3 类角色** | 完全不存在 | 角色 → 权限 → 资源三级 + 前后端双向校验 | ✅ `rbac.py` 平台角色 + 坐席名单制 + 三端登录页 + navConfig 按角色过滤 + 后端 403 兜底 |
| **多租户 / 数据隔离** | row_security 就绪但身份可伪造 | 隔离参数由服务端注入 | ✅ JWT tenant claim + 网关注入租户头 + checkpoint 跨租户防护（多租户为默认口径） |
| **审计日志** | 无 | 谁 / 何时 / 操作 / 资源 ≥ 180 天 | ✅ 幂等账本 / 工具审批单 / Token 用量 / 网关日志全量落库 |
| **核心数据存 SQLite** | 14 个 SQLite 单文件 | PostgreSQL 统一 | ✅ 全量收口 PG（7 schema × 18 表，SQLite 散落存储下线） |

> 上表「关键判断」段落为 2026-08 的规划论证，四个预言全部兑现，保留作过程记录。

**🔑 关键判断**：**鉴权不是"补一个登录页"，而是解锁 3 件事的钥匙**：

1. 审计日志（无身份不可审计）
2. 多租户隔离（row_security 已就绪）
3. Trace 的 user 维度（无法做用户级分析）

三者当前全部因无可信身份而无法落地。建议作为**捆绑价值主张**而非 4 个独立需求。

### 🟡 P1 — 影响规模化（2026-09-29 复核）

| 维度 | 原现状（2026-08） | 落地结果 |
|---|---|---|
| **Migration 治理** | 裸 SQL + 编号重复，无回滚 | ✅ `sql/migrations/` 编号治理（至 069，061 编号空缺）+ db-migrate 工具 + 迁移三层校验；Alembic 引入仍未做 |
| **数据模型文档** | database.md 41 行 | ✅ 已迁 [DATABASE.md](DATABASE.md) 595 行 |
| **密钥管理** | `.env` 明文；硬编码默认值 | 🔴 未做（硬编码 key 已清除，KMS 接入未启动） |
| **前端数据层** | react-query 装而未用；两套 API 客户端 | ✅ react-query v5 为三端标准（轮询/缓存收口），BFF 代理统一 |
| **前端错误语义** | 静默吞错返回 `[]` | ✅ `describeApiError` 归一 + ErrorState/ErrorCard 统一组件 |
| **Workflow LLM 兜底** | `_llm_fallback` 是 stub | 🚧 router 级 LLM 兜底已接（`orchestration/workflow/router.py`），任务级兜底以代码为准 |

### 🟢 P2 — 长期工程债务

| 维度 | 现状 | 改进方向 |
|---|---|---|
| **可观测性接入 OTel** | 自建 Tracer（功能完整） | OpenTelemetry → Jaeger / Tempo + Grafana |
| **健康检查** | `/health` 仅确认进程存活 | `/health/ready` 深度探测 DB / LLM / Chroma |
| **测试覆盖** | 部分覆盖 + Vitest | 契约 + E2E + 负载 + Golden Dataset |
| **驾驶舱** | Sidebar 首项"数据驾驶舱" → 重定向 `/agent` | 面向 3 类角色的差异化首页 |
| **数据质量规则引擎** | 采集仅清洗三步，无业务校验 | 规则引擎 + 异常告警 |
| **数据血缘** | pipeline 不感知表依赖 | 自动血缘采集 + 可视化 |

---

## 2. Phase 1 — 基础智能助手（✅ 已完成）

> 以下为 Phase 1 收官时（2026-08）的规模口径；当前规模（9 核心节点 / 17 Capability / 5 域图）以根 [README.md](../README.md)「系统规模」为准。

**目标**：单 Agent 入口，回答用户问题。

| 里程碑 | 状态 | 关键产物 |
|---|---|---|
| RAG 6 段流水线 | ✅ | HistoryAware → MultiQuery → ChunkLevel → Adaptive → Rerank → LLM Generate |
| Multi-Agent 5 节点 | ✅ | Planner → Critique → Supervisor ⇄ Skills → Reporter |
| 9 Capability | ✅ | sql.query / rag.search / business.analyze / report.generate / email.send / data.export / web.search / web.crawl / data.collect |
| SQL Agent 6 层校验 | ✅ | 类型 / 白名单 / 敏感列 / 黑名单 / LIMIT / 只读账号 |
| Memory 3 层 | ✅ | L1 短期 / L2 会话 / L3 长期（pgvector） |
| 报告生成 | ✅ | 6 种内置 + Template + Chart + LLM 润色 |
| 可观测性 | ✅ | Trace + 14 前端组件 + Prom 指标 |
| 稳定性 | ✅ | 重试 / 降级 / 超时 / 限流 / 熔断 / SSE backpressure |

**代码量**：~30K 行（不含前端 + 测试）

---

## 3. Phase 2 — 运营自动化（🚧 进行中）

**目标**：从"回答问题"到"自动跑业务"。

| 里程碑 | 状态 | 关键产物 |
|---|---|---|
| Workflow 引擎 | ✅ | `@workflow` / `@step` + DAG + APScheduler |
| 2 个 workflow 实例 | ✅ | `daily_report`（7 步）/ `inventory_alert`（8 步） |
| 数据采集中心 | ✅ | 5 阶段 Pipeline + 5 套本地数据集 |
| 库存预警 | ✅ | 阈值规则 + 告警中心 + 通知策略 |
| 邮箱真发 | ✅ | SMTP + 前端邮件镜像 |
| Memory 衰减 cron | ✅ 已完成 | Celery beat 周期任务 `memory-daily-decay`（`tasks/celery_app.py`） |
| Workflow LLM 兜底 | 🚧 | router 级已接 LLM 兜底；任务级兜底以代码为准 |
| 报告调度 | 🚧 | 暂只能人工 trigger 或 workflow 包 |
| 数据采集调度 | 🚧 | `Scheduler.start/stop` 仍 NotImplementedError |

### 3.1 Phase 2.5 收尾

- [x] Memory 衰减 cron 接入调度（✅ Celery beat）
- [ ] Workflow LLM 兜底真实化
- [ ] 报告调度入口（`POST /reports/schedule`）
- [ ] 数据采集 `Scheduler.start/stop` 接入 APScheduler
- [ ] 数据采集 Selenium fetcher（缺失）
- [ ] 数据采集 HTTP 增量（当前全量重读）

---

## 4. Phase 3 — 企业平台化（✅ 已完成，2026-09）

**目标**：从"工具"到"平台"。P0 于 2026-09 全部落地（鉴权 008 / 审批门 007 / 部门 041 / 幂等账本 050 / super_admin 054，发布门禁 release.sh + 12 Gate）。

### 4.1 🔴 P0 必做（✅ 全部完成）

| 任务 | 落地结果 |
|---|---|
| 身份鉴权（JWT + 刷新令牌） | ✅ local_jwt + sessions 设备登录 + HttpOnly Cookie 静默续期 |
| users / roles / permissions 表 | ✅ rbac.py + 三端登录页 + 坐席名单制 |
| 行级安全接入鉴权源 | ✅ JWT tenant claim + 网关注入 |
| 审计日志 | ✅ 幂等账本 / 审批单 / Token 用量 / 网关日志 |
| 核心数据 SQLite → PG | ✅ 7 schema × 18 表，SQLite 下线 |
| Migration 治理 | ✅ sql/migrations 编号治理 + 三层校验（Alembic 引入未做） |

### 4.2 🟢 P2 长期工程（持续）

| 任务 | 优先级 |
|---|---|
| OpenTelemetry 接入 | 中 |
| 健康检查深度化 | 低 |
| E2E + 负载测试 | 中 |
| Agent 市场（Skill 共享） | 高 |
| 三方系统集成（ERP / WMS / 财务） | 高 |
| 移动端 / 微信 / 钉钉入口 | 低 |

### 4.3 平台化愿景

```
                ┌──────────────────────────┐
                │  企业智能运营 Agent 平台   │
                └────────────┬─────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   ┌────▼─────┐         ┌────▼─────┐         ┌────▼─────┐
   │  微信/钉钉 │         │ 浏览器/移动│         │  API/SDK  │
   └────┬─────┘         └────┬─────┘         └────┬─────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             ▼
                ┌──────────────────────────┐
                │   Multi-Tenant Gateway   │
                │   (Auth / RBAC / Audit)  │
                └────────────┬─────────────┘
                             ▼
                ┌──────────────────────────┐
                │   Agent / Workflow 市场  │
                └────────────┬─────────────┘
                             ▼
                ┌──────────────────────────┐
                │  数据 / 知识 / 工具集成   │
                └──────────────────────────┘
```

---

## 5. 关键风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| ~~**鉴权缺失无法上线**~~ | ~~上线即被拒~~ | ✅ 已解除（2026-09 JWT + RBAC 落地） |
| ~~**SQLite 数据层不可扩展**~~ | ~~多用户 / 高可用失败~~ | ✅ 已解除（全量收口 PG） |
| **LLM 成本失控** | 商业化失败 | ✅ 预算闭环（租户级）+ Token 看板 + 模型价格双人审核 |
| **LLM 幻觉** | 不可信 | Faithfulness NLI + Evidence Gate 三层拒答 + Citation 强制 |
| **Send[] 并行无并发限流** | OOM / 卡顿 | Supervisor 阶段加 max_concurrent 配置 |
| **Planner 失败兜底单一** | 偶发单步 rag | 增强 fallback，根据关键词判断 SQL 还是 RAG |
| **降级链只有 3 条** | 复杂场景失败率高 | 沉淀降级规则 + 失败时 LLM 兜底 |
| **Workflow LLM 兜底是 stub** | 复杂任务失败 | router 级已接；任务级待真实化 |
| ~~**DB Migration 治理缺失**~~ | ~~团队协作 conflict~~ | ✅ 已解除（sql/migrations 编号治理 + 三层校验） |
| **3 个月提交 364 次** | 文档易滞后 | 每次 PR 强制更新 ARCHITECTURE.md / API.md；2026-09-29 已立归档与索引维护约定 |

---

## 6. 进度跟踪

### 6.1 已完成项（Phase 1 全部 + Phase 2 主体）

- ✅ RAG 全链路（索引 + 检索 + 拒答 + 校验）
- ✅ Multi-Agent 编排 + 9 Capability
- ✅ SQL Agent 6 层安全
- ✅ Workflow 引擎 + 2 实例
- ✅ 报告生成 + 邮件推送
- ✅ 数据采集 5 阶段
- ✅ 库存预警 + 告警
- ✅ 可观测性 Trace + 14 前端组件
- ✅ 稳定性（重试 / 降级 / 限流 / 熔断 / backpressure）

### 6.2 进行中（Phase 2 收尾）

- 🚧 Workflow LLM 兜底（任务级）
- 🚧 报告调度
- 🚧 数据采集调度

### 6.3 当前剩余（2026-09-29 口径）

Phase 3 平台化 P0 与垂直域生产收口（5 域图 / 坐席工作台 / 幂等 / 异步任务运行时 / 发布门禁 / Final RC 7336 用例）均已完成。当前剩余：

- 📋 RBAC super_admin 两级授权 + 撤权即时吊销（migration 054 已入未推广）
- 📋 RAG 评测基线在现行模型栈重建（fixture 重 ingest → 双跑 → 门禁刷新）
- 📋 旅游预订真实供应商接入
- 📋 灰度放量机制 + 真实故障演练（发布前门禁类）
- 📋 架构减法 STOP E / STOP M
- 📋 密钥管理接入 KMS
- 📋 数据质量规则引擎 / 数据血缘
- 📋 OpenTelemetry 接入 / 健康检查深度化
- 📋 Agent 市场 / 三方系统集成（ERP / WMS / 财务）/ 移动端入口

---

## 验证

最后验证：2026-10-03 · P0/Phase3 状态逐项对照代码与迁移目录核实（celery beat、scheduler、routes 计数实测；迁移编号订正至 069）；规模口径以根 [README.md](../README.md)「系统规模」为唯一权威。

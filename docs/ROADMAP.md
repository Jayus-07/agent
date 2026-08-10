# ROADMAP — 迭代规划与现状差距

> 本文档聚焦"现状距企业级还差什么 + 分 3 期怎么补"。
> 配套阅读：[PRD.md](PRD.md) / [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 1. 现状差距矩阵

按 P0/P1/P2 三档排序，**P0 = 不补不能上线**，**P1 = 影响规模化**，**P2 = 长期工程债务**。

### 🔴 P0 — 不补不能上线

| 维度 | 现状 | 与企业级差距 | 修复路径 |
|---|---|---|---|
| **身份鉴权** | 单一全局 API Key（默认空 = 全开放）；前端根本不发 key | OIDC / JWT + 刷新令牌 + 会话管理 | FastAPI Depends + JWT 中间件 → `current_user` → `user_id = JWT.sub` |
| **RBAC / 3 类角色** | 完全不存在（无 users/roles/permissions 表、无登录页、菜单硬编码） | 角色 → 权限 → 资源三级 + 前后端双向校验 | 新增 users / roles / permissions 表 + 登录页 + Sidebar 动态化 |
| **多租户 / 数据隔离** | `row_security.py` 实现正确，但 `current_user_id` 来自 HTTP body → 可越权 | 隔离参数由服务端鉴权上下文注入 | 接上 JWT 即可生效（机制已就绪） |
| **审计日志** | 无（仅记 IP + User-Agent） | 谁 / 何时 / 操作 / 资源 / Token 消耗 ≥ 180 天 | 新增 audit_log 表 + 统一 middleware |
| **核心数据存 SQLite** | 告警 / 报告 / 工作流 / Trace / 文档 / chunk 全在 14 个 SQLite 单文件 | PostgreSQL 统一 + 备份 + 主从 | 迁移 PG backup 文件 |

**🔑 关键判断**：**鉴权不是"补一个登录页"，而是解锁 3 件事的钥匙**：

1. 审计日志（无身份不可审计）
2. 多租户隔离（row_security 已就绪）
3. Trace 的 user 维度（无法做用户级分析）

三者当前全部因无可信身份而无法落地。建议作为**捆绑价值主张**而非 4 个独立需求。

### 🟡 P1 — 影响规模化

| 维度 | 现状 | 与企业级差距 | 修复路径 |
|---|---|---|---|
| **Migration 治理** | 裸 SQL + 手工 psql，无版本表，003 编号重复，无回滚 | Alembic + 版本表 + 可回滚 + CI 校验 | 引入 Alembic + 重新打 baseline |
| **数据模型文档** | `database.md` 41 行，仅分层规范，无表结构 | ER 图 + 数据字典 + 血缘 | 已迁到 [DATABASE.md](DATABASE.md) 200+ 行 |
| **密钥管理** | `.env` 明文存密码；只读库密码硬编码默认值 | Vault / Secrets Manager | 接入外部 KMS |
| **前端数据层** | 手写 `useState+useEffect`；`react-query` 装但未用；两套 API 客户端（`lib/api/*` vs `services/*`）路径不一致 | 统一客户端 + 缓存 + 重试 + 乐观更新 | 改造 services/ → 走 fetcher 抽象 + 接 react-query |
| **前端错误语义** | observability 静默吞错返回 `[]`；alerts 严格区分 | 全局统一：故障 ≠ 空数据 | 统一 error boundary + 自研 error reporter |
| **Workflow LLM 兜底** | `_llm_fallback` 是 stub | 真实 LLM 兜底 | Phase 5 路线图 |

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
| Memory 衰减 cron | 🚧 80% | `MemoryService.run_decay` 存在但无 scheduler 入口 |
| Workflow LLM 兜底 | 🚧 30% | `_llm_fallback` 是 stub |
| 报告调度 | 🚧 | 暂只能人工 trigger 或 workflow 包 |
| 数据采集调度 | 🚧 | `Scheduler.start/stop` 仍 NotImplementedError |

### 3.1 Phase 2.5 收尾（约 1-2 周）

- [ ] Memory 衰减 cron 接入 APScheduler
- [ ] Workflow LLM 兜底真实化
- [ ] 报告调度入口（`POST /reports/schedule`）
- [ ] 数据采集 `Scheduler.start/stop` 接入 APScheduler
- [ ] 数据采集 Selenium fetcher（缺失）
- [ ] 数据采集 HTTP 增量（当前全量重读）

---

## 4. Phase 3 — 企业平台化（📋 待启动）

**目标**：从"工具"到"平台"，支持多用户、多租户、多系统集成。

### 4.1 🔴 P0 必做（不补不能上线）

| 任务 | 工作量 | 依赖 |
|---|---|---|
| 身份鉴权（JWT + 刷新令牌） | 大 | — |
| users / roles / permissions 表 | 中 | 鉴权 |
| 行级安全接入鉴权源 | 小 | 鉴权 |
| 审计日志 | 中 | 鉴权 |
| 核心数据 SQLite → PG | 大 | — |
| Migration 治理（Alembic） | 中 | — |

**预计**：2-3 个月完成所有 P0

### 4.2 🟡 P1 规模化（4 周）

| 任务 | 工作量 |
|---|---|
| 前端数据层统一（react-query） | 2 周 |
| 全局错误语义统一 | 1 周 |
| 密钥管理接入 KMS | 1 周 |
| 数据质量规则引擎 | 2 周 |

### 4.3 🟢 P2 长期工程（持续）

| 任务 | 优先级 |
|---|---|
| OpenTelemetry 接入 | 中 |
| 健康检查深度化 | 低 |
| E2E + 负载测试 | 中 |
| Agent 市场（Skill 共享） | 高 |
| 三方系统集成（ERP / WMS / 财务） | 高 |
| 移动端 / 微信 / 钉钉入口 | 低 |

### 4.4 平台化愿景

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
| **鉴权缺失无法上线** | 上线即被拒 | P0 立项，机制已就绪（row_security + JWT） |
| **SQLite 数据层不可扩展** | 多用户 / 高可用失败 | P0 立项，迁移 PG backup 文件 |
| **LLM 成本失控** | 商业化失败 | rate_limit + 成本埋点 + 缓存 + Self-Correction 上限 |
| **LLM 幻觉** | 不可信 | Faithfulness NLI + Evidence Gate 三层拒答 + Citation 强制 |
| **Send[] 并行无并发限流** | OOM / 卡顿 | Supervisor 阶段加 max_concurrent 配置 |
| **Planner 失败兜底单一** | 偶发单步 rag | 增强 fallback，根据关键词判断 SQL 还是 RAG |
| **降级链只有 3 条** | 复杂场景失败率高 | 沉淀降级规则 + 失败时 LLM 兜底 |
| **Workflow LLM 兜底是 stub** | 复杂任务失败 | Phase 2.5 真实化 |
| **DB Migration 治理缺失** | 团队协作 conflict | 引入 Alembic + 打 baseline |
| **3 个月提交 364 次** | 文档易滞后 | 每次 PR 强制更新 ARCHITECTURE.md / API.md |

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

- 🚧 Memory 衰减 cron
- 🚧 Workflow LLM 兜底
- 🚧 报告调度
- 🚧 数据采集调度

### 6.3 待启动（Phase 3）

- 📋 鉴权 / RBAC / 多租户 / 审计日志
- 📋 SQLite → PG 迁移
- 📋 Alembic Migration 治理
- 📋 前端数据层统一
- 📋 Agent 市场
- 📋 三方系统集成

---

## 验证

最后验证：2026-08-10 · 与代码一致（gaps 来自 3 个 Explore agent 调研 + production-readiness.md）。

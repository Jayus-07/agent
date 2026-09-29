# 项目文档总索引

> **电商 RAG + Multi-Agent 平台** 的文档总入口。
> 文档随代码一同演进，最后验证：2026-09-28。
>
> ⚠️ **口径提示**：`docs/` 历史上堆积过大量**带日期的交接 / 收口 / 审计报告**（2026-09-29 已归档
> 146 个至 [archive/](archive/)），它们是历史事实记录，**不随后续代码演进回写**。引用系统规模、
> 架构口径时，以根 [README.md](../README.md) 的「系统规模」表与 [AGENTS.md](../AGENTS.md) 为
> 唯一权威；本文件只负责导览。

---

## 1. 顶层 8 个文档（PRD + TRD + 设计规范，新人入口）

| 文档 | 内容 |
|---|---|
| [PRD.md](PRD.md) | 业务背景 / 目标 / 用户角色 / 8 大功能需求 / 非功能 / 当前状态 / 后续规划（2026-09-29 刷新） |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 顶层架构图 + 子系统地图 + 关键数据流 + 设计原则 |
| [DESIGN.md](DESIGN.md) | 前端三端设计规范：设计 token / 布局骨架 / 页面清单 / 组件与四态规范 / 独特约定与反模式（2026-09-29 新建） |
| [RAG_DESIGN.md](RAG_DESIGN.md) | RAG 流水线 + 索引链路 + Evidence Gate + Faithfulness |
| [AGENT_DESIGN.md](AGENT_DESIGN.md) | Multi-Agent 编排 + Workflow 引擎 + AgentState（capability/节点口径见根 README） |
| [DATABASE.md](DATABASE.md) | 业务库 schema + agent_memory + Migration（SQLite 散落存储已于 2026-09 收口下线） |
| [API.md](API.md) | 路由 / 端点 + 鉴权 + SSE 协议 + DTO + 错误处理 |
| [ROADMAP.md](ROADMAP.md) | 现状差距矩阵 + 路线图 + 关键风险（P0/Phase3 已于 2026-09 关闭，剩余见 §6.3） |

> 8 份均已按 2026-09-29 口径校准（数量类以根 README「系统规模」为唯一权威，文档内不复抄）。

---

## 2. 关键深读文档

| 文档 | 用途 |
|---|---|
| [architecture/Architecture-Baseline.md](architecture/Architecture-Baseline.md) | **架构基线（STOP H 冻结版）**：Runtime 九层分层 / 请求生命周期 / 数据流 / 权威口径指针 |
| [architecture/Extension-Guide.md](architecture/Extension-Guide.md) | 扩展指南：新增 Domain / Capability / Skill / Tool / Workflow / MCP 的流程与验证门 |
| [architecture/Frozen-Contracts.md](architecture/Frozen-Contracts.md) | 冻结契约清单：SSE / checkpoint / node id / route_mode / ToolRuntime / Tool Contract / MCP boundary 等红线、守卫与变更流程 |
| [architecture/system-overview.md](architecture/system-overview.md) | 部署拓扑 / 端口表 / 异步层 / 网关认证（承接 README 收敛出的部署细节） |
| [architecture/ai-runtime.md](architecture/ai-runtime.md) | 主图节点职责 / 域图细节 / 客服锁域 / 跨轮状态契约（承接 README 收敛出的编排细节） |
| [architecture/domain-service-map.md](architecture/domain-service-map.md) | 域图 × 专家 × 工具 × 第三方服务 × 凭据地图：每个专家用什么、调什么外部服务、凭据怎么管、挂了怎么降级（2026-09-29 新建） |
| [architecture/adr/0001-merge-dual-registry.md](architecture/adr/0001-merge-dual-registry.md) | 决策：合并双注册表 |
| [architecture/adr/0002-ragchain-decomposition.md](architecture/adr/0002-ragchain-decomposition.md) | 决策：RAGChain 拆解 |
| [architecture/adr/0003-directory-layering.md](architecture/adr/0003-directory-layering.md) | 决策：目录分层规范 |
| [decisions/auth-decision.md](decisions/auth-decision.md) | 鉴权方案（自建 JWT，`security/local_jwt.py`） |
| [contracts/identity-header-protocol.md](contracts/identity-header-protocol.md) | APISIX 网关注入身份头（X-User-Id）契约 |
| [observability/trace-model.md](observability/trace-model.md) | Trace 数据模型（SpanKind 枚举） |
| [operations/commands.md](operations/commands.md) | 运维命令（高频查询） |
| [operations/troubleshooting-checklist.md](operations/troubleshooting-checklist.md) | 故障排查（高频查询） |

---

## 3. 目录结构与规模

```
docs/
├── PRD.md / ARCHITECTURE.md / DESIGN.md / RAG_DESIGN.md / AGENT_DESIGN.md
├── DATABASE.md / API.md / ROADMAP.md      ← 顶层 8 文档（新人入口）
├── architecture/adr/    ← ADR 决策记录
├── contracts/           ← 跨端契约（身份头协议等）
├── customer-service/    ← 客服域专项（refactor-plan / target-architecture / 五剧本）
├── decisions/ auth/     ← 技术决策与鉴权
├── observability/ operations/  ← Trace 模型 / 运维命令 / 排查清单
├── reports/ evidence/ coordination/ features/ feasibility/
├── prompt-management/ prompt/ team-prompts/ rag_eval/ samples/
├── archive/
│   ├── 2026-09/         ← 已归档的过程报告（128 个：102 带日期 + 26 非日期，只读）
│   ├── superpowers/     ← AI 工作计划 / 设计规格归档（37 个）
│   └── reports/         ← 已完结的脚本验收输出
└── 其余根目录散文件      ← 被源码注释 / 脚本 / AGENTS.md 点名引用的规格、审计、交接文档
```

全目录约 **370 个文件**，其中归档区 [archive/](archive/) 约 180 个（历史事实记录，只读）。
找最新状态：先看根 [README.md](../README.md) 与 [AGENTS.md](../AGENTS.md)，再看下方「近期专项」。

---

## 4. 关键概念速查（2026-09-28 对齐）

- **Multi-Agent 编排**：主图 9 核心节点（router / tool_selector / skill_executor / workflow_executor / planner / critique / supervisor / general_chat / reporter），LangGraph 实现
- **RAG 链路**：改写 → MultiQuery → 混合检索（Vector+BM25 → RRF）→ Rerank → Evidence Gate → 带引用生成 → META 尾拒答
- **3 层记忆**：L1 短期 / L2 会话（PG）/ L3 长期（PG + pgvector）
- **Capability DAG**：17 capability（3 个 `routed:false` 内部能力），`capabilities.yaml` 为唯一事实源
- **域图**：5 个（客服 / 旅游 / 选品漏斗 / 旅游商务 / 旅游预订），代码默认全关
- **Trace 体系**：每请求一棵 span 树，SpanKind 枚举强约束（阶段清单见 `observability/tracer.py`，不在此手抄）
- **Evidence Gate**：Retrieval / Rerank / Faithfulness 三层拒答 + Self-Correction
- **SSE 协议**：meta / status / log / delta / done / error

---

## 5. 维护约定

1. **新文档必须归档到对应目录**；过程类交接 / 收口报告按 `YYYY-MM-DD-主题.md` 命名放 `reports/`，**不要堆根目录**；完结后移入 `archive/<年-月>/`（2026-09-29 已归档一批，`git mv` 保留血缘）
2. **PRD / ARCHITECTURE / DESIGN 等 8 个顶层文档** 是新人入口，**必须与代码同步更新**；滞后口径以上方「口径提示」为准
3. **重大决策** 写 ADR → `architecture/adr/NNNN-xxx.md`
4. **数量 / 规模类口径** 只维护根 README「系统规模」表与 AGENTS.md，禁止在第二处手抄（G2）
5. **被源码注释 / 测试 / AGENTS.md 按路径引用的文档不移动**；确需移动时必须同步改引用方

---

## 6. 相关索引

- 项目根 [AGENTS.md](../AGENTS.md) — 项目级硬约束与架构知识（**改代码前先读**，唯一入口；根 `CLAUDE.md` 为指向它的薄指针，2026-09-29 起废止双维护）
- 根 [README.md](../README.md) — 系统规模实测口径 + 快速开始
- 根 [命令文档.md](../命令文档.md) — 启停 / 评测 CLI 速查
- [HANDOFF.md](HANDOFF.md) — 会话交接记录
- 个人全局配置与跨会话记忆：用户级 ZCode 配置目录（`~/.zcode/`）

---

## 7. 近期专项（2026-09）

**收官与盘点（最新）**

- [2026-09-25-五线计划书进度盘点-未完成与遗漏项汇总.md](2026-09-25-五线计划书进度盘点-未完成与遗漏项汇总.md) — 客服/旅游/记忆/上下文/代码审查五线收口状态 + 全局遗漏（**最新欠账口径**）
- [2026-09-25-FinalRC-TestDebt-Closure.md](2026-09-25-FinalRC-TestDebt-Closure.md) — 全量回归收官：分块执行闭合、flaky 甄别（89 persistent + 85 env_flaky）
- [2026-09-25-FinalRC-TestDebt-Baseline.md](archive/2026-09/2026-09-25-FinalRC-TestDebt-Baseline.md) — 回归基线三件套口径（PYTHONPATH + PGPORT=5433 + 分块）
- [gateway-apisix-final-report.md](gateway-apisix-final-report.md) — APISIX 网关迁移收官与实测踩坑
- [java-side-handover.md](java-side-handover.md) — Java 侧（Enterprise_OA）割接清单

**生产收口系列（2026-09-23 ~ 09-25）**

- 幂等运维（OPERATIONAL_FROZEN）、Task Runtime（Phase1/2 全链路 FROZEN）、Memory 收口、Context Budget 收口、Model Governance 收口、SQL Agent 收口（STOP A-D+D0）、旅游线 STOP I/J/K/L（质量 / Provider / 商务 / 预订）——过程报告见 [archive/2026-09/](archive/2026-09/) 与 [HANDOFF.md](HANDOFF.md)

**智能客服优化（2026-09-19 规划波次）**

- [2026-09-19-智能客服Agent优化方案-规划稿.md](archive/2026-09/2026-09-19-智能客服Agent优化方案-规划稿.md) — 客服上线级优化的实施依据（P0~P6 分阶段）
- [2026-09-19-智能客服优化-P0基线报告.md](archive/2026-09/2026-09-19-智能客服优化-P0基线报告.md) — 未提交改动 176 项所有权清单 + 配置快照
- 客服域既有文档：[customer-service/](customer-service/)（refactor-plan / target-architecture / REFACTOR-TASK-SPEC 五剧本）

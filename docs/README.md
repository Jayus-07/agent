# 项目文档总索引

> **电商 RAG + Multi-Agent 平台** 的文档总入口。
> 文档随代码一同演进，最后验证：2026-08-10。

---

## 1. 顶层 7 个文档（PRD + TRD 融合，推荐先读）

| 文档 | 内容 |
|---|---|
| [PRD.md](PRD.md) | 业务背景 / 目标 / 用户角色 / 4 大功能需求 / 非功能 / 当前状态 / 后续规划 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 顶层架构图 + 5 大子系统地图 + 关键数据流 + 设计原则 |
| [RAG_DESIGN.md](RAG_DESIGN.md) | RAG 6 段流水线 + 索引链路 + Evidence Gate + Faithfulness |
| [AGENT_DESIGN.md](AGENT_DESIGN.md) | Multi-Agent 5 节点 + 9 Capability + Workflow 引擎 + AgentState |
| [DATABASE.md](DATABASE.md) | 7 schema × 18 表 + agent_memory + 14 SQLite 散落 + Migration |
| [API.md](API.md) | 22 路由 ~90 端点 + 鉴权现状 + SSE 协议 + DTO + 错误处理 |
| [ROADMAP.md](ROADMAP.md) | 现状差距矩阵 + 3 期路线图 + 关键风险 |

> 5 分钟看完 → 子目录深读 7 个关键文档。

---

## 2. 7 个关键深读文档

| 文档 | 用途 |
|---|---|
| [architecture/adr/0001-merge-dual-registry.md](architecture/adr/0001-merge-dual-registry.md) | 决策：合并双注册表 |
| [architecture/adr/0002-ragchain-decomposition.md](architecture/adr/0002-ragchain-decomposition.md) | 决策：RAGChain 拆解 |
| [architecture/adr/0003-directory-layering.md](architecture/adr/0003-directory-layering.md) | 决策：目录分层规范 |
| [decisions/auth-decision.md](decisions/auth-decision.md) | 鉴权方案（**Phase 3 P0 关键**） |
| [observability/trace-model.md](observability/trace-model.md) | Trace 数据模型（SpanKind 枚举） |
| [operations/commands.md](operations/commands.md) | 运维命令（高频查询） |
| [operations/troubleshooting-checklist.md](operations/troubleshooting-checklist.md) | 故障排查（高频查询） |

---

## 3. 目录结构

```
docs/
├── PRD.md                  ← 产品需求
├── ARCHITECTURE.md         ← 架构总览
├── RAG_DESIGN.md           ← RAG 全链路
├── AGENT_DESIGN.md         ← Multi-Agent + Workflow
├── DATABASE.md             ← 数据库设计
├── API.md                  ← 接口设计
├── ROADMAP.md              ← 迭代规划
├── README.md
│
├── architecture/adr/       ← 3 个 ADR（决策记录）
├── decisions/              ← 鉴权方案
├── observability/          ← Trace 数据模型
└── operations/             ← 运维命令 + 故障排查
```

**15 个 .md 文档**（7 顶层 + 7 关键顶层入口 + 1 README）。

---

## 4. 关键概念速查

- **Multi-Agent 编排**：Planner → Critique → Supervisor → Skills → Reporter，LangGraph 实现
- **RAG 6 段流水线**：HistoryAware → MultiQuery → ChunkLevel(Hybrid RRF) → Adaptive → Rerank → LLM Generate
- **3 层记忆**：L1 短期 / L2 会话（PG）/ L3 长期（PG + pgvector）
- **Capability DAG**：Skill 执行图，9 个 Capability
- **Trace 体系**：每个请求一棵 span 树，存 TraceCollector + SQLite
- **Evidence Gate**：Retrieval / Rerank / Generation 三层拒答 + Self-Correction
- **SSE v2 协议**：meta / status / log / delta / done / error

---

## 5. 维护约定

1. **新文档必须归档到对应目录**，不要散落到根目录
2. **PRD / ARCHITECTURE 等 7 个顶层文档** 是新人入口，**必须与代码同步更新**
3. **重大决策** 写 ADR → `architecture/adr/NNNN-xxx.md`
4. **代码变更** 同步更新 7 个顶层文档中的对应章节

---

## 6. 相关索引

- 项目根 [CLAUDE.md](../CLAUDE.md) — 项目级约束与架构知识
- 个人全局配置：用户级别的 `~/.claude/CLAUDE.md` — 跨项目偏好
- 跨会话记忆：用户级别的 `~/.claude/projects/<project>/memory/MEMORY.md` — 按项目分类的会话记忆

# Architecture Simplification — STOP H 收官：Architecture Final Documentation & Baseline Freeze

日期：2026-09-29
基线：`main@e4613db`（STOP G 已结案）
性质：纯文档阶段（零生产代码改动）。架构减法八步（STOP A-H）全部收官，自此进入**平台维护阶段**：功能迭代以本套基线文档为分层事实参考。

结论先行：

```text
ARCHITECTURE_SIMPLIFICATION_PASS=true
STOP_H_PASS=true
PRODUCTION_ARCHITECTURE_BASELINE_FROZEN=true
```

---

## 1. 最终架构（Runtime 九层）

```text
Agent Platform
├ Router Runtime            ── 每请求拍板去向（域预过滤 + 三层路由）      orchestration/graph/router_node.py · orchestration/router/
├ Domain Runtime            ── 3 顶级域 / 5 物理域图                     domains/ · customer_service/ · travel/ · selection_funnel/
├ Capability Runtime        ── capability→Skill 执行调度                 capabilities.yaml(SSOT) · direct_executor.py · skills/registry.py
├ Plan Runtime              ── 复杂请求任务拆解与并行调度                orchestration/graph/（plan 支线链）
├ Expert Runtime            ── 专家节点公共生命周期（STOP F）             core/node_runtime/ · 双域 experts/base.py
├ Tool Contract Boundary    ── 两型输出契约 + 边界归一（STOP G）          shared/tool_envelope.py · skills/base.py · skills/validation.py
├ Tool Runtime              ── 执行治理（超时/重试/熔断/隔离舱）          core/tool_runtime/
├ Integration Adapter       ── MCP 第二出口（2 server / 5 tool）          mcp_servers/
└ Shared Governance         ── 认证·记忆·上下文预算·模型治理·幂等·可观测·评测  security/ memory/ context_budget/ infra/llm observability/ evaluation/
```

完整版（架构图 / 请求生命周期时序 / 四条数据流 / 权威口径指针）：**[Architecture-Baseline.md](Architecture-Baseline.md)**。

## 2. STOP A-H 状态总览

| STOP | 内容 | 结案提交 | 标志 |
|---|---|---|---|
| A | 只读基线审计 | fef0f41 | STOP_A_PASS=true |
| B | Router 合并收敛（三 router 归一 + trace 元数据） | 984a9ba → 39b3c8c 等 | STOP_B_PASS=true |
| C | Capability SSOT（manifest 单源） | 39b3c8c | STOP_C_PASS=true |
| D | 业务实体唯一守卫 | addaa32 | STOP_D_PASS=true |
| E | Domain 语义边界收口 | 8ecf7fc | STOP_E_PASS=true |
| F | Expert Runtime（core/node_runtime 六段生命周期 + 双专家 base 内部收敛） | c2dc0a4 | STOP_F_PASS=true / NODE_RUNTIME_CONTRACT_PASS=true |
| G | Tool Contract（两型契约显式化 + unwrap_envelope 收敛 + map.lookup 裸 JSON 修复） | e4613db | STOP_G_PASS=true / TOOL_CONTRACT_PASS=true |
| H | 架构基线文档 + 冻结（本 STOP） | 本提交 | STOP_H_PASS=true / PRODUCTION_ARCHITECTURE_BASELINE_FROZEN=true |

**八步四条主线全部命中要害**：Router 没乱（B）、Capability 没双源（C）、Domain 没物理乱合并（E）、Expert 没过度抽象（F——只抽六段生命周期不做万能 Runtime）、Tool 没强行统一（G——两型契约而非单一 ToolResult）。

## 3. 本 STOP 交付物（全部纯文档）

| 文件 | 内容 |
|---|---|
| `docs/architecture/Architecture-Baseline.md`（新增） | 总体架构图 / Runtime 九层职责与代码落点 / 请求生命周期时序 / 四条数据流 / 扩展与冻结指针 / 权威口径 |
| `docs/architecture/Extension-Guide.md`（新增） | 新增 Domain / Capability / Skill / Tool / Workflow / MCP 的决策要点、接线流程、验证门（含 STOP G 两型契约选型表） |
| `docs/architecture/Frozen-Contracts.md`（新增） | 十一大冻结契约簇（LangGraph 拓扑 / checkpoint / Router / Capability SSOT / ToolRuntime / Tool Contract / Expert Runtime / Trace·SSE·API / Domain / MCP / 平台铁律），逐条含守卫测试与变更流程 |
| `README.md`（更新） | 图 2 支线表述对齐 Runtime 命名；图 3 由「主图节点明细」替换为 **Runtime 九层分层图**；「Multi-Agent 编排」改为用户视角描述（不再展示 critique/supervisor/节点清单等内部细节）；术语表补九层 Runtime 词条；文档索引补三份新文档。业务能力（企业知识问答 / NL2SQL 数据分析 / 客服 / 旅游规划 / 智能选品漏斗 / Workflow）章节保留原有用户视角表述 |
| `docs/README.md`（更新） | 「关键深读文档」表置顶三份新基线文档 |

## 4. 冻结边界

全部红线收敛至 **[Frozen-Contracts.md](Frozen-Contracts.md)**（11 簇：LangGraph 拓扑 / checkpoint / Router Runtime / Capability SSOT / Tool Runtime / Tool Contract Boundary / Expert Runtime / Trace·SSE·API / Domain Runtime / MCP Boundary / 平台铁律 G1-G4）。每条含冻结理由、守卫测试与统一变更流程（新 ADR + 例外台账 + 消费方原子迁移 + 兼容期；只向后兼容演进可直接进行但须跑守卫）。

## 5. 扩展规范

**[Extension-Guide.md](Extension-Guide.md)**：五类资产的入口指南（Tool 两型契约选型 → Skill 五处接线含 output_type 显式声明 → Workflow 三处 → Domain 六要点含 prefilter 接线 → MCP 四要点），通用验证四件套命令，变更分级速查（普通资产 / 触碰冻结契约 / 口径校准）。上游细则仍以操作手册为唯一事实源（G2 不复抄）。

## 6. 验证记录（2026-09-29 实跑）

| 项 | 结果 |
|---|---|
| mermaid 语法校验（README 3 图 + system-overview + Architecture-Baseline 2 图，扩展脚本实跑） | **6/6 PASS** |
| registry 一致性三件套（registry/layer/adr0001） | **37 passed** |
| evaluation 冒烟（tests/evaluation 全套） | **163 passed, 112 skipped** |

按 spec 未重跑大规模回归（STOP F/G 已各有全量记录：CS 1012 / orchestration+runtime 757 / skills 119 等）。

## 7. Deferred 列表（架构冻结后独立评审，均非阻塞）

| # | 项 | 来源 | 备注 |
|---|---|---|---|
| 1 | CS 专家补 span | STOP F 风险3 | trace 形态变更，须独立评审 |
| 2 | TraceMiddleware 内部改用 NodeRunner | STOP F 审计 §6-2 | 前提 = span 形态逐字节一致 |
| 3 | `sql_query_tool` 混型卫生修正（成功 Markdown/失败封套 → 统一 text） | STOP G 挂账 | 属 Tool 函数体修改，独立批次 |
| 4 | semantic 失败的 step 级 error 携带可读 reason | STOP G 发现 | 既有粗口径「输出校验失败: semantic」，错误呈现增强 |
| 5 | T25 冻结集漏 `qweather_backup` | f4f9628 遗留 | 一行断言补充 |
| 6 | 旅游域重构 Phase 1+（BaseAgent 契约 / 零 LLM 主链换真实数据源） | 功能重构线（与架构减法线并行立项） | 设计稿已冻结，待实施；在冻结契约内进行 |
| 7 | RAG 评测 fixture 库重建（rag_eval_kb 已清） | 评测债 | 重跑评测前必须重新 ingest |

## 8. 最终判定

```ini
ARCHITECTURE_SIMPLIFICATION_PASS=true
STOP_H_PASS=true
PRODUCTION_ARCHITECTURE_BASELINE_FROZEN=true
NEXT=功能迭代（以 Architecture-Baseline 为分层基线、Frozen-Contracts 为红线、Extension-Guide 为扩展入口）
```

最后验证：2026-09-29。

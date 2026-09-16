# Agent / Skill / Tool / MCP 四层设计规范

- **版本**：v1.3（2026-09-16）· v1.2 → v1.3 修订：§5 域图补 2 步接线（prefilter + `router_node.py` 硬编码链路）、§5 顶部加操作手册入口（见 §7 #19）
- **历史版本**：v1.2（2026-09-16）· v1.1 → v1.2 修订：§4 补 E9（`routed:false` 对 Planner 不生效）、§8 加 P2 跟进项、`capabilities.yaml` 头部补「反直觉设计」备忘并更正 2 处不实的 `reason`（见 §7 #16–18）
- **适用范围**：`backend/` 下所有 Agent 编排、Skill、Tool、MCP Server、Workflow 的新增与修改
- **强制力**：§6 列出的规则由 pytest 守护，违反即测试失败；§4 例外台账内的条目是**登记在案**的偏离，不是可以随意复制的先例
- **配套**：`docs/agent-platform-add-domain-graph` 相关技能（新增域图）、`docs/2026-09-16-总交接与实施计划.md`（全局待办）

---

## §0 为什么要有这份规范

四层原本是「分层统一、层内分化」：越靠下越统一（MCP > Tool > Skill），越往上越松。
这是演进顺序的必然结果（底层经 ADR-0001 统一重构过，上层随客服域、旅游域、业务 agent 分批长出），
但已实际产生四次静默漂移，全部是「一处声明、多处手写」导致的：

| # | 事件 | 后果 |
|---|---|---|
| 1 | `map_lookup_tool` 定义在 `skills/map/skill.py` 且从未注册 | 34 个 Tool 里唯一从注册表消失的，无人发现 |
| 2 | `selection_decision` workflow 只在 `app/server.py` 注册、不在 `capabilities.yaml` | 向量路由对它失明，用户提问选不中 |
| 3 | `MarketResearch` 在 manifest 里，但 `evaluation/runners/e2e.py` 手写的注册列表漏了它 | 评测侧认为该 workflow 不存在 |
| 4 | `competitor.analyze`（历史）已注册 Skill、可被规则路由，却被 LLM Router 拒绝 | 三方漂移，已由 manifest 化收口 |

结论：**同一件事只允许有一个声明处，其余全部派生**。这条原则是本规范的全部内容。

---

## §1 四层职责与调用方向

```
Planner / Supervisor            ┐
   │ 产出 plan(capability, params)│  Agent 层：决定「做什么」
   ▼                             ┘
capability ──→ Skill            ┐
（manifest 声明）   │             │  Skill 层：能力契约 + 重试/超时/输出归一化
                   ▼             ┘
                 Tool           ┐
                   │             │  Tool 层：一次确定性调用（LangChain @tool）
                   ▼             ┘
            Infrastructure      ┐
   （RAG / SQL / 腾讯 LBS / SMTP）│  基础设施：无业务语义
                                ┘
MCP = Tool 的对外协议封装（不是第 5 层，是 Tool 的第二个出口）
Workflow = 多个 Skill/Tool 的静态编排（DAG），与 capability 并列供路由选择
```

方向铁律：**上层调用下层，下层不知道上层存在**。
Tool 不得 import Skill；Skill 不得 import Planner；Infrastructure 不得 import 以上任何一层。

### 事实源清单（改之前先看这里）

| 层 | 声明处 | 派生/执行处 |
|---|---|---|
| Agent | `orchestration/graph/builder.py`（主图 8 节点，见 §3.1） | `orchestration/graph/system.py` |
| 域包 | `backend/<domain>/register.py`（`DomainGraph` 四元组，域代码与主图同级的独立包） | 由下面的触发器 import → builder 自动布线 |
| 域触发器 | `backend/domains/__init__.py`（**每新增一域加一行 import**，当前 2 行）<br>⚠️ `backend/domains/` 只是触发器，**不含任何域代码**；域代码在 `backend/travel/`、`backend/customer_service/` | builder 自动布线（节点 + 条件边 + 直连 END） |
| Skill | `skills/<name>/skill.py`（类声明）+ `skills/registry.py`（实例登记） | `orchestration/tool_registry.py` 派生 `CAPABILITY_MAP` |
| Capability 路由 | `orchestration/router/capabilities.yaml` | `router/manifest.py` → `types.py` / `vector_router.py` / `llm_router.py` |
| Tool | `backend/tools/<mod>.py`（`@tool` + 底部 `tool_registry.register`） | `tools/tool_registry.py` |
| MCP | `mcp_servers/servers/<name>.py`（`MCPServer` 子类） | `mcp_servers/servers/__init__.py::register_all()` |
| Workflow | `orchestration/workflows/<name>.py`（`@workflow` meta） | `orchestration/workflows/__init__.py::register_all()` |

**当前规模（2026-09-16）**：12 Skill / 17 capability（其中 3 个 `routed:false` 内部 —— 注意该标记**只约束路由层**，Planner 仍可见，见 §4 E9）/ 34 Tool /
4 workflow / 2 域图（客服 9 节点、旅游 9 节点）/ 主图 8 核心节点 / 内置 MCP 2 server 5 tool。

---

## §2 全局治理原则

| 编号 | 原则 | 落地方式 |
|---|---|---|
| **G1** | 声明式注册，启动期派生，fail-fast | 声明不合法就抛异常（`ManifestError` / `TypeError`），不静默降级 |
| **G2** | 单一事实源（SSOT） | 每类东西只有一个声明处；派生量禁止手写回去，由测试反向锁死 |
| **G3** | 模块自注册 | 谁定义谁注册（`__init__.py` 或模块底部）；禁止集中代注册；容器级用 `register_all()` |
| **G4** | 例外必须登记 | 做不到统一的，必须在 §4 台账登记理由与处置，不得口头约定 |

---

## §3 各层规范

### §3.1 Agent 层

**主图核心节点固定 8 个**，顺序与命名不得随意改动（`builder.py`）：

`router` · `tool_selector` · `skill_executor` · `workflow_executor` · `planner` · `critique` · `supervisor` · `reporter`

Skill 节点与域图节点由自动发现加入，**不得手写进 builder**：

- Skill 节点：`tool_registry.get_skill_nodes()` → 完成边回 `supervisor`
- 域图节点：`domain_graph_registry.get_all()` → 自带 reporter，直连 `END`

**新增域图的唯一写法**（参考 `travel/register.py`、`customer_service/register.py`）：

```python
# <domain>/register.py
domain_graph_registry.register(DomainGraph(
    name="<domain>",
    node_name="<domain>_graph_node",
    label="<中文说明>",
    adapter=<domain>_graph_node,
))
```
然后在 `backend/domains/__init__.py` 加一行 import。**不要改 `builder.py`。**

域图内部结构约定：`slot_filler`（可选）+ `supervisor` + N 个 expert + `validator`/`repair`（可选）+ `reporter`。
两个现有域图都是 9 节点，新增域图照此形态。

### §3.2 Skill 层

**必须继承 `BaseSkill`**（`skills/base.py`），并在类定义期满足三项硬约束 ——
`__init_subclass__` 会直接抛 `TypeError`，写漏了根本 import 不过去：

| 必填 | 说明 |
|---|---|
| `name` | 与目录名一致，且决定图节点名 `<name>_skill` |
| `capabilities` | 至少 1 个，形如 `<域>.<动作>` |
| `description` | 进 Planner prompt 的能力描述，不能空 |
| `examples` | 至少 1 个，Planner 参考 |
| `params_schema` | 推荐类型化写法 `{"type","required","description","enum","auto"}`；旧式纯字符串值仅作向后兼容 |

**capability 命名**：`^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`（**恰一个点**，`<域>.<动作>`）。
由 `manifest.py` 的 `_CAP_NAME_RE` 强校验。全域唯一，且不得与 workflow 重名。

**图节点自注册**：在 `skills/<name>/__init__.py` 内完成，**不在 `registry.py` 集中代注册**：

```python
# skills/<name>/__init__.py
from backend.orchestration.tool_registry import tool_registry
from backend.skills.<name>.skill import <Name>Skill, <name>_skill_node

tool_registry.register_skill_node("<name>_skill", <name>_skill_node)
```

**Skill 层禁止事项**：

- ❌ **禁止定义 `@tool`**。Tool 一律在 `backend/tools/` 下定义并注册。
  即使是为了收敛 Planner prompt 而做的「聚合 Tool」（如 `map_lookup_tool`），
  也属于 Tool 层，放 `backend/tools/<域>/lookup.py`，由 Skill 持有引用。
- ❌ 禁止直接写 SQL / 调 HTTP。一律经 Tool。
- ⚠️ 覆盖 `execute()` 属例外（当前 2 例：`sql`、`business_analysis`），
  因为要自行保证结构化输出；**新增覆盖需在 §4 登记**。

**一个 Skill 对应多个 Tool** 时，覆盖 `_select_tool(capability, params)` 做分发
（参考 `skills/competitor_analysis/skill.py`），并把 params 过滤到目标 Tool 的签名内
—— LangChain `invoke` 遇到未知参数会直接抛错。

### §3.3 Tool 层

**定义位置**：`backend/tools/`（按域分子包：`map/`、`travel/`）。**不在 `skills/` 下定义。**

**三项约定**（第 1、2 项对**全部** Tool 强制；第 3 项仅对**新增** Tool 强制 —— 存量情况见 §4 E8）：

1. 必须用 `@tool` 装饰（LangChain）；
2. 必须在文件底部注册（同一文件多处定义由 `tool_registry` 查重并抛 `DuplicateToolError`）：

```python
# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402
tool_registry.register(<fn>, __file__)
```
   同一文件多个 Tool 用元组循环注册（参考 `tools/map/route.py`）。

3. **新增** Tool 必须返回 **JSON 字符串**：成功与失败都用 JSON，**失败返回 `{"error": ...}` 而非空值**，
   以便 LLM 区分「查不到」与「查不了」。统一走 `tools/map/_base.py` 的 `ok()/fail()/not_configured()`。

   > ⚠️ **本条只对新增 Tool 强制，存量不追溯。** 全部 34 个 Tool 中仅 **16 个**返回 JSON
   > （`map/` 全部 14 + `travel/poi` + `sql.execute_sql_tool`），其余 **18 个返回 Markdown / 纯文本**
   > （`competitor` 4 · `email` 4 · `web` 2 · `memory` 2 · `sql.sql_query_tool` 1 · `rag` 1 ·
   > `report` 1 · `data_collection` 1 · `calculator` 1 · `export` 1）。
   > 这是**登记在案的偏离**（§4 E8），不是遗漏——它们的文本契约已被前端渲染、evaluation runner
   > 与 `final_answer` 消费，改 JSON 属破坏性变更且无功能收益。
   > **不要以这 18 个为参照写新 Tool，也不要顺手改造它们。**

**参数派生**：Tool 的 `args_schema` 是参数的唯一事实源 —— MCP 工具清单与 Skill 只做引用，不得手写第二份。

> ⚠️ **同名模块澄清**（踩过坑，务必要看清 import 路径，不要看名字）：
>
> | 模块 | 是什么 | 数据源 | 生产消费方 |
> |---|---|---|---|
> | `backend/tools/tool_registry.py` | **Tool** 注册表 | 各 Tool 模块底部 `register()` | **无**（消费方是质量脚本 + 守护测试） |
> | `backend/orchestration/tool_registry.py` | **Capability** 注册表 | `skills/registry.py` 的 Skill 实例派生 | Planner / Critique / tool_selector / direct_executor / builder / system / supervisor … |
>
> 本节说的 `tool_registry` 一律指**前者**。`CAPABILITY_SCHEMA` 是**后者**的
> `cached_property`（`orchestration/tool_registry.py:106`），与 Tool 注册表无关 ——
> 「Planner 用的是 CAPABILITY_SCHEMA」**不等于**「Planner 不用 tool_registry」。
>
> **Tool 注册表的职责**（它是 Tool 层的发现与查重权威，不是死代码）：
> 1. 重复定义防护（`DuplicateToolError`，P0）；
> 2. **静态发现**：`scan_repo_declared_tools()` —— AST 扫描 `@tool` 声明，
>    是质量脚本与守护测试的**唯一判据来源**；
> 3. 运行期登记（`_registered_tools`），供诊断/统计。
>
> **「漏注册」的真实影响与检出**：它不影响 Planner（走 Capability 层）、不影响
> `/mcp/tools`（走 `mcp_servers.manager._servers` 的 `list_tools()`，第三套清单），
> 影响的是 Tool 层自身的可发现性与统计。漏注册长期无人发现的原因不是「没有消费方」，
> 而是**唯一的校验脚本坏了**：`scripts/tool_quality_check.py` 曾在 L131 缩进错位
> （`IndentationError`，根本无法运行），`_clean` 版期望清单又硬编码 10 条
> （34 个工具里漏的那 24 个不在检查范围），且两者均未接入 CI。
> 2026-09-16 已修：脚本可运行、判据改为 AST 派生、与守护测试共用同一实现。
>
> **门禁**：`backend/tests/test_layer_consistency.py::TestToolLayerCompleteness`（pytest 内，
> 双向：漏注册 + 幽灵条目）；诊断入口 `python scripts/tool_quality_check.py`。

### §3.4 MCP 层

MCP 不是新的一层，而是 **Tool 的对外协议封装**。

**必须继承 `MCPServer`**（`mcp_servers/manager.py`），声明 `name` + `description` + `list_tools()` + `call_tool()`。

**参数禁止手写**：有对应 Tool 的，一律用 `langchain_tool_to_mcp_meta(tool, name=..., description=...)`
从 `args_schema` 派生（由 `test_registry_consistency.py` 锁死）。
确实没有 Tool 等价物的（如 `list_tables`），才允许手写参数表并注明原因。

**注册**：在 `mcp_servers/servers/__init__.py::register_all()` 加一行，由 `app/server.py` 在启动时调用。
**不要**在 `server.py` 里逐条 `manager.register(...)`。

两个出口必须一致：标准协议端点（`mcp_servers/protocol_app.py`，端口 8091，工具集自动 = `manager.discover()`）
与 REST（`app/api/routes/mcp.py` 的 `/mcp/tools|/servers|/call`）不得有第二份清单。

### §3.5 Workflow 层

**声明**：`@workflow` 装饰器写入 `WorkflowMeta`（`name` / `description` / `examples` / `steps`）。
`name` 必须满足 `^[a-z][a-z0-9_]*$`（**纯蛇形、不带点**；带点是 capability 的命名空间，
由 `manifest.py` 的 `_WF_NAME_RE` 拒绝）。

**三处必须同时到位，缺一即故障**：

| 处 | 位置 | 缺了会怎样 |
|---|---|---|
| ① 类实现 | `orchestration/workflows/<name>.py` | — |
| ② 注册清单 | `orchestration/workflows/__init__.py::register_all()` | 路由选中后无法执行 |
| ③ 路由声明 | `orchestration/router/capabilities.yaml` 的 `workflows` 段（≥1 条 examples） | **向量路由对它失明** |

②③ 的一致性由 `test_layer_consistency.py::TestWorkflowManifestAlignment` 双向锁定。

---

## §4 例外台账（登记在案的偏离）

台账内的偏离**允许存在**，但必须满足：有明确理由、有注释说明、不阻碍后来者照 §3 写新代码。
**新代码不得以此为由复制偏离。**

| ID | 例外 | 现状 | 理由 | 处置 |
|---|---|---|---|---|
| **E1** | Agent/专家契约存在 4 套 | ①`skills/base.py::BaseSkill`(12) ②`agents/capability/base.py::BaseCapability`+`BaseAgentSkill`(1 实现) ③客服 `run_expert_safely`+`ExpertResult`(5) ④旅游同形副本(4) | ③④ 的字段语义不同（客服绑 `response_draft/evidence`，旅游传结构化 POI/行程），`travel/experts/base.py` 顶部已写明「刻意不复用」；硬套会变成假复用 | ③④ 保留。② 只有 1 个实现（`InventoryAnalyzer`），**新业务 agent 优先用 ②**；若长期无第二实现，应降级为 deprecated 并迁到 ② |
| **E2** | Skill 覆盖 `execute()` 绕过输出归一化 | `skills/sql`、`skills/business_analysis` | 需返回结构化结果（`SQLResult.model_dump()` / `BusinessInsight`），由 `output_type="structured"` 语义驱动 | 保留。新增覆盖须在 PR 说明理由并在本表登记 |
| **E3** | capability 命名风格个别不一致 | `travel.poi_search` 是「域.子域_动词」，其余为「域.动词」 | 结构上合法（正则只要求一个点）；改名会破坏路由/前端契约 | 保留为历史例外。**新 capability 统一用 `<域>.<动词>`** |
| **E4** | MCP 部分工具参数手写 | `rag.list_documents` / `rag.get_stats` / `sql.list_tables` | 无 Tool 等价物 | 保留，代码内已注明 |
| **E5** | `tools/tool_registry.py`（Tool 注册表）无运行时消费方 | 消费方是 `scripts/tool_quality_check.py` + `test_layer_consistency.py`，均在运行时之外 | Tool 层与 Capability 层职责分离的必然结果：Planner 的输入是 Capability 层（`orchestration/tool_registry.py`），Tool 层只负责定义、内聚与可发现性 | **不作为缺陷处理**。定位已收窄为「静态发现 + 重复定义防护 + 运行期登记」（§3.3）。若未来要让 MCP 暴露全部 Tool，先评估 prompt 膨胀 |
| **E6** | 两套路由索引 | capability/workflow 走 `vector_router`（manifest 派生）；`orchestration/workflow/router.py::TaskRouter` 自建 embed 索引 | TaskRouter 现仅测试在用 | 生产路由一律以 manifest + `vector_router` 为准；**禁止向 TaskRouter 加新依赖**，长期应下线 |
| **E7** | 两个同名 `tool_registry` 模块 | `tools/tool_registry.py`（Tool 表）与 `orchestration/tool_registry.py`（Capability 表） | ADR-0001 合并双注册表时未同步改名 | 两处 docstring 已加模块级澄清注释。长期宜把 orchestration 侧改名 `capability_registry.py`（涉及 30+ 处 import，列入 §8 遗留） |
| **E8** | 存量 Tool 返回非 JSON（Markdown / 纯文本） | 34 个 Tool 中 **16 个 JSON**（`map/` 全部 14 + `travel/poi` + `sql.execute_sql_tool`），**18 个非 JSON**（`competitor` 4 · `email` 4 · `web` 2 · `memory` 2 · `sql.sql_query_tool` 1 · `rag` 1 · `report` 1 · `data_collection` 1 · `calculator` 1 · `export` 1）<br>另：`sql.execute_sql_tool` 的异常分支是 `raise` 而非 `fail(...)`，同样偏离 | 存量 Tool 多为「委托子系统 + 返回人类可读结果」：`sql_query_tool` 走 NL→SQL Agent 输出 Markdown 表格，`search_knowledge_tool` 输出 RAG 生成的文本，`competitor.*` 直接产出 Markdown 报告。这些契约已被前端渲染、evaluation runner 与 `final_answer` 消费，改 JSON 是**破坏性变更**且无功能收益 | 保留。**§3.3 第 3 条已同步收窄为「仅对新增 Tool 强制」**。存量若要统一，须先盘点下游消费方并单独立项，不得在无关改动中顺手改造 |
| **E9** | `routed: false` 的隔离只覆盖**路由层**，未覆盖 Planner / Critique | 3 个内部能力：`email.watch` · `competitor.watch` · `competitor.history`<br>**已隔离**：`rule_router`（`_COMPETITOR_KEYWORDS` 只指向 `competitor.analyze`）· `vector_router`（`ROUTE_EXAMPLES` 只取 `routed_capabilities`）· `llm_router`（校验 `ALL_CAPABILITIES`，仅 14 个）<br>**未隔离**：`agents/planner/planner.py::_format_capabilities_schema()` 与 `agents/planner/critique.py` 规则 1 都遍历 `tool_registry.get_available_capabilities()`（**17 个全含**） | 数据源不同：路由层消费 `router/types.py::ALL_CAPABILITIES`（manifest 的 `routed_capabilities`），Planner / Critique 消费 `orchestration/tool_registry`（由 Skill 自注册派生，按设计含全部声明过的能力）。`routed` 字段从未被 Planner 链消费 | **保留，但须明确语义**：`routed: false` = 「不参与**用户问题路由**」，**不等于**「Planner 不可见」。<br>**风险点**：Planner prompt 只渲染 `description / params / 示例`，**不渲染 `routed` 与 `reason`**，LLM 无从判断某能力是内部的。`email.watch` 是默认 `timeout_sec=120` 的阻塞长轮询（`tools/email.py::watch_email_tool` → `agently_watch`），其 `reason` 自述「仅通知闭环与 automation 内部使用」，却与 14 个公开能力并列出现在可选清单中。<br>**若需收紧**：在 `_format_capabilities_schema()` 过滤非 routed 能力、或为其追加「内部能力」标记 —— 属**行为变更**（影响 LLM 可见能力集），须单独评估，不在本次改动范围 |

---

## §5 新增东西的 Checklist

> 📖 **动手前先看操作手册**：[`2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md`](2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md)
> —— 本节是「要改哪些文件」的摘要，手册是本节的展开版（含可抄的代码模板、**隐藏接线点总表**、
> 验证命令、以及 §3.1 域图接入时 `router_node.py` 预过滤链路等本节未覆盖的步骤）。
> 两者冲突以本节 + §3 为准，并回来把手册改对。

### 加一个 Tool
1. 在 `backend/tools/<域>/<mod>.py` 定义，`@tool` 装饰，返回 JSON 字符串，失败 `fail(...)`
2. 文件底部 `tool_registry.register(<fn>, __file__)`
3. 若属于某 Skill 的能力，把 Tool 接到该 Skill 的 `_tool_fn` 或 `_select_tool`
4. 跑 `test_layer_consistency.py`

### 加一个 Skill（含新 capability）
1. `skills/<name>/skill.py`：继承 `BaseSkill`，声明 `name` / `capabilities` / `description` / `params_schema` / `examples`
2. `skills/<name>/__init__.py`：`tool_registry.register_skill_node("<name>_skill", <name>_skill_node)`
3. `skills/registry.py`：import 该 Skill 类并加进 `_instances`
4. `skills/__init__.py`：加进 re-export 与 `__all__`（保持包面一致）
5. `orchestration/router/capabilities.yaml`：加 `capabilities` 条目 —— `routed: true` 需 ≥2 条（建议 5-10）examples；`routed: false` 必须写 `reason`
6. 跑 `test_registry_consistency.py` + `test_layer_consistency.py`

### 加一个域图
1. 照 `travel/register.py` 写 `<domain>/register.py`（`DomainGraph` 四元组）
2. `backend/domains/__init__.py` 加一行 import
3. 域图内部节点照 §3.1 形态；**不要改 `builder.py`**
4. ⚠️ **还差两步，缺了域永远不触发**（2026-09-16 补记，详见操作手册 §6）：
   - 写 `orchestration/graph/<domain>_prefilter.py`（暴露 `try_<domain>_prefilter(query, state)`，
     受 `config/<domain>.py` 的 `<DOMAIN>_ENABLED` 门控，**默认 false**）
   - **在 `orchestration/graph/router_node.py` 里把该 prefilter 插进预过滤链路**
     —— 当前顺序是**硬编码**的（CS → 旅游 → CS 语义兜底 → 三层 Router），
     新增域必须手动加进这段，不是自动发现的

### 加一个 Workflow
1. `orchestration/workflows/<name>.py`：`@workflow(name="<蛇形名>", ...)`
2. `orchestration/workflows/__init__.py`：加进 `register_all()` 的延迟 import 与 `classes` 元组
3. `capabilities.yaml` 的 `workflows` 段加条目（≥1 条 examples）
4. 跑 `test_layer_consistency.py`

### 加一个 MCP Server
1. `mcp_servers/servers/<name>.py`：继承 `MCPServer`，参数用 `langchain_tool_to_mcp_meta` 派生
2. `mcp_servers/servers/__init__.py::register_all()` 加一行

---

## §6 守护测试矩阵

| 规则 | 守护测试 |
|---|---|
| capability 注册表从 Skill 派生（非硬编码） | `test_registry_consistency.py::TestCapabilityDerivation` |
| manifest ↔ skills registry 双向对账 | `test_registry_consistency.py::TestCapabilityManifest` |
| Skill `params_schema` 与 Tool 实际参数不脱节 | `test_registry_consistency.py::TestSkillToolAlignment` |
| MCP 参数从 `args_schema` 派生 | `test_registry_consistency.py::TestMcpListToolsDerivation` |
| 协议端点工具集 == `manager.discover()` | `test_registry_consistency.py::TestProtocolEndpointParity` |
| 每个 Skill 的节点名同时出现在 `_skill_nodes` 与 `CAPABILITY_MAP` | `test_adr0001_dual_registry_merge.py` |
| **所有 `@tool` 必须已注册** | **`test_layer_consistency.py::TestToolLayerCompleteness`** |
| **`@tool` 不得定义在 `skills/` 下** | **`test_layer_consistency.py::TestToolLayerOwnership`** |
| **每个 Skill 都有图节点 + 节点不得集中代注册** | **`test_layer_consistency.py::TestSkillNodeSelfRegistration`** |
| **`register_all()` ↔ manifest workflows 双向对齐 + 幂等** | **`test_layer_consistency.py::TestWorkflowManifestAlignment`** |
| **命名约定（capability 带点 / workflow 不带点）** | **`test_layer_consistency.py::TestNamingConvention`** |

---

## §7 本次归一改动清单（2026-09-16）

| # | 偏差 | 改动 | 文件 |
|---|---|---|---|
| 1 | `map_lookup_tool` 出界 + 漏注册 | 移到 Tool 层并注册；Skill 改为引用 + re-export | `backend/tools/map/lookup.py`（新）、`skills/map/skill.py`、`tools/map/__init__.py` |
| 2 | manifest 缺 `selection_decision` | 补 `workflows` 段（4 条 examples）+ 头部治理规则补 workflow 命名段 | `orchestration/router/capabilities.yaml` |
| 3 | workflow 注册集中手写且两处不一致 | 新增 `register_all()` 作唯一入口；server.py 与 e2e.py 均改为调用（顺带修掉 e2e 漏 `MarketResearch`） | `orchestration/workflows/__init__.py`、`app/server.py`、`evaluation/runners/e2e.py` |
| 4 | Skill 节点注册位置 10 vs 2 | `data_collection` / `business_analysis` 改为各包自注册；`registry.py` 不再代注册 | `skills/data_collection/__init__.py`、`skills/business_analysis/__init__.py`、`skills/registry.py` |
| 5 | 包面 re-export 不齐 | `skills/__init__.py` 补齐 3 个 Skill 的 re-export | `skills/__init__.py` |
| 6 | workflow 命名无校验 | `manifest.py` 加 `_WF_NAME_RE` fail-fast | `orchestration/router/manifest.py` |
| 7 | 误导性启动日志 | `已加载 20 个 Tool` → `tools 包导入完成，本包已注册 20 个 Tool` | `backend/tools/__init__.py` |
| 8 | 上述规则无守护 | 新增 10 条守护断言 | `backend/tests/test_layer_consistency.py`（新） |
| 9 | **质量脚本跑不起来**（`IndentationError`，L131），是漏注册长期无人发现的真正原因 | 修缩进；补 `sys.path` 自举；期望清单由硬编码 10 条改为 AST 派生 | `scripts/tool_quality_check.py` |
| 10 | `_clean` 版脚本与主脚本两套清单会漂移 | 收敛为薄包装，实现只留一份 | `scripts/tool_quality_check_clean.py` |
| 11 | 两个同名 `tool_registry` 模块易混（本次误判的根因） | 两处模块 docstring 加同名澄清；AST 发现逻辑收进 Tool 注册表作唯一判据 | `backend/tools/tool_registry.py`、`backend/orchestration/tool_registry.py` |
| 12 | §3.3「Tool 必须返回 JSON」写成全域必须，但实测仅 16/34 达标，且 §4 台账未登记 | 措辞收窄为「**新增** Tool 必须」+ 补 §4 **E8** 记录存量 18 个非 JSON Tool 的现状、理由与不改造约束 | 本文件 §3.3、§4 |
| 13 | 11 个 routed capability 的 examples 仅 2–4 条，低于规范自荐的 5–10 条（4 个刚好卡硬下限 2 条） | 逐条补到 6 条，并写入「examples 撰写要点」（条数是召回余量、须落在能力边界内）；`business.analyze` 等 11 个 | `orchestration/router/capabilities.yaml` |
| 14 | §1 事实源清单把「域包 `register.py`」与「`domains/__init__.py` 触发器」写得像同一目录 | 拆成「域包 / 域触发器」两行，并注明 `backend/domains/` 只是触发器、不含域代码 | 本文件 §1 |
| 15 | §8 P3 只描述了「覆盖率门禁形同虚设」，未给出规避手法 | 补 2026-09-16 实证（局部跑 2 个测试文件 → 用例全绿但 `EXIT=1`、`coverage.xml` 被覆写为 9%）+ 规避办法 `--no-cov` | 本文件 §8 |
| 16 | 「`competitor.analyze` 的样例『监控一下竞品价格变化』与内部能力 `competitor.watch` 语义重叠、可能被向量路由抢走」——经核实为**机制误判** | **不改样例**（该样例是确定性路由的承重件，见 §4 E9 与本条说明）；在 YAML 头部补「反直觉设计」备忘三条 | `orchestration/router/capabilities.yaml` |
| 17 | `routed: false` 的隔离只覆盖路由层，Planner / Critique 未过滤，台账未登记 | 补 §4 **E9** 记录隔离生效/未生效的确切位置与 `email.watch` 风险点；§8 加 P2 跟进项 | 本文件 §4、§8 |
| 18 | `competitor.watch` / `competitor.history` 的 `reason` 写「仅 selection_decision workflow 内部使用」，**经核实不成立** —— 该 workflow 的 `competitor_data` 步骤是直接 `store.list_watch()`（`selection_decision.py:125`），**不经过这两个能力**。这条错文案正是「样例与 watch 重叠」误判的源头 | 改写为可核实的表述：说明其真实语义是「不参与用户问题路由，用户侧请求由 `competitor.analyze` + `action` 分发承接」；并同步更正上方「仅 selection_decision workflow 消费」的注释 | `orchestration/router/capabilities.yaml` |

| 19 | §5「加一个域图」只有 3 步，**漏掉 2 步接线**：`orchestration/graph/router_node.py` 的预过滤链路是**硬编码**的（CS → 旅游 → CS 语义兜底 → 三层 Router），域图不会自动被发现；且新域缺 `config/<domain>.py` 的开关 | §5 域图补第 4 步（写 prefilter + 改 `router_node.py`）；新增操作手册并把本手册 §5 的摘要指向它 | 本文件 §5、`docs/2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md`（新） |

**实测结果**：AST 扫描 34 个 `@tool` → 注册表 34 个，**0 漏注册、0 幽灵条目**；
12 个 Skill → 12 个图节点全在；`register_all()` ↔ manifest 双向对齐。
质量脚本 `python scripts/tool_quality_check.py` 可独立运行并通过（EXIT=0）；
负向验证确认判据能同时抓出「漏注册」与「幽灵条目」两个方向。

**v1.2 复核（#16–18）**：`capabilities.yaml` 本轮改动 = **18 行头部注释**（「反直觉设计」备忘）
+ **2 处 `reason` 文案更正**（`competitor.watch` / `competitor.history`）+ **1 行内部能力注释重写**；
YAML 语义未变（17 capability / 4 workflow / 99 examples / `competitor.analyze` 6 条），
行尾保持全 CRLF（300/300）。守护测试 **36 个用例全绿**（以 `--no-cov` 运行，未覆写覆盖率产物）。
E9 的「已隔离 / 未隔离」四点均在代码中逐一定位核实：
`vector_router.py::ROUTE_EXAMPLES` 取自 `_manifest.routed_capabilities`、
`llm_router.py` 校验 `ALL_CAPABILITIES`（14）、
`planner.py::_format_capabilities_schema()` 与 `critique.py` 规则 1 取 `tool_registry.get_available_capabilities()`（实测 17）。

**v1.1 复核（#12–15）**：`capabilities.yaml` 结构校验通过（17 capability / 4 workflow，
examples 唯一性、下限、`reason`、命名全绿），`routed:true` 的 examples 已无低于 5 条者
（总数 65 → 99）；守护测试 **36 个用例全绿**（`test_registry_consistency` 16 +
`test_layer_consistency` 9 + `test_adr0001_dual_registry_merge` 11，均以 `--no-cov` 运行）。
VectorRouter 启动时会按新的 examples 总数自检并自动重建 Chroma 索引，无需人工干预。

---

## §8 已知遗留（未在本次改动范围）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P1 | E1-② `agents/capability` 只有 1 个实现 | 需拍板：要么落地为业务 agent 唯一基座，要么标记 deprecated |
| P2 | E6 `TaskRouter` 自建索引仍在测试中使用 | 与 `vector_router` 功能重叠，建议下线并迁移测试 |
| P2 | `.mcp.json`（仓库根）外部 6 个 MCP server | 属开发机客户端配置，未纳入 §3.4 治理；如需团队统一，应移入受版本控制的配置并文档化 |
| P2 | E7 两个同名 `tool_registry` 模块 | 已加 docstring 澄清。正式改名 `orchestration/capability_registry.py` 需动 30+ 处 import，建议与下次大范围重构合并执行 |
| P2 | 质量脚本未接入任何门禁 | `scripts/tool_quality_check.py` 已修好可运行，但没有 CI 调用它（见末行）。真正的门禁是 `test_layer_consistency.py`（pytest 覆盖到），脚本定位为人工诊断入口 |
| P3 | `agent-mcp-service-1` 容器长期 `unhealthy` | 8091 端口的 MCP 服务健康检查未通过，与本层规范无关，需单独排查（容器运行时问题，非代码问题） |
| P3 | 覆盖率门禁 `--cov-fail-under=55` 形同虚设 | 局部运行会覆写项目级 `coverage.xml` / `.coverage` / `htmlcov/`（`pytest.ini` 的 `addopts` 带 `--cov-report=xml:coverage.xml`，无按运行范围隔离），项目内不存在可信全量覆盖率数字。<br>**2026-09-16 实证**：仅运行 2 个守护测试文件 → 25 个用例全绿，但进程以 `EXIT=1` 退出（`Coverage failure: total of 9 is less than fail-under=55`），且 `coverage.xml` 被覆写为 9% 的部分覆盖率。<br>**规避**：局部跑测试一律加 `--no-cov`（CI 全量跑不受影响）。 |
| — | CI 三个 workflow 全为 `.disabled` | 无自动化门禁，规范全靠本地 pytest 自觉 |
| P2 | E9 `routed: false` 对 Planner / Critique 不生效 | 3 个内部能力（尤其 `email.watch` 默认 120s 阻塞长轮询）会作为**可选**能力出现在 Planner prompt 中，且不带「内部」标记。收紧需改 `planner.py::_format_capabilities_schema()`（过滤或标注），属**行为变更**，须单独评估后再动 |

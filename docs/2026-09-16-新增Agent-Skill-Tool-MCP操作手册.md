# 新增 Agent / Skill / Tool / MCP 操作手册

> **面向**：任何要往本仓库「加东西」的 agent（含未来的我）。
> **配套**：`docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md`（v1.2，讲**为什么**与**现状/台账**）。
> 本手册只讲**怎么做** —— 按场景列文件清单、给可抄的模板、给验证命令。
> **冲突时以规范文档 §3（分层规范）与 §4（例外台账）为准**，并回来把本手册改对。
>
> 最后核对：2026-09-16（逐条对照代码实测，非抄文档）。

### 📁 路径约定（全文适用，避免歧义）

| 写法 | 相对谁 | 例 |
|---|---|---|
| 以 `backend/`、`mcp_servers/`、`docs/`、`scripts/` 开头 | **仓库根** | `backend/tools/map/_base.py` |
| 以 `orchestration/`、`skills/`、`agents/`、`config/`、`tools/`、`tests/` 开头 | **`backend/`** | `orchestration/graph/builder.py` 实为 `backend/orchestration/graph/builder.py` |

> ⚠️ 这两类**不是同一层目录**。`backend/domains/` 只是域图**触发器**、不含域代码；
> 域代码在 `backend/travel/`、`backend/customer_service/`（与主图同级的独立业务包）。

---

## 0 · 30 秒决策表

| 我要加什么 | 看 | 要动的文件数 |
|---|---|---|
| 一个原子操作（查表 / 调外部 API / 发邮件） | §2 Tool | 2 |
| 一类新业务能力，且要能被用户提问路由到 | §3 Skill + Capability | 5 |
| 一个确定性多步流程（不走 Planner） | §4 Workflow | 3 |
| 把已有 Tool 暴露给外部 MCP client | §5 MCP | 2 |
| 一个新垂直域（带自己的子图 + 专家） | §6 域图 | 5～7（**含 2 处隐藏接线**） |
| 一个业务 Agent / 分析专家 | §7 Agent | 2 |
| 只是想看清「代码里还有哪些必须改、文档没写」 | **§8 隐藏接线点总表** | — |

---

## 1 · 四条铁律与方向约束（违反必被打回）

| 编号 | 原则 | 落地 |
|---|---|---|
| **G1** | 声明式注册，启动期派生，**fail-fast** | 声明不合法就抛（`ManifestError` / `TypeError` / `DuplicateToolError`），**不静默降级** |
| **G2** | **单一事实源（SSOT）** | 每类东西只有一个声明处；派生量**禁止手写回去**（守护测试反向锁死） |
| **G3** | **模块自注册** | 谁定义谁注册（包 `__init__.py` 或模块底部）；**禁止集中代注册**；容器级用 `register_all()` |
| **G4** | **例外必须登记** | 做不到统一的，必须在规范 §4 台账登记理由与处置，不得口头约定 |

**调用方向铁律**：

```
Planner / Supervisor        Agent 层：决定「做什么」
      ↓ capability
    Skill                   Skill 层：能力契约 + 重试/超时/输出归一化
      ↓
    Tool                    Tool 层：一次确定性调用（LangChain @tool）
      ↓
Infrastructure              RAG / SQL / 腾讯 LBS / SMTP（无业务语义）
```

**上层调下层，下层不知道上层存在。** Tool 不得 import Skill；Skill 不得 import Planner；
Infrastructure 不得 import 以上任何一层。MCP 不是第 5 层，是 **Tool 的第二个出口**。

**事实源清单（改之前先确认改的是哪一处）**

| 层 | 声明处 | 派生 / 执行处 |
|---|---|---|
| Agent 主图 | `orchestration/graph/builder.py`（8 核心节点，**不得手写加节点**） | `orchestration/graph/system.py` |
| Skill | `skills/<name>/skill.py`（类）+ `skills/registry.py`（实例） | `orchestration/tool_registry.py` 派生 `CAPABILITY_MAP` |
| Capability 路由 | `orchestration/router/capabilities.yaml`（**唯一事实源**） | `router/manifest.py` → `types.py` / `vector_router.py` / `llm_router.py` |
| Tool | `backend/tools/<域>/<mod>.py`（`@tool` + 底部 `register`） | `tools/tool_registry.py` |
| MCP | `mcp_servers/servers/<name>.py` | `mcp_servers/servers/__init__.py::register_all()` |
| Workflow | `orchestration/workflows/<name>.py`（`@workflow`） | `orchestration/workflows/__init__.py::register_all()` |
| 域包 | `backend/<domain>/register.py` | 由下面的触发器 import → builder 自动布线 |
| 域触发器 | `backend/domains/__init__.py`（**每新增一域加一行 import**）<br>⚠️ `backend/domains/` **只是触发器、不含域代码**；域代码在 `backend/travel/`、`backend/customer_service/` | builder 自动布线（节点 + 条件边 + 直连 END） |

---

## 2 · 加一个 Tool

**位置**：`backend/tools/<域>/<mod>.py`（按域分子包，如 `map/`、`travel/`）。
❌ **不得定义在 `skills/` 下** —— 守护测试 `TestToolLayerOwnership` 会抓
（`TOOL_SCAN_ROOTS = ("backend/tools", "backend/skills")` 就是为抓它而同时扫两处）。

### 2.1 模板

```python
"""tools/<域>/<mod>.py — <一句话职责>"""
from langchain_core.tools import tool

from backend.tools.map._base import fail, not_configured, ok   # 新 Tool 一律用这套 JSON 助手


@tool
def my_tool(query: str, limit: int = 5) -> str:
    """<给 LLM 看的功能描述>。

    query: <参数说明>
    limit: <参数说明>
    返回: JSON 字符串
    """
    if not _configured():
        return not_configured()          # 「查不了」——显式返回，别抛也别返回空
    try:
        payload = _impl(query, limit)
    except Exception as e:
        logger.warning(f"[Tool:my_tool] 失败：{e}")
        raise                            # 上抛给 BaseSkill，保留重试机制
    return ok({"items": payload})


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402
tool_registry.register(my_tool, __file__)
```

### 2.2 三项约定

| # | 约定 | 强制范围 |
|---|---|---|
| 1 | 必须 `@tool` 装饰 | **全部** Tool |
| 2 | 必须在**文件底部**注册（同一文件多处定义由 `tool_registry` 查重抛 `DuplicateToolError`）；同文件多个 Tool 用元组循环注册（见 `tools/map/route.py`） | **全部** Tool |
| 3 | 必须返回 **JSON 字符串**：成功失败都用 JSON，**失败返回 `{"error": ...}` 而非空值**；统一走 `tools/map/_base.py` 的 `ok() / fail() / not_configured()` | **仅新增** Tool |

> ⚠️ 第 3 条**只对新增强制**。存量 34 个 Tool 里只有 16 个返 JSON，其余 18 个返 Markdown/纯文本
> —— 这是**登记在案的偏离**（规范 §4 **E8**），它们的文本契约已被前端渲染 / evaluation runner /
> `final_answer` 消费，改 JSON 是破坏性变更。
> **不要以那 18 个为参照写新 Tool，也不要顺手改造它们。**

**为什么「查不到」与「查不了」必须分开**（`tools/map/_base.py` 的设计说明）：
LLM 拿到 `{"items": []}` 会理解成「这里没有」并据此改写结论，拿到 `{"error": "配额用尽"}` 才会换策略。
混为一谈是幻觉的常见起点。

### 2.3 写副作用 → 必须过审批门

凡有写副作用的 Tool（发邮件 / 导出 / 采集 / 竞品写动作），执行前**必须**调
`backend/security/tool_approval.ensure_approved(tool_name, action, user_id, detail)`：

- 返回 `None` → 放行；返回 `str` → **待审批提示，直接作为 Tool 返回值**（Planner/Reporter 会转述给用户）。
- `TOOL_APPROVAL_MODE=required`（默认）时建审批单，管理员经 `/api/approvals` 批准后**重试同指纹操作**放行（TTL 内）。
- 工具层身份一律取自 `tools/session.get_tool_user_id()`（`RequestContext.bind` 注入），**禁止硬编码 user_id**。

参考实现：`tools/email.py`、`tools/export.py`、`tools/data_collection.py`、`tools/competitor.py`。

### 2.4 接线与验证

若这个 Tool 属于某个 Skill 的能力 → 把它接到该 Skill 的 `_tool_fn`，或多 Tool 时接 `_select_tool`（见 §3.4）。

```bash
cd backend && "D:/Program Files/workplace/agent/.venv/Scripts/python.exe" -m pytest \
  tests/test_layer_consistency.py -q --no-cov        # 必须：所有 @tool 已注册、且没出界
python scripts/tool_quality_check.py                   # 诊断入口（人工用）
```

---

## 3 · 加一个 Skill（含新 capability）

### 3.1 五处都要改（漏一处就故障）

| # | 文件 | 做什么 | 漏了会怎样 |
|---|---|---|---|
| 1 | `skills/<name>/skill.py` | 继承 `BaseSkill`，声明属性 + `_tool_fn` + `*_skill_node` 适配器 | — |
| 2 | `skills/<name>/__init__.py` | **自注册图节点**（见 3.3） | Skill 在图上不存在，执行即失败 |
| 3 | `skills/registry.py` | import 类 + 加进 `_instances` | capability 无实例，Planner 拿到也无从执行 |
| 4 | `skills/__init__.py` | 加进 re-export 与 `__all__` | 包面不一致 |
| 5 | `orchestration/router/capabilities.yaml` | 加 `capabilities` 条目 | **用户提问永远路由不到它** |

### 3.2 类定义硬约束（`__init_subclass__` 在**类定义期**就抛 `TypeError`）

| 必填 | 说明 |
|---|---|
| `name` | 与目录名一致；决定图节点名 `<name>_skill` |
| `capabilities` | ≥1 个，形如 `<域>.<动作>` |
| `description` | 进 Planner prompt 的能力描述，**不能空**（否则 LLM 不知道这能力干什么） |
| `examples` | ≥1 个，Planner 参考 |
| `params_schema` | 推荐类型化：`{"type","required","description","enum","auto"}`；旧式纯字符串仅向后兼容 |

```python
class MySkill(BaseSkill):
    name = "my"                           # 与目录名一致；决定图节点名 = f"{name}_skill"
    capabilities = ["my.domain_action"]
    description = "……（写给 Planner 看，含能力边界与不适用场景）"
    params_schema = {
        "query": {"type": "string", "required": True, "description": "…"},
        "mode":  {"type": "string", "required": False, "enum": ["a", "b"]},
    }
    examples = [{"query": "……"}, {"query": "……"}]
    output_type = "text"                  # 或 "structured"（见 3.5）

    @property
    def _tool_fn(self):
        return my_tool


async def my_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器 — Supervisor / SkillExecutor 路由到此节点"""
    skill = MySkill()
    cap = (state.get("plan", {}).get("nodes", {})
           .get(state.get("current_step_id", ""), {})
           .get("capability", "my.domain_action"))
    return await skill.execute(state, step_capability=cap)
```

> 节点名规则：**`f"{skill.name}_skill"`**。所以 `name="my"` → 节点 `my_skill`；
> `name="map_lookup"` → 节点 `map_lookup_skill`。
> **`name` 要起得让节点名可读**，别出现 `my_skill_skill` 这种。
>
> ⚠️ **被强制的**是「`f"{name}_skill"` 这个派生节点名必须已注册」（`TestSkillNodeSelfRegistration`
> 双向校验 `_skill_nodes` ↔ `CAPABILITY_MAP`）—— 所以 `__init__.py` 里注册的字符串**必须**由
> `name` 推导得出，写死一个不一致的名字会被抓。
> ⚠️ **未被强制的**是「`name` == 目录名」—— 规范如此要求，但**当前没有任何测试校验**。
> 请自觉遵守（`skills/registry.py` 的 import 路径是按目录写的，名字漂了会很难读）。

### 3.3 图节点自注册（**不在 `registry.py` 集中代注册**）

```python
# skills/<name>/__init__.py
from backend.orchestration.tool_registry import tool_registry
from backend.skills.<name>.skill import MySkill, my_skill_node

tool_registry.register_skill_node("<name>_skill", my_skill_node)

__all__ = ["MySkill", "my_skill_node"]
```

守护测试 `TestSkillNodeSelfRegistration` 双向校验「每个 Skill 都有图节点」+「节点不得集中代注册」。

### 3.4 一个 Skill 对应多个 Tool → 覆写 `_select_tool`

```python
def _select_tool(self, capability: str, params: dict):
    action = params.get("action") or ""
    if action == "watch" or capability == "my.watch":
        tool = my_watch_tool
    else:
        tool = my_analyze_tool
    allowed = set(getattr(tool, "args", {}) or {})
    return tool, {k: v for k, v in params.items() if k in allowed}   # ← 必须裁参数
```

⚠️ **必须把 params 裁剪到目标 Tool 的签名内** —— LangChain `invoke` 遇到未知参数**直接抛错**。
参考实现：`skills/competitor_analysis/skill.py`。

> 项目里的**漏斗式**范式：`competitor.analyze` 是唯一 routed 的竞品 capability，
> `action` 参数决定分发到 analyze / watch / history / watchlist 四个 Tool。
> 新增多 Tool 能力优先照这个来，别为每个子动作开一个 routed capability。

### 3.5 输出契约（`output_type` / `output_types`）

`execute()` 在 Tool 返回边界**按声明归一化**，保证下游（`final_answer`、done 事件 sources、记忆落库）类型稳定：

- `output_type = "text"`（默认）→ 保证 `str`；dict/list 会被序列化成 JSON 字符串。
- `output_type = "structured"` → 保证 `dict`；str 会尝试 `json.loads`。
- 按 capability 覆盖用 `output_types = {"my.cap": "structured"}`。

> 血的教训：`sql.query` 曾把 `SQLResult` dict 原样透传进 `final_answer`，下游所有按字符串处理的地方
> （done 事件 sources、`emit_delta`、记忆落库写 VARCHAR）**全部崩溃**。
> **覆写 `execute()` 就等于自己承担输出契约**（现仅 `sql`、`business_analysis` 两例，属规范 §4 **E2** 例外；
> 新增覆盖必须登记）。

### 3.6 `capabilities.yaml` 条目怎么写

```yaml
  - name: my.domain_action          # ^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$（恰一个点）
    skill: my_skill                 # 必须与 skill.py 的 name 一致
    routed: true                    # 或 false（内部能力）
    rule_keywords:                  # 可选：规则路由（RuleRouter）关键词
      - 关键词
    examples:                       # routed:true 时必填，≥2（建议 5–10）
      - 用户会怎么问这句
      - 换一种说法
```

**三条硬约束（`manifest.py` 会 fail-fast）**：块内 `examples` 不得重复；`routed: true` → examples **≥2**；
`routed: false` → 必须写 **`reason`**。

- **examples 条数是召回余量，不是装饰。** 2 条只是硬下限，5–10 条才是安全区。
- **examples 必须落在能力边界内** —— 先读 `description`：`travel.poi_search` 只支持福州/厦门/杭州；
  `competitor.analyze` 只处理「有明确竞品 URL 的页面级分析」。
- 增删 examples 后**不必手工删** `backend/data/router_index` —— VectorRouter 启动时按数量对账，不一致会自动重建。

**`routed: false` 的语义**（极易误判，已在 yaml 头部注明）：
= 「**不参与用户问题路由**」，**不等于「不可见」**。它不在向量索引里（`vector_router` 只索引 `routed_capabilities`），
Rule/LLM Router 也选不到，**所以它抢不走任何 routed 能力的路由**；
但 `planner.py::_format_capabilities_schema()` 与 `critique.py` 读的是 `orchestration/tool_registry`
（**17 个全含**），**内部能力对 Planner LLM 仍可见**，且 prompt 不渲染 `routed`/`reason`（规范 §4 **E9**）。
→ 所以 `routed:false` 只适合「不阻塞、无副作用」的能力。**阻塞型（如 120s 长轮询）不要靠它兜底。**

### 3.7 Skill 层禁止事项

- ❌ **禁止定义 `@tool`** —— 一律放 `backend/tools/`，即使是为了收敛 Planner prompt 的「聚合 Tool」
  （如 `map_lookup_tool` 放 `backend/tools/map/lookup.py`，由 Skill 持引用）。
- ❌ 禁止直接写 SQL / 调 HTTP —— 一律经 Tool。
- ⚠️ 覆写 `execute()` 属例外，**必须登记 §4**。

### 3.8 别忘：登记用户可读标签

新 capability 若可能走 **direct 路径**，**必须**在
`orchestration/graph/direct_executor.py::_USER_CAP_LABELS` 加一条中文标签。
不加会 fallback 成泛称 `"信息查询"` —— **而这个 label 会直接渲染给终端用户当标题**
（历史上曾把 `### 直接执行 sql.query` 暴露给用户，故有此表）。

---

## 4 · 加一个 Workflow

**三处必须同时到位，缺一即故障**：

| # | 位置 | 缺了会怎样 |
|---|---|---|
| ① | `orchestration/workflows/<name>.py`：`@workflow(name="<蛇形名>", description=…, examples=[…])` | — |
| ② | `orchestration/workflows/__init__.py::register_all()`：加快捷 import + 进 `classes` 元组 | 路由选中后**无法执行** |
| ③ | `capabilities.yaml` 的 `workflows:` 段加条目（≥1 条 examples） | **向量路由对它失明** |

`name` 必须满足 `^[a-z][a-z0-9_]*$`（**纯蛇形、不带点** —— 带点是 capability 的命名空间，
`manifest.py::_WF_NAME_RE` 会拒绝）。②③ 一致性由 `TestWorkflowManifestAlignment` 双向锁定（含幂等）。

```bash
cd backend && "…/.venv/Scripts/python.exe" -m pytest tests/test_layer_consistency.py -q --no-cov
```

---

## 5 · 加一个 MCP Server

**MCP 不是新的一层，是 Tool 的对外协议封装。**

1. `mcp_servers/servers/<name>.py`：继承 `MCPServer`（`mcp_servers/manager.py`），
   声明 `name` + `description` + `list_tools()` + `call_tool()`。
2. `mcp_servers/servers/__init__.py::register_all()` 加一行 `manager.register(XxxMCPServer())`。
   ❌ **不要**在 `server.py` 里逐条 `manager.register(...)`。

**参数禁止手写**：有对应 Tool 的，一律
`langchain_tool_to_mcp_meta(tool, name="短名", description="对外描述")` 从 `args_schema` 派生
（由 `TestMcpListToolsDerivation` 锁死）。

```python
from mcp_servers.schema_adapter import langchain_tool_to_mcp_meta

def list_tools(self) -> list:
    return [
        langchain_tool_to_mcp_meta(my_tool, name="my_short_name",
                                   description="面向外部 client 的描述"),
    ]
```

确实**没有** Tool 等价物的（如 `rag.list_documents`、`sql.list_tables`），才允许手写参数表并**注明原因**
（属规范 §4 **E4** 例外）。

**两个出口必须一致**：标准协议端点（`mcp_servers/protocol_app.py`，端口 8091，工具集自动 = `manager.discover()`）
与 REST（`app/api/routes/mcp.py` 的 `/mcp/tools|/servers|/call`）**不得有第二份清单**
（`TestProtocolEndpointParity`）。

---

## 6 · 加一个域图（新垂直域）

⚠️ **本节比规范 §5 的清单多了 2 处隐藏接线**（实测得出，规范里没写）。漏了会「注册成功但永远不触发」。

| # | 文件 | 做什么 | 漏了会怎样 |
|---|---|---|---|
| 1 | `<domain>/register.py` | `domain_graph_registry.register(DomainGraph(name=…, node_name=…, label=…, adapter=…))` | 域图不存在 |
| 2 | `backend/domains/__init__.py` | **加一行 import** | 上面那步永远不执行 |
| 3 | `orchestration/graph/<domain>_prefilter.py` | 廉价规则判定「是不是这个域的请求」，暴露 `try_<domain>_prefilter(query, state) -> dict \| None` | **域永远不触发**（主 Router 会把请求判成 rag.search 之类） |
| 4 | **🔴 `orchestration/graph/router_node.py`** | **把新 prefilter 插进预过滤链路**（当前顺序硬编码：CS → 旅游 → CS 语义兜底 → 三层 Router） | **同上，域永远不触发** ← 最容易漏 |
| 5 | `config/<domain>.py` | `<DOMAIN>_ENABLED`（**默认 `false`**，与 `CS_ENABLED`/`TRAVEL_ENABLED` 同策略）+ 阈值集中于此，不散落魔数 | 未验收的域直接对全量流量开放 |
| 6 | 域图内部节点 | 照 §3.1 形态：`slot_filler`(可选) + `supervisor` + N 个 expert + `validator`/`repair`(可选) + `reporter` | — |

**不要改 `builder.py`** —— 域图节点由 `domain_graph_registry.get_all()` 自动发现并布线（节点 + 条件边 + 直连 `END`）。
域图**自带 reporter**，直连 END，不走主图 reporter。

```python
# <domain>/register.py
from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import domain_graph_registry
from backend.orchestration.graph.<domain>_graph_node import <domain>_graph_node

domain_graph_registry.register(DomainGraph(
    name="<domain>",                       # 与 router_node 里设置的 route_mode 一致
    node_name="<domain>_graph_node",
    label="<中文标签>",                     # 前端可视化用
    adapter=<domain>_graph_node,           # (state: dict) -> dict
))
```

**prefilter 契约**（照 `travel_prefilter.py` 抄，与本域 config 开关配合）：

```python
def try_<domain>_prefilter(query: str, state: dict) -> dict | None:
    """命中 → 返回主图 state 更新 dict（含 route_mode="<domain>"）；未命中/域关闭/异常 → None。"""
    try:
        from backend.config.<domain> import <DOMAIN>_ENABLED
        if not <DOMAIN>_ENABLED:
            return None
    except Exception:
        return None
    if not is_<domain>_request(query):     # 纯函数、可单测
        return None
    return {"route_decision": None, "route_mode": "<domain>", "<domain>_context": {...}}
```

**判定口径务必保守**，并写清负向断言。参考 `travel_prefilter.py` 的教训：
「近 3 天 / 最近 30 天」这类业务时间窗必须排除，否则「福州的近3天订单量」会被抢进旅游域；
且 lookbehind 必须同时排除数字（只写 `(?<!近)` 时，「最近30天」会在第二位数字处匹配出 `0天`，断言形同虚设）。

**验收开关**：新域默认 `ENABLED=false`，先在本地/白名单验完再放量（参考 `CS_ROLLOUT_WHITELIST` 的灰度形态）。

---

## 7 · 加一个 Agent / 专家

项目里 Agent/专家契约有 **4 套**（规范 §4 **E1**，属登记例外）：

| | 契约 | 现状 |
|---|---|---|
| ① | `skills/base.py::BaseSkill` | 12 个（业务能力，**不是** agent） |
| ② | `agents/capability/base.py::BaseCapability` + `BaseAgentSkill` | **仅 1 个实现**（`InventoryAnalyzer`） |
| ③ | `customer_service/experts/`：`run_expert_safely` + `ExpertResult` | 5 个（绑 `response_draft`/`evidence`） |
| ④ | `travel/experts/base.py`（同形副本） | 4 个（传结构化 POI/行程） |

👉 **新业务 agent 优先用 ②**（`BaseAgentSkill`），在 `agents/capability/` 下实现。
③④ 字段语义不同、**刻意不复用**（两个域包顶部已写明），不要为了「统一」硬套成假复用。

---

## 8 · 隐藏接线点总表（文档没写、代码里必须做）

**这是本手册最该逐条核对的一节。** 任何一项漏了，症状都是「看着都对，就是不生效」。

| 触发场景 | 隐藏接线 | 漏了的症状 |
|---|---|---|
| 新 capability | `direct_executor.py::_USER_CAP_LABELS` 加中文标签 | 用户看到标题「信息查询」 |
| 新 prefilter / 新域 | `router_node.py` 里插进预过滤链路 | 域**永远不触发** |
| 新域 | `config/<domain>.py` 的 `<DOMAIN>_ENABLED` | 未验收域全量放量 |
| 新写副作用 Tool | `security/tool_approval.ensure_approved()` | 绕过人工审批门 |
| 多 Tool 的 Skill | `_select_tool` 里**裁 params 到目标 Tool 签名** | LangChain `invoke` 抛未知参数 |
| 新 Skill | `skills/__init__.py` 的 re-export / `__all__` | 包面不一致 |
| 覆写 `execute()` | 自己保证输出契约 + 登记 §4 E2 | 下游按字符串处理处崩溃 |
| 改 params_schema / 描述 / prompt | 跑 planner 评估（`datasets/planner_params.json`） | 契约回归无人察觉 |
| 改域 TTL | `checkpointer_cleanup.py`（三方共用，**全进程单例**） | 三处不一致 |

---

## 9 · 验证与门禁

**改完必须跑到全绿，否则等于没改完。**

```bash
# 项目自己的 venv（托管 python 没有项目依赖，连 pyyaml 都没有）
PY="D:/Program Files/workplace/agent/.venv/Scripts/python.exe"

cd backend
"$PY" -m pytest tests/test_registry_consistency.py \
                 tests/test_layer_consistency.py \
                 tests/test_adr0001_dual_registry_merge.py -q --no-cov
```

> ⚠️ **局部跑 pytest 必须加 `--no-cov`。** `pytest.ini` 的 `addopts` 写死了
> `--cov=backend --cov-report=xml:coverage.xml --cov-fail-under=55` 且**无运行范围隔离** →
> 跑子集时用例全绿但进程 `EXIT=1`（`Coverage failure: total of 9 < fail-under=55`），
> 并**覆写项目级** `coverage.xml` / `.coverage` / `htmlcov/`（三者 gitignore，不影响仓库，
> 但会让别人读到假的覆盖率数字）。

**守护测试矩阵**

| 规则 | 守护测试 |
|---|---|
| capability 注册表从 Skill 派生（非硬编码） | `test_registry_consistency.py::TestCapabilityDerivation` |
| manifest ↔ skills registry 双向对账 | `test_registry_consistency.py::TestCapabilityManifest` |
| Skill `params_schema` 与 Tool 实际参数不脱节 | `test_registry_consistency.py::TestSkillToolAlignment` |
| MCP 参数从 `args_schema` 派生 | `test_registry_consistency.py::TestMcpListToolsDerivation` |
| 协议端点工具集 == `manager.discover()` | `test_registry_consistency.py::TestProtocolEndpointParity` |
| 每个 Skill 的节点名同现在 `_skill_nodes` 与 `CAPABILITY_MAP` | `test_adr0001_dual_registry_merge.py` |
| **所有 `@tool` 必须已注册** | **`test_layer_consistency.py::TestToolLayerCompleteness`** |
| **`@tool` 不得定义在 `skills/` 下** | **`test_layer_consistency.py::TestToolLayerOwnership`** |
| **每个 Skill 都有图节点 + 节点不得集中代注册** | **`test_layer_consistency.py::TestSkillNodeSelfRegistration`** |
| **`register_all()` ↔ manifest workflows 双向对齐 + 幂等** | **`test_layer_consistency.py::TestWorkflowManifestAlignment`** |
| **命名约定（capability 带点 / workflow 不带点）** | **`test_layer_consistency.py::TestNamingConvention`** |

**其它验证入口**：`python scripts/tool_quality_check.py`（Tool 层诊断）；
管理端只读总览 `GET /api/agents` + `GET /api/capabilities`（B13，可对账 Skill/Capability 台账；
挂载点见 `backend/app/api/router.py`，以实际路由为准）。

**「新能力到底有没有进 Planner 视野」——最该跑的一条自检**
（⚠️ **必须在仓库根运行**；在 `backend/` 下会 `ModuleNotFoundError: No module named 'backend.skills'`）：

```bash
cd "D:/Program Files/workplace/agent"
"D:/Program Files/workplace/agent/.venv/Scripts/python.exe" -c "
import backend.skills
from backend.orchestration.tool_registry import tool_registry
print(len(tool_registry.CAPABILITY_MAP), sorted(tool_registry.CAPABILITY_MAP))"
```

2026-09-16 实测输出 **17 个**（含 3 个 `routed:false` 内部能力 —— 这正是 E9 的证据：
`CAPABILITY_MAP` 就是 Planner prompt 的数据源，所以内部能力**对它可见**）。
你新加的能力**没出现在这里 = 永远不会被调用** —— 不报错、不告警。

---

## 10 · 易踩的坑（都是实测踩过的）

1. **`async def execute` 在 AST 里是 `AsyncFunctionDef`** —— 写脚本统计「谁覆盖了 execute()」时只查
   `ast.FunctionDef` 会全漏。
2. **两个同名 `tool_registry` 模块**（看 import 路径，别看名字）：
   `backend/tools/tool_registry.py` = **Tool** 表；`backend/orchestration/tool_registry.py` = **Capability** 表。
3. **`routed: false` 抢不走路由**（不在向量索引里），但**对 Planner 可见**（§3.6 / 规范 E9）。
4. **examples 可能是承重件**：若某问句在 RuleRouter 只命中**弱信号**（confidence `0.72 < 0.8` 阈值），
   它会 fallback 到向量路由，那条**逐字样例就是它唯一的确定性来源，删不得**
   （如 `competitor.analyze` 的「监控一下竞品价格变化」）。
   **改/删既有 examples 前，先查 `rule_router.py` 的关键词组与阈值。**
5. **`reason` 字段是人写的解释，不是事实** —— 要 grep 验证调用点。实测两条竞品
   `reason` 写「仅 selection_decision workflow 内部使用」，而该 workflow 是**直接读 store**、
   根本没走那两个能力（规范 §7 #18）。
6. **不要并行编辑同一个文件** —— 同时发两个编辑会 **lost update**（后写覆盖先写），
   而工具**两个都报 success**。同文件编辑一律**串行**，改完**回读确认**。
7. **CRLF**：`capabilities.yaml` 是 CRLF，规范文档是 LF。改完用字节数核一下别被改掉。
8. **`.bat` 必须 ASCII-only**（cmd 按 GBK 解析，中文注释会破坏控制流）。

---

## 11 · 改 / 删 与破坏性变更

契约（`params_schema` / `output_type` / capability 名）与消费方（Planner、Reporter）**同仓同发布**，
**不加版本号**，靠三条兼容：

1. **向后兼容演进**：只**新增可选参数**；不改既有参数语义；不删 / 不改名已有字段。
   **破坏性变更 = 新 capability 名 + 旧 Capability 保留废弃期**。
2. **测试守护**：见 §9 矩阵 + `test_base_output_contract.py` + e2e 离线故障注入集
   （`backend/evaluation/datasets/e2e/cases.jsonl` 的 `F-*` 用例）。
3. **变更跑评估**：改 `params_schema` / 描述 / prompt 后跑 planner 评估（live），
   关键写操作参数用 `backend/evaluation/datasets/planner_params.json` 的 `expected.params` 断言。

仅当工具以**独立部署制品**对外（MCP Server 发布、跨团队共享）才引入显式版本号。

**删除**：先摘路由（`capabilities.yaml`）→ 再摘注册（`registry.py` / `register_all()`）→ 最后删代码。
反向顺序会让路由选中一个不存在的目标。

---

## 12 · 什么时候必须登记 §4 例外台账

只要你的做法**偏离了 §2–§7 的规范**，就必须在规范文档 §4 追加一行，写清：**现象 / 现状 / 理由 / 处置**。
台账内的偏离**允许存在**，但必须「有明确理由、有注释说明、**不阻碍后来者照规范写新代码**」。

**新代码不得以台账里的旧例外为由复制偏离。** 已知 E1–E9（截至 v1.2）：

| ID | 一句话 |
|---|---|
| E1 | Agent/专家契约有 4 套（新业务 agent 优先用 ②） |
| E2 | 覆盖 `execute()` 绕过输出归一化（现 2 例） |
| E3 | capability 命名风格个别不一致（`travel.poi_search`；新 capability 用 `<域>.<动词>`） |
| E4 | MCP 部分工具参数手写（无 Tool 等价物） |
| E5 | Tool 注册表无运行时消费方（定位是静态发现 + 查重） |
| E6 | 两套路由索引（生产一律以 manifest + `vector_router` 为准） |
| E7 | 两个同名 `tool_registry` 模块 |
| E8 | 存量 18 个 Tool 返回非 JSON（**不要顺手改造**） |
| E9 | `routed: false` 只约束路由层，Planner / Critique 未过滤 |

---

## 13 · 收尾清单（提 PR 前逐条打勾）

- [ ] 声明处 + 派生处都改了（§1 事实源清单对照）
- [ ] 命名合规：capability `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`（恰一个点）；workflow `^[a-z][a-z0-9_]*$`（不带点）
- [ ] §8「隐藏接线点总表」逐条核过
- [ ] 新增 `@tool` 已在文件底部注册，且**没有**定义在 `skills/` 下
- [ ] 新 Tool 返回 JSON、失败返 `{"error": …}`；写副作用过了审批门
- [ ] `capabilities.yaml` 的 examples ≥5（≥2 是硬下限）、落能力边界内、块内不重复；`routed:false` 写了 `reason`
- [ ] 新增 capability 加了 `_USER_CAP_LABELS`
- [ ] 三个守护测试文件**加 `--no-cov`** 跑绿
- [ ] 改了 `params_schema` / 描述 / prompt → 跑了 planner 评估
- [ ] 有偏离 → 规范 §4 追加了台账行
- [ ] 规范文档 §1「当前规模」数字已更新（12 Skill / 17 capability / 34 Tool / 4 workflow / 2 域图 / 2 MCP）

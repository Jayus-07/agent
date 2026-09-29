# Extension Guide — 扩展指南（平台维护阶段）

日期：2026-09-29（STOP H 收官）
性质：新增各类资产的**入口指南**。每类给：决策要点 → 接线流程 → 必跑验证；完整接线点总表与模板以上游手册为唯一事实源（本文不复抄，G2）。

> 上游权威：`docs/2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md`（隐藏接线点总表）＋ `AGENTS.md`（硬约束）＋ [Frozen-Contracts.md](Frozen-Contracts.md)（动契约前必读）。

**通用验证（任何资产改动后必跑）**：

```bash
cd backend && ../.venv/Scripts/python.exe -m pytest \
  tests/test_registry_consistency.py tests/test_layer_consistency.py \
  tests/test_adr0001_dual_registry_merge.py tests/skills/test_base_output_contract.py \
  -q --no-cov
```

⚠️ 局部跑必须带 `--no-cov`（pytest.ini 挂死覆盖率门，否则全绿也 EXIT=1）。

---

## 1. 新增 Tool（原子操作）

**先做契约选型（STOP G 两型契约）**：

| Tool 的输出给谁 | 型 | 写法 |
|---|---|---|
| LLM 直接阅读（检索结果、报告、正文） | `text` | 返回 Markdown/纯文本 str；失败用 `[X ERROR] ...` 文本或 raise 交 Skill 重试 |
| 程序消费（下游步骤、adapter、MCP） | `structured` | `shared/tool_envelope.py` 的 `tool_success_result(data, **extra)` / `tool_error_result(msg)`——成功 data 嵌套、失败带可执行 error；地图域走 `tools/map/_base.py` 的 `ok/fail/not_configured` |

流程：`backend/tools/<域>/<mod>.py` 写 `@tool` 函数（无状态、返回 JSON 字符串、失败显式出现在返回值）→ **文件底部** `tool_registry.register(my_tool, __file__)` → 副作用 Tool 加 `security/tool_approval.ensure_approved()`，`user_id` 取 `tools/session.get_tool_user_id()`。

红线：不得定义在 `skills/` 下；不手写返回类型元数据（守卫测试会比对 Tool 模块形状与 Skill 声明）；存量 18 个 Markdown 工具是 E8 例外，新 Tool 不参照。

## 2. 新增 Skill + Capability

五处接线（漏 capabilities.yaml = 启动 fail-fast）：

1. `backend/skills/<name>/skill.py`——继承 `BaseSkill`，只写执行实现与 `name`；
2. **显式声明输出契约**（STOP G，禁止隐式默认）：`output_type = "text" | "structured"`（混合 capability 用 `output_types` dict）——声明必须与 `_tool_fn` 指向的 Tool 实际输出型一致（守卫测试锁定）；
3. `__init__.py` 自注册图节点；
4. `skills/registry.py::_instances`；
5. `capabilities.yaml` 补 capability 段（唯一业务 metadata：description/examples/params_schema）。

易漏接线：direct 支线可达的 capability 补 `orchestration/graph/direct_executor.py::_USER_CAP_LABELS`。
改 `params_schema`/描述/prompt 后另跑 planner 评估（`backend/evaluation/datasets/planner_params.json`）。

## 3. 新增 Workflow

三处：类实现 → `orchestration/workflows/__init__.py::register_all()` → `capabilities.yaml` 的 `workflows` 段（漏第三处 = 向量路由失明）。纯蛇形命名，不带点。

## 4. 新增 Domain（域图）

推荐技能：`agent-platform-add-domain-graph`。要点：

1. 域图自注册（`register.py` → `domains/__init__.py` 触发 → builder 自动布线，**不改 builder.py**）；
2. **prefilter 必须插进 `router_node.py`**，否则域永不触发；
3. 遵守 STOP E 语义边界（`DomainGraph.name` / subflow metadata 是冻结契约）；
4. 域内专家走 Expert Runtime：专家经 `run_expert_safely`（域 base 薄适配），不自己写计时/异常包装；
5. 新增第三方服务必须补 [domain-service-map.md](domain-service-map.md) 五行（用途/消费方/凭据变量/开关/降级）；
6. 域开关代码默认全关，由根 `.env` 决定取值。

## 5. 新增 MCP Integration

MCP 是 Tool 对外的**第二出口**（Integration Adapter），不是内部链路的一环：

1. 继承 `mcp_servers/manager.py::MCPServer`，实现 `list_tools()/call_tool()`；
2. `mcp_servers/servers/__init__.py::register_all()` 注册；
3. 参数一律 `langchain_tool_to_mcp_meta` 从 Tool 的 `args_schema` 派生，**禁止手写**；
4. 对外返回走 `manager.route` 统一信封 `{ok, tool, server, result|error}`——这是对外契约，禁与内部封套互相迁移（[Frozen-Contracts.md](Frozen-Contracts.md) §10）。

## 6. 变更分级速查

| 改什么 | 门 |
|---|---|
| 普通资产（上述 1-5） | 通用四件套 + 该域测试 |
| 触碰 [Frozen-Contracts.md](Frozen-Contracts.md) 任一条 | 停：新 ADR + 台账 + 原子迁移 + 兼容期 |
| 只做口径校准 | 更新文档「最后验证」日期并注明口径，不动码 |

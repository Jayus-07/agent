# TASK: 管理端治理 P1+P2 优化（admin-governance-p1p2）

> 来源：2026-11-10 管理端 Agent/Skill/Tool/MCP 治理审查。范围：P1（4 项）+ P2（2 项），
> 基于最新 main（6a560ac）实施，PR 合并。用户已授权提交与合并。
> 审查阶段明确不参考 `docs/tasks/admin-catalog-refactor/PAGE-SPEC.md`（用户指定忽略）。

## 目标与范围

| 项 | 内容 | 状态 |
| --- | --- | --- |
| P1-1 | 后端去重：`admin_mcp`/`admin_tools` 逐字相同的 `_invoke_*_llm`、`_redact_urls`+`_URL_PATTERN` 下沉 `backend/shared/admin_llm.py`；semantic suggest 审计补 model 留痕 | ✅ b7e44b3 |
| P1-2 | `GET /admin/mcp/tools`、`GET /admin/mcp/connections/{id}/tools` 载荷裁剪：新增 `_tool_list_view`，hash/审核留痕/语义契约/examples 等大字段仅详情端点下发；invocation 保留（来源徽标+HTTP 编辑预填依赖） | ✅ b7e44b3 |
| P1-3 | 前端 `errMessage` 三处重复下沉 `api/client.ts` | ✅ f197abd |
| P1-4 | `api/registry.ts` 运行时视图改名 `getRuntimeMcpServers/getRuntimeMcpTools`（+Runtime 类型前缀），消除与治理视图 `api/mcp.ts#getMcpTools` 同名不同义 | ✅ f197abd |
| P2-5 | tools/page.tsx 2093→786 行、mcp/page.tsx 1242→623 行按组件拆分（纯移动零行为变化） | ✅ f01e02c |
| P2-6 | react-query 增量样板：FailureAnalysisSection、ConnectionHealthSection 迁移 useQuery；frontend-guide 增补数据层约定 | ✅ d4c85dd |

## 明确不做

- 静态工具（代码事实源）不加运行时启停开关——避免第二事实源，破坏 consistency + CI 守护前提。
- 批量审核/授权端点（P3，随规模再做）；写工具逐个人工确认红线不破。
- `/admin/tools/errors` 跨副本采集（P3，扩副本前处理）。
- admin_mcp service 层抽取（本轮不做，P1/P2 落地后另行评估）。

## 验收标准

- 后端定向测试全绿（admin_mcp/semantic/failure_analysis/tools_stats/failure_probe/discovery + mcp_dynamic_e2e）。
- frontend-admin：tsc --noEmit 干净 + vitest 全量绿（590 用例）+ `next build` 成功。
- devctl 用本 worktree 代码重建后健康检查通过。
- PR 合并 main，100% 变更经 PR 预检。

## 完整链路评审记录（2026-11-10，实施中途执行）

六条链路逐一核实（消费方全仓 grep + tsc + 测试）：

| 链路 | 路径 | 结论 |
| --- | --- | --- |
| A 目录读 | `GET /admin/mcp/tools`（_tool_list_view）→ getMcpTools（McpToolSummary[]）→ tools 页目录行/计数 + mcp 页计数/抽屉/HTTP 编辑预填 | 消费字段 12 项全部保留；全仓无其他列表消费方；tsc+590 用例锁 |
| B 详情读 | `GET /admin/mcp/tools/{id}`（_tool_view 全量+snapshots+grants）→ ToolDrawer 六 tab | 未改动 |
| C 审核写 | review/grant/disable/semantic/*（幂等键+CAS+审计）→ 缓存失效+列表刷新+详情强拉 | semantic 审计补 model 留痕；其余未改动 |
| D 连接链路 | connections CRUD/test/discover/enable → refresh_now + 15s 轮询 + 写调用强一致复核 | 未改动 |
| E LLM 辅助 | failure-analysis / semantic suggest → shared/admin_llm（redact→invoke→留痕） | 去重后单一实现，7+11 用例过 |
| F 内部 MCP 视图 | `/api/mcp/*`（mcp_servers/manager）→ registry.ts Runtime 前缀 → skills 页 | 仅命名去歧义，端点未动 |

评审产出（已在本轮处理或记录）：

1. 列表载荷契约无测试锁定 → 已补 `TestToolListPayload`（含列表⊂详情一致性断言）。
2. `getConnectionTools` 前端无消费方（连接抽屉用本地过滤）——本次纯移动未动，遗留清理项。
3. tool-skill-guide 仅粗粒度描述管理端入口，无字段级契约记载，无需改；frontend-guide 补数据层约定。
4. react-query Provider 已常驻 layout.tsx → 阶段 4 直接 useQuery，无需新增 Provider。

## 环境/复现备注

- 本地跑治理定向 pytest 必须**移开 .env**（ALLOW_UNAUTHENTICATED=true 会改变敏感端点门禁语义导致 403）并显式指定 *_test 库 env（对齐 CI 无 .env 语义）；测试库 schema 用 `scripts/init_db.py`（MEMORY_PGDATABASE=agent_memory_test BUSINESS_PGDATABASE=agent_business_test）补迁移。

# PROGRESS: admin-governance-p1p2

> Worktree: `D:\Program Files\workplace\agent-wt-gov-p1p2`
> Branch: `refactor/admin-governance-p1p2-20261110`（基线 origin/main 6a560ac）

## 已完成（含验证证据）

| Commit | 内容 | 验证 |
| --- | --- | --- |
| b7e44b3 | P1-1 后端去重（shared/admin_llm）+ P1-2 列表裁剪（_tool_list_view）+ semantic 审计 model 留痕 | 定向 pytest 64 passed + test_mcp_dynamic_e2e 16 passed |
| f197abd | P1-3 errMessage 下沉 + P1-4 registry Runtime 改名 + McpToolSummary 类型拆分 | tsc 干净；vitest 87 文件 590 用例全绿 |
| f01e02c | P2-5 tools/mcp 页面拆分（18 文件，纯移动） | tsc 干净；590 用例全绿（page.test 零改动通过） |
| d4c85dd | 载荷契约测试（TestToolListPayload）+ P2-6 react-query 样板（2 section）+ fixture 收紧 + mountPage 包 Provider | test_admin_mcp_api 23 passed；tsc 干净；590 用例全绿（含时序修正后连跑复核） |

测试补充说明：

- `TestToolListPayload` 4 用例：摘要字段齐全 / 大字段不随列表下发 / 列表视图 ⊂ 详情视图一致性。
- mcp 页 fixture 收紧为 `McpToolSummary` 精简形状（消费端锁定裁剪契约）。
- `waitForText` 助手：react-query 异步解析时序修正，断言保留未删。
- 探查中发现的历史事实：HTTP 工具编辑表单的 Schema 预填在列表数据流下历来恒为默认值（snapshots 仅详情端点返回），本次保持既有行为并注释说明。

## 文档同步

- `docs/development/frontend-guide.md`：新增「5. 服务端数据获取（react-query 增量迁移）」——根 Provider 常驻、增量边界、queryKey/invalidate/双形状/测试包裹约定、errMessage 与 Runtime 前缀命名约定；原「验收」顺延为第 6 节。
- 本任务文档 TASK.md / PROGRESS.md 新建并随 PR 提交。

## 剩余步骤

1. 全量定向验证（后端 6 个测试文件 + e2e；前端 tsc + vitest + next build）。
2. devctl 用本 worktree 重建受影响服务（app/frontend-admin）并健康检查。
3. push 分支 → PR → 合并（用户已授权）。

## 遗留/风险

- **遗留清理项（非本轮范围）**：`api/mcp.ts#getConnectionTools` 前端无消费方（连接抽屉用本地 mcpTools 过滤），后续可评估删除或接线。
- **风险**：列表载荷裁剪是内部 admin API 契约变化，消费方仅本仓两页（已同 PR 适配 + 契约测试锁定）；若有仓外脚本直连该接口（未发现），需同步。
- 本地验证注意事项见 TASK.md「环境/复现备注」（.env 干扰、测试库迁移）。

## 独立评审（2026-11-10，新会话，基于合并后 main）

> 评审对象：PR #414（merge b5b021c）在 main 上的最终状态；评审会话独立执行，
> 以合并后代码与本地复跑为准，不引用 PR 描述充当结论。
> 评审分支：`review/admin-gov-p1p2-20261110`（基线 origin/main e08b868）。

### 六链路结论

| 链路 | 结论 | 证据 |
| --- | --- | --- |
| A 目录读 | **通过**。裁剪后列表仅摘要 12 字段（+端点级 grants），与前端 `McpToolSummary` 逐字段对齐；tools 页目录行/计数、mcp 页计数/连接抽屉行/HTTP 编辑预填、`DynamicSourceBadge` 的消费字段全部在保留集内，无越界访问 | `backend/app/api/routes/admin_mcp.py:1293-1314`（视图）、`:1343`/`:1378`（两列表端点）；消费方 `frontend-admin/src/app/tools/page.tsx:405-431`、`src/app/mcp/page.tsx:280-342,555-575`、`src/app/mcp/HttpToolForm.tsx:84-94`（预填仅用 invocation/tool_name/description）、`src/app/tools/badges.tsx:30-44` |
| B 详情读 | **通过**。详情端点全量视图 + snapshots(≤20) + grants 未改动；`ToolDrawer` 只收 `toolId` 强拉 `getMcpToolDetail`，六 tab（overview/schema/policy/ai/debug/history）消费字段全部由详情载荷覆盖 | `backend/app/api/routes/admin_mcp.py:1387-1428`；`frontend-admin/src/app/tools/ToolDrawer.tsx:53-58,68-93`；类型对齐 `frontend-admin/src/api/mcp.ts:161-176`（McpTool 含 snapshots/grants 可选字段） |
| C 审核写 | **通过**。review/grant/disable 端点零改动；semantic suggest 仍走 `run_governance_mutation`（幂等键+CAS+审计统一入口），新增审计字段 `model` 与 failure_analysis 同口径（LLM 调用后取名、软失败留空）；前端 `handleMutated` = invalidate 详情缓存 → 页面列表刷新 → `load(true)` 强拉详情 → revision 重挂 | `backend/app/api/routes/admin_mcp.py:1606-1626`；`frontend-admin/src/app/tools/ToolDrawer.tsx:88-93` |
| D 连接链路 | **通过，无波及**。PR 触及文件不含连接 CRUD/test/discover/enable 与运行时侧 `verify_write_authorization`；`refresh_now` 调用点未动；健康巡检周期仍由后端 probe_interval 驱动（前端 mcp 目录无 setInterval 轮询代码，原实现即非前端轮询） | `backend/app/api/routes/admin_mcp.py:483`；`backend/infra/mcp_registry/store.py:397`、`backend/skills/mcp_dynamic/skill.py:55`（均不在 PR #414 diff 中） |
| E LLM 辅助 | **通过，逐字等价**。`redact_urls`（正则/占位符/str-or 空串处理）、`invoke_admin_llm`（消息构造、to_thread、多块 content 拼接）、`current_llm_model`（软失败留空）与合并前两处私有实现逐字一致；调用点经 `import ... as _redact_urls` 别名保持零改动；旧函数/旧正则无残留 | `backend/shared/admin_llm.py:16-52` vs 原 `admin_mcp.py`/`admin_tools.py` 删除段（PR diff）；别名导入 `admin_mcp.py:49`、`admin_tools.py:39`；消费点 `admin_mcp.py:332-338`、`admin_tools.py:922` |
| F 运行时视图 | **通过，仅命名变化**。`registry.ts` 端点 URL（`/api/mcp/servers`、`/api/mcp/tools`）与响应形状未动；skills 页与测试 mock 全部改用 Runtime 前缀；`getMcpTools` 剩余引用均为治理视图合法消费 | `frontend-admin/src/api/registry.ts:103-130`；`src/app/skills/page.tsx:24-27,85-98`、`src/app/skills/page.test.tsx:6-17` |

### P2-5/P2-6 抽查

- P2-5 纯移动：抽查 `tools/badges.tsx` 与拆分前 `page.tsx` 对应函数逐字一致（仅 `function`→`export function`）；page.test.tsx 拆分 commit 零改动通过。实际行数核对：tools 2088→786、mcp 1239→623（TASK.md 记载 2093/1242 有 ±5 行出入，不影响结论）。
- P2-6 react-query：`ConnectionHealthSection` 原 `useEffect([conn.id])` + setState → `useQuery(['admin','mcp-health',conn.id])` + select，切换连接重拉语义等价；`FailureAnalysisSection` 原 `loadRecent` useEffect → `useQuery(['admin','tool-failure-analyses'])`，写后 `invalidateQueries`。探活按钮禁用态、错误重试语义保留。

### 独立复跑验证（评审会话本机执行）

| 项 | 命令/环境 | 结果 |
| --- | --- | --- |
| 后端治理定向 pytest | 移开 .env + CI 等价 env（PGHOST=127.0.0.1 PGPORT=5433、*_test 库）+ 库凭据；7 文件（admin_mcp/semantic/failure_analysis/tools_stats/failure_probe/discovery/mcp_dynamic_e2e） | 72 passed + 16 skipped（0 errors） |
| 前端 TypeScript | `npx tsc --noEmit`（frontend-admin） | 干净（exit 0） |
| 前端 vitest 全量 | `npx vitest run`（frontend-admin） | 87 文件 590 用例全绿 |

### 评审发现与处置

1. **[P2，已修] TestToolListPayload 不被任何 CI 门禁执行**：`admin_mcp.py`/`admin_tools.py` 不在 `rag_smoke_path_filter.py` 相关路径中（admin 改动不触发 RAG Smoke 完整清单），`api-structural.yml` 只在 router/observability 路径触发，`admin-observability-auth` 只跑 `test_admin_observability_reads.py`——后端载荷契约测试只在本地跑，属漂移防线缺口（前端 vitest mock 与后端解耦，锁不住 `_tool_list_view` 单侧漂移）。处置：治理面 4 路径入 `PR_STATIC_ONLY_ENTRIES`（PR 触发免费预检、main push 不触发真实评测）+ rag_smoke 定向清单加 `test_admin_mcp_api.py::TestToolListPayload`（3 纯函数用例、无 DB）+ `test_rag_smoke_path_filter.py` 补正反触发回归与清单守卫断言（防"过滤器说触发、清单没跑用例"空转）。
2. **[已修正] TestToolListPayload 用例数**：PROGRESS 原文"4 用例"实为 3 个新增契约用例（summary_fields_present / detail_only_fields_absent / summary_view_is_subset_of_detail_view）+ 1 个原有测试 `test_connection_detail_requires_idempotency_free_read` 因插入位置物理落入该类（属 TestInputValidation 的输入校验用例，与载荷契约无关）。该测试已移回 `TestInputValidation`，`TestToolListPayload` 恢复纯契约组（3 用例）。
3. **[已修正] TASK.md 行数**：tools 2093→786 实测 2088→786；mcp 1242→623 实测 1239→623，TASK.md 已按实测修正（±5 行为拆分 commit 前统计口径出入，不影响"纯移动"结论）。
4. **[已收口] getConnectionTools 死代码**：全仓 grep 证实无 import/调用（连接抽屉用 `getMcpTools()` 本地过滤）；已从 `api/mcp.ts` 删除并注释说明（后端 `/connections/{id}/tools` 端点保留，供直连与契约测试使用）。

### 评审结论

PR #414 六链路全部通过，无 P1 级问题；P2 级 CI 门禁缺口与死代码已随评审分支 `review/admin-gov-p1p2-20261110` 修复走 PR。红线核查：静态工具无运行时启停改动；写工具审核仍为逐条人工确认路径（review/grant 端点单工具粒度未变）。

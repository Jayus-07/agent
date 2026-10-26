# Batch 0 进度（2026-10-27）

## 已完成（待用户确认提交）

### 核查结论

- PR #107 HEAD `9a6efa0`，基点 `e5178cd`，40 文件 +5082/−40；5 项 Codex
  Review 问题全部在代码层复核属实。
- CI 基线判定：
  - `Detect RAG changes` / `RAG Smoke`：**PR 引入**——迁移
    `114_mcp_capability_center.sql` 未在 path filter 清单登记（exit 2 级联）。
  - `Evaluation V2 free contract tests`：**main 既有故障**（`ragas` 缺失，
    main fb9fd5f/c5782c2 同款失败，PR 未触碰 evaluation）。
  - `frontend-admin-functional`：**main 既有故障**（Vitest rbac.test.ts
    ECONNREFUSED :3000，main c5782c2 同款失败）。
- 多 Worker 撤权/禁用语义：检查点齐全（guard/router/direct_executor/base
  四处过 `product_allows_capability`）但全部基于 15s 轮询快照，无强一致
  路径；ADR 诚实记录 15s 语义，前端却宣称"即时生效"——不满足计划 E06
  （高风险工具不得在延迟窗口执行）。

### 修复（11 文件，+509/−22）

> 2026-10-27 用户确认后已按路径限定分批提交并推送：
> `27ff922` fix(mcp) 修复主体 / `814e174` chore(ci) 迁移登记 / `55ae3b1` docs(tasks) 档案。

| 问题 | 修复 |
|---|---|
| P1 Planner 白名单 | `plan_utils.normalize_plan` 改用 `effective_manifest().planner_visible_capability_names`（与 Planner/Critique 同源） |
| P1 审核 CAS | `_execute` 返回 rowcount；approve UPDATE 加 `AND content_hash=%(expected)s AND status<>'retired'`，0 行 → 409 |
| P2 快照签名 | `McpToolRuntime.content_digest`（描述/Schema/治理/示例摘要）参与 `signature()`；新增 `derived_content_digest()` |
| P2 详情缓存 | 模块级缓存失效函数；发现/审核/停用/授权后 `resyncToolDetails`；`merged` 改列表优先 |
| P2 超时字段 | 前端提交/回显 `governance.runtime.timeout_ms`（`McpGovernance` 类型补 runtime） |
| 安全语义（计划额外要求） | `store.verify_write_authorization()` DB 直查强一致；`skill.py` 写操作出网前复核，fail-closed；internal/只读豁免 |
| CI 门禁 | 114 及基线欠账 102–113 全部登记（评测/RAG 5 项 → PAID；RBAC/审批/审计 5 项 + 114 → NON_AI） |
| 文档 | ADR-0011 D2/后果、tool-skill-guide 同步写调用即时拦截语义；前端文案修正 |

### 验证证据（本机 Windows，D:/Python/python.exe）

- `pytest backend/tests/mcp_registry/`：53 passed
- `pytest backend/tests/agents/test_planner_critique.py`：10 passed
- `pytest backend/tests/test_mcp_dynamic_e2e.py`（真 PG:5433 + mock MCP
  server，-m pg）：7 passed（含新增 CAS 并发 2 例）
- `pytest backend/tests/api/test_admin_mcp_api.py`：15 passed
- `pytest backend/tests/orchestration/router/ + context_budget/test_tool_guard.py`：
  150 passed, 1 failed（`test_router_has_no_second_registered_domain_mapping_literal`
  ——main 基线同款失败，非本 PR 回归）
- `pytest tests/test_rag_smoke_path_filter.py`：12 passed；PR 全量变更路径
  模拟 `--event pull_request` → `relevant=true`（预期，PR 含 RAG 路径）
- `frontend-admin`：`tsc --noEmit` 通过；`vitest run` 549 passed / 7 failed
  （rbac、design-tokens 为 main 基线既有失败；RoleGate 3 例为 PR 分支基点
  旧于 main 的既有漂移，PR 未触碰相关文件，合并后以 main 版本为准）

## 未验证 / 阻断

- 容器级部署验证 **UNVERIFIED**：改动在 PR 分支，按 AGENTS.md 待合并授权后
  `devctl.bat rebuild`（注意须以合并后代码构建）。
- 合并 PR #107：待用户额外授权（提交与推送已确认完成）。
- main 既有 CI 故障（ragas、frontend-admin ECONNREFUSED、execution_mode
  硬编码守卫）不在本批范围，需另立修复。

## 下一步

1. 用户确认后提交并推送 `feat/mcp-capability-center`，观察 PR #107 CI 全绿。
2. 获合并授权后合并 PR #107，基于新 main 开 Batch 1（连接中心 + Tool 治理）。

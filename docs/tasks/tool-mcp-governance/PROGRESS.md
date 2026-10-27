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

## 推送后 CI 定性（2026-10-27，HEAD 21e37d2）

- **Detect RAG changes：pass**——迁移登记修复生效。
- **RAG Smoke preflight：13 failed = main 基线回归，非 PR 引入**。
  决定性对比（同机同库 5433/agent_memory 同命令）：
  - PR 代码（worktree）`test_llm_budget.py` 17 passed、
    `test_specialized_budget.py + test_pgvector_store.py` 56 passed；
  - main 代码（c5782c2）同命令 6 failed + 7 failed；
  - 破坏源在 main 基点后合入的提交：`231675e`（改 infra/llm/budget.py，
    预算不拒绝/observe 计数丢失）、`41f69a7`/`d886f7c`（pgvector 指纹
    校验，INDEX_EMBEDDING_MISMATCH）。
  - CI 的 pull_request 事件 checkout 的是 PR⊕main 的 merge 预览，因此带
    上 main 的破坏；RAG Smoke (8 cases) 失败为 preflight 的级联聚合。
- **Evaluation V2 free contract tests：4 failed = main 基线故障**。
  1 × ragas 缺失（main 同款）+ 3 × CI PG service connection refused
  （环境类）；该 workflow 在 main push 10-25/10-26 也均 failure。
- **frontend-admin-functional：main 基线故障**（rbac.test.ts
  ECONNREFUSED :3000，main c5782c2 同款）。
- contract-check / scan / static-structure / frontend-admin-security /
  admin-observability-auth / Evaluation admin build：pass。
- **结论：PR #107 分支未引入任何 CI 回归；PR 引入的门禁问题已修复转绿。**
- 新发现待办：main 的 budget/pgvector 回归（本机可复现，非 flaky）需另立
  任务修复，属于 Batch 1 前的优先阻断候选。

## 下一步

1. 用户确认后提交并推送 `feat/mcp-capability-center`，观察 PR #107 CI 全绿。
2. 获合并授权后合并 PR #107，基于新 main 开 Batch 1（连接中心 + Tool 治理）。

## Batch 1 启动与 main 回归修复（2026-10-27）

- PR #107 已合并（merge commit `58fc327`）；本地 main 与另一会话提交
  `1eeca23`（stabilize retrieval and 4c8g runtime）合流为 `7f78798`，
  合流后 mcp_registry + planner 63 passed（MCP 能力中心完好）。
- Batch 1 worktree：`D:/Program Files/workplace/agent-wt-toolgov`
  （分支 `feat/tool-governance-batch1`，基于 7f78798）。

### 回归根因与修复（3 个测试文件，生产代码零改动）

| 失败 | 根因 | 修复 |
|---|---|---|
| test_llm_budget 6 例 | `231675e` 有意将 max_calls 改为"只记账不拒绝"，漏改测试 | 按新语义改写：observe 不再含 request_calls 标记；拒绝门禁断言迁移到 token/retries 路径；新增"调用数记账不封顶 + token 仍拒绝"回归保护 |
| test_specialized_budget 2 例 | 同上（embedding）+ `1eeca23` 有意移除 rerank HTTP 重试（超时标记 unavailable 不重复投递），均漏改测试 | embedding fixture 内单独收紧 max_total_tokens=3（不动共享 fixture）；rerank 测试改写为"超时一次即停、条目 only primary" |
| test_pgvector_store 7 例 | `41f69a7` 新增索引身份校验，`_StubEmbedding` 缺身份属性 → runtime_fingerprint_missing | 测试桩补齐 `_model_name/_provider/_provider_id/_index_fingerprint`（与生产 _TrackedEmbedding 对齐） |

验证：`pytest tests/test_llm_budget.py tests/rag/test_specialized_budget.py
tests/rag/test_pgvector_store.py tests/mcp_registry/` → **126 passed**。

### 待办

- ~~回归修复提交待用户确认~~ → 已确认提交（`e07f4c1` test(budget) /
  `26f2ecc` test(rag)）并推送；**PR #108** 已创建：
  <https://github.com/Jayus-07/agent/pull/108>
- CI 验证（merge 预览，2026-10-27）：**RAG Smoke preflight 13 failed →
  pass**，RAG Smoke (8 cases) 级联恢复 pass，Detect RAG changes pass，
  contract-check / scan / static-structure / admin-observability-auth /
  frontend-admin-security / Evaluation admin build pass；剩余失败仅
  Evaluation V2 free contract tests（ragas + CI PG service，main 同款）
  与 frontend-admin-functional（ECONNREFUSED :3000，main 同款）——均为
  PR 描述声明的既有故障。
- PR #108 合并待用户授权；合并后开始 Batch 1 主体（连接中心 + Tool 治理）。
- main 既有故障另立任务：Evaluation workflow（ragas 缺失 / PG service）、
  frontend-admin rbac 测试（:3000）。

## Batch 1 主体：HTTP API Tool 统一接入（2026-10-27）

PR #108 已合并（`64091d6`）。Batch 1 第一批：外部 REST API 以"HTTP API
连接 + 受控调用定义"接入既有 ai.mcp_* 注册表——**同一张注册表、同一套
审核/授权/漂移/CAS/强一致撤权链，零新 Runtime**。

### 后端（生产代码）

- **迁移 115**：`mcp_connections.transport` 放宽为
  `streamable_http | http_api`；`mcp_tools.invocation` JSONB（调用定义）+
  快照表同列（已对本机 5433 库执行并验证）。
- **specs.validate_invocation()**：method ∈ {GET, POST}；path 必须相对路径；
  path/query/headers 的 `{name}` 模板只能引用 input_schema 已声明参数；
  禁配 Authorization/Cookie 头（凭据统一注入）；POST 可 body_from。
- **specs.content_hash()** 纳入 invocation——调用目标变化 = 契约漂移，
  复用既有"漂移 → 重审"机制；**CAS 与写操作强一致撤权自动覆盖 HTTP 工具**
  （同一 review/invoke 链）。
- **infra/http_tool_client.py**：httpx trust_env=False 直连（与 mcp_client
  同模式，防系统代理架空 SSRF）；path 逐段 URL 编码防注入；模板参数缺失
  显式报错；未引用参数整体入 POST body。
- **skill.py**：按 `conn.transport` 分流 MCP/HTTP 执行；强一致复核与
  SSRF 兜底对两类工具同等生效。
- **admin_mcp.py**：连接创建支持 transport；新增
  `POST /connections/{id}/tools/manual`（手工登记，pending 起步）与
  `PUT /tools/{id}/invocation`（编辑产生新快照版本，approved 即漂移）；
  transport 不匹配 / slug 冲突 / 非法模板全部 fail-fast。

### 前端（frontend-admin）

- 连接表单：新增连接类型选择（MCP 自动发现 / HTTP API 手工登记），编辑时
  类型锁定；http_api 连接的操作按钮从"发现"切换为"登记工具"。
- `HttpToolForm`：登记/编辑表单（method/path/query/headers/body_from/
  JSON Schema），编辑已批准工具时明确提示"保存将漂移需重审"。
- `/tools` 统一目录：外部动态工具段增加 MCP/HTTP 徽章区分（HTTP 徽章
  tooltip 展示 method+path），段标题同步。

### 验证（本机真实 PG:5433 + FastAPI/uvicorn mock server）

- `pytest tests/test_mcp_dynamic_e2e.py tests/api/test_admin_mcp_api.py
  tests/mcp_registry/` → **76 passed**（含新增 HTTP 工具全链路 E2E：
  连接 → 登记 → transport 隔离 → 非法模板拒绝 → slug 冲突 → 审核 →
  授权 → 真实 HTTP 出网调用 → pending 不可调用 → 编辑漂移退出运行时 →
  重审恢复 → invocation 快照版本可追溯）
- frontend-admin：`tsc --noEmit` 通过；vitest 547 passed / 6 failed
  ——rbac（:3000）、RoleGate（基点漂移）为既有故障；surface /
  design-tokens 单跑即过（全量并发环境抖动），与本次改动无关。

### 待办

- 提交推送 / PR 待用户确认；`devctl.bat rebuild` 容器验证在合并后执行。

## Batch 1 收尾（2026-10-27，PR #110 合并后）

- PR #110 已合并（`6deae1a`）；经用户选择方案 B，以主仓库执行
  `devctl.bat rebuild /y` 重建共享栈（原栈由另一会话 worktree 管理、
  基线 7c39450，重建前已确认其无未提交改动）。
- **容器级验证通过**：15 容器全部 healthy；/health 报
  `migrations.packaged_max = applied_max = 115_http_api_tools.sql`；
  容器内 `specs.py` 含 `_VALID_HTTP_METHODS`、`http_tool_client.py`
  存在；admin 前端 :3200 可达。

### 本批改动（PR #110 后续，未提交）

- **http_api 连接探活**：`/test` 按 transport 分流——http_api 用 HEAD
  探活（404/405 也算可达，验证网络/TLS 而非路由存在）；不可达显式
  `ok=False + connection_failed`。e2e 2 passed。
- **invocation 版本对比视图**：审核面板对 http_api 工具展示最近 3 版
  调用定义（版本号/hash/时间 + 当前版高亮）。
- **OpenAPI 可选导入**：登记表单粘贴 OpenAPI 3.0 JSON → 前端轻量解析
  （第一个 GET/POST 操作）预填 method/path/query/Schema；服务端
  validate_invocation 仍是唯一校验事实源，不引新依赖。

### 验证

- 后端：`test_mcp_dynamic_e2e + test_admin_mcp_api + mcp_registry` →
  **78 passed**
- 前端：`tsc --noEmit` 通过；mcp 页面无既有测试文件，surface 回归 18 passed

### 下一步（Batch 1 剩余 → Batch 2）

- 本批提交推送建 PR（待用户确认）。
- Batch 2：能力包（CapabilityBundle）/ Skill 依赖 / Agent 授权矩阵、
  独立 Tool Discovery Agent、多工具真实路径验证。

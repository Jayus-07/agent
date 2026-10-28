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

## Batch 2 主体：能力包与产品授权矩阵（2026-10-27）

PR #111 已合并（`561eae8`），`devctl.bat rebuild` 后运行栈健康并确认
PR #111 代码在容器内（`_probe_http_api`）。

### 设计决策（调研结论）

现有三层授权链已相当完整（`product_allows_capability` → 域内候选过滤 →
GovernanceRuntime 执行期复核），动态 mcp.* 已有 DB 授权表。真实缺口：
1. 静态能力的产品上限是硬编码域黑名单，管理端不可管理；
2. 无能力包聚合单元——不能把 Tool/Skill 组合后统一授权；
3. Discovery 策略不可配置（tool_selector 已有预算闸/角色策略，够用）。

最小方案：迁移 116 建能力包三表；`product_allows_capability` 静态分支
改为**授权表非空 → 白名单判定，空/未加载 → 回退既有黑名单**（部署零
破坏）；Discovery 不另建 Agent，复用既有 tool_selector 的 FC 链与预算闸
（原计划的"独立 Discovery Agent"按删除优先原则裁剪——现有链已具备
权限预过滤、预算上限、trace 归因，重复建即平行系统）。

### 后端

- **迁移 116**：`ai.capability_bundles` / `ai.capability_bundle_items`
  （静态+动态成员统一存名）/ `ai.product_bundle_grants`（已对本机库执行）。
- **infra/bundle_registry**：15s 轮询快照（与 mcp_registry 同模式），
  内容签名 + 失效回调；`bundle_allows` 三态（True/False/None=回退）。
- **product_context**：静态分支优先走包授权白名单（跨域成员显式授权
  即放行，白名单优先于黑名单）；None 回退黑名单；internal/public 不变。
- **admin_bundles API**：CRUD + 授权/撤销（run_governance_mutation 幂等
  + 审计）。**授权 bundle 自动为其中动态成员补建 ai.mcp_tool_grants，
  撤销同步禁用**——写操作强一致撤权对 bundle 撤销同样即时生效。
  动态成员登记时校验运行时生效（防悬空授权）。
- **server.py** 挂载 bundle_registry 刷新循环；router 注册新路由。

### 前端

- `src/api/bundles.ts`（mutationRequest 幂等封装）；
- `/skills` 页新增 `BundleGrantMatrix`：能力包 × 产品入口授权矩阵、
  创建（成员多行输入）、启停；授权/撤销即时刷新。

### 验证（本机真实 PG:5433）

- 单元：`tests/mcp_registry/test_bundle_registry.py` → 10 passed
  （成员并集/禁用包排除/签名内容/白名单与黑名单回退/租户隔离三态）；
- e2e：`tests/api/test_admin_bundles_api.py` → 3 passed（创建冲突/非法
  domain 拒绝 → 授权后白名单生效、非成员拒绝 → 成员替换即时生效 →
  撤销回退黑名单；动态成员 grants 同步补建/禁用 + 快照同步；悬空成员
  拒绝）；
- 回归：mcp_registry + admin_mcp_api + e2e + product_scope → **97 passed**
  （既有授权链无破坏）；
- 前端：tsc 通过；/skills 页既有测试 2 passed。

### 修复记录

- 迁移 116 首版漏 `product_bundle_grants.updated_by` 列（revoke SQL 引用
  报 UndefinedColumn），已补列并对本机库 ALTER 生效。
- Mimosa 两次误报 SQL 注入（模块级常量查询/测试工具函数），均为
  psycopg2 参数化写法，改为纯字面量形式后通过。

### 下一步

- 本批提交推送建 PR（待用户确认）→ CI → 合并授权。
- Batch 2 剩余：以"销售数据 → 库存查询 → 补货分析"真实多工具路径做
  e2e（计划验收场景）；budgets 页加工具次数/并发约束展示（Batch 3）。

## Batch 2 收尾：多工具真实路径 e2e（2026-10-27）

PR #112 已合并（`4a7a0b4`）；`devctl.bat rebuild` 后 /health 报
`applied_max = 116_capability_bundles.sql`、容器内 bundle_registry 符号
核实——迁移 116 在共享栈生效。

### 多工具 e2e（TestMultiToolReplenishmentPath，1 passed）

计划验收场景 E08 的完整路径（真实 PG + mock REST server，agent 产品
身份）：
1. http_api 连接 + 三工具登记（销售 GET / 库存 GET / 补货 PUT·R2 写）
2. 审核（R2 强制用户确认）→ 逐工具 grants 授权 → **能力包聚合三工具
   并授权**（bundle → 动态成员 grants 自动补建）
3. 任务执行：销售查询（42 件）→ 库存查询（3 件）→ 按差值计算补货量 →
   补货写入（PUT，写操作强一致复核**放行**）→ 绕平台链直连 mock 读回，
   确认数据真实落上游（非伪造成功）
4. 撤销补货工具 grants → 写调用**立即被强一致复核拦截**（E06）

### 顺带修复与验证中发现的治理行为

- **HTTP method 白名单扩展 PUT**（补货场景）：specs / http_tool_client /
  前端类型与表单选项三处同步；body_from 支持 POST/PUT。
- 强一致复核的**租户隔离**得到实证：测试上下文租户与工具 grants 租户
  不一致时写调用被拒（读操作走快照不受影响）——修正测试后放行。
- `based_on_sold` 参数不在 invocation 模板/body_from 引用中 → 不出网
  （参数走私防护实证）。
- e2e 基建教训：幂等旁路 patch 的作用域必须覆盖全部请求执行期
  （monkeypatch 而非 with 块）；`from __future__ import annotations`
  下 FastAPI 端点的 Request 注解必须模块级导入。
- 环境：Docker Desktop 中途重启导致一次 PG 间歇断连（测试 skip/500），
  恢复后复跑通过；幂等键冲突（IDEMPOTENCY_CONFLICT）为治理门禁正确
  行为，测试改用按操作唯一键。

### 验证

- `test_mcp_dynamic_e2e + test_admin_bundles_api + test_admin_mcp_api +
  mcp_registry` → **92 passed**
- 前端 tsc 通过（method 联合类型加 PUT）

### Batch 2 状态：完成。下一步 Batch 3（预算策略工具约束 / Prompt 版本
Discovery 入口 / 资产一致性扩展 / 发布记录联动）。

## Batch 3 主体（2026-10-27）

PR #113 已合并（`a655d2b`）。Batch 3 三项（发布记录联动经调研由既有
prompt/lock 版本机制覆盖，不另建）：

### 3-1 预算策略 × 工具执行约束

- 后端：`GET /api/admin/tools/constraints`——每 capability 一行超时 /
  单请求调用上限 / 并发隔离 / 重试 / 熔断 / 限流（ToolPolicy ∪
  ToolSpec，静态策略表 + 动态审核 governance 统一视图，只读）。
- 前端：`cost-governance/budgets/ToolConstraintsSection.tsx` 嵌入预算
  治理页（静态/动态来源徽章；只读，约束修改走 /mcp 审核流）。

### 3-2 Prompt 版本页工具治理入口

- `promptGroups.ts` 新增「工具治理（发现与选择）」分组，纳入已注册的
  `router.tool_selector`（FC 工具选择/发现 prompt）——复用既有版本
  流水线（草稿→测试→评估→发布），不建第二套。
- AI 语义建议/失败分析按计划属"AI 建议+人工审核"范畴，当前无独立
  prompt 消费点（失败分析由 trace 差异+人工完成），按删除优先不预建。

### 3-3 资产一致性检查扩展

- `consistency.py` 新增第 8 段 `dynamic_registry`：
  - 运行时快照派生失败清单（审核配置/调用定义非法）；
  - bundle 引用未生效动态 capability（悬空授权）；
  - 连接声明 Bearer 凭据但密文行缺失；
  - invocation 合法性复用 validate_invocation 统一校验器。
- DB 段 async 直连（报告端点 async 上下文），对账失败软失败不炸整份
  报告。前端 consistency 页消费同一 report 结构，自动展示新段。

### 验证

- `test_consistency_dynamic` 4 passed（干净快照/派生失败/悬空引用/
  DB 异常软失败）；`test_bare_prompt_collection` 21 passed（prompt
  逐字守卫未破坏）；前端 tsc 通过、prompts config 测试 2 passed。

### Batch 3 状态：主体完成。剩余（Batch 4 范畴）：配置导入导出与
环境映射、健康检查与退避重连巡检、端到端 SSE 断线恢复联测、清理与
交付整合。

## Batch 4 收官（2026-10-27）

PR #114 已合并（`953c201`），rebuild 后运行栈验证 Batch 3 代码生效
（consistency 动态段 6 处符号、admin_tools constraints 3 处）。

### 4-1 配置导出（不含凭据）

- `GET /api/admin/mcp/export`：连接声明（name/base_url/transport/
  auth_type/network_scope）+ 能力包（成员 + 授权产品）+ 版本/时间/
  租户；**凭据密文、fingerprint、last4、审核状态、运行时状态绝不
  导出**——目标环境重新配置凭据并重新审核（ADR-0011 D7 红线）。
- e2e：导出含声明字段、全文无 `sk-export-`/`secret_cipher`/
  `fingerprint`/`last4`、无 enabled/status 运行时字段。
- 环境映射：导出物即"声明清单"，导入方按目标环境改 base_url 后重建
  连接 → 重新发现/登记 → 重新审核授权（不自动导入，避免绕过审核）。

### 4-2 清理与同构问题修复

- `get_capabilities_schema_text()`（Critique LLM 修正的 prompt 数据源）
  与已修复的 normalize_plan 同病——仍基于静态 CAPABILITY_MAP，会把
  动态 mcp.* 步骤判无效"修正"掉。改为 effective_manifest 同源。
- 复查确认：无其他静态白名单残留消费点（get_available_capabilities
  仅剩 critique schema_text 一处，已改）；无平行系统残留。

### 交付验证

- 全量相关回归：`test_mcp_dynamic_e2e + test_admin_bundles_api +
  test_admin_mcp_api + test_consistency_dynamic + mcp_registry +
  planner_critique` → **107 passed**（9m36s，真实 PG + mock server）
- 运行栈：rebuild 后 /health ok、迁移 116 生效、Batch 3 符号在容器内。

## 任务终态

| Batch | 内容 | PR |
|---|---|---|
| 0 | PR #107 收敛：5 项 Review 修复 + 写操作强一致撤权 + CI 门禁登记 | #107（已合并） |
| 1 | 连接中心（MCP+HTTP API）：invocation 受控调用、审核 CAS、探活、版本对比、OpenAPI 导入 | #110 #111（已合并） |
| 2 | 能力包与产品授权矩阵、多工具真实路径 e2e | #112 #113（已合并） |
| 3 | 预算×工具约束视图、Prompt 工具治理入口、一致性动态对账 | #114（已合并） |
| 4 | 配置导出（凭据红线）+ Critique 同构修复 + 交付验证 | 本批 |

计划"明确不做"清单均未实施（第二 Runtime / 管理端任务控制台 /
在线编辑器 / 自动授权等）。裁剪记录：独立 Discovery Agent 复用
tool_selector FC 链；发布记录联动复用 prompt/lock 版本机制。

## 交接包对照收口（2026-10-28，未提交待确认）

用户提供 32 项已确认决策 + 融合版 V2 原型交接包，本轮按指令做差异审查
与 P0/P1 安全修复；不重做 Batch 0–4、不合并 PR、不扩范围。

### GitHub 事实

- PR #115 OPEN / MERGEABLE，HEAD `e3148d9` = 分支 HEAD；Codex Review
  两条未解决发现（P1 导出 URL 内嵌凭据、P2 Critique 无授权预过滤），
  `has_unresolved_findings=true`。
- main `953c201`（PR #114 合并点）；已合并 #107 #110 #111 #112 #113 #114。

### P0 修复一：导出凭据红线（admin_mcp.py）

- 连接创建/更新入口拒绝 URL 内嵌凭据（userinfo `user:pass@`、凭据型
  query `?api_key=`/`token=` 等 22 类敏感参数名、`#fragment`）→ 400。
- 导出端点对每行 base_url 统一净化（剔除 userinfo/凭据 query/fragment，
  保留 host 与非敏感参数）；净化失败或无 host 的历史行拒绝导出，响应
  新增 `redacted_connections` / `rejected_connections` 透明可审计。
- e2e（真 PG）：创建/更新拒绝 4 形态 + 合法 query 放行 + 更新不受影响；
  历史行（直插 `legacy:p%40ss@...?api_key=...#frag`）导出净化断言全文
  无密钥/userinfo/参数名、region=cn 保留、redacted 列表精确匹配；
  `http://` 行进 rejected。

### P0 修复二：Critique/Planner/normalize 共用授权集（Codex P2）

- `product_context` 新增 `product_id_from_state`（与执行门同取值链）与
  `filter_capabilities_for_state`（同谓词 `product_allows_capability`
  (product, cap, (state.domain,))——渲染面不得宽于执行面；`map.lookup`
  因 domain=travel 但刻意保留主图，按执行门同谓词判断而非声明域）。
- Planner：可见集按产品/域过滤；**缓存键加 product_id+domain**（否则跨
  产品缓存命中=越权计划泄露）；`_fallback_plan`/`_ensure_knowledge_step`
  补 rag 授权守卫。
- Critique：授权可见集贯穿规则引擎、schema 文本（`get_capabilities_schema_text(allowed=)`
  新参）、自动修复结果与 LLM 修正（`normalize_plan(allowed=)` 新参）——
  LLM 修正无法恢复越权步骤。
- engine `_is_execution_override` 快路复核补 product 门（verified_context
  可信 product_id）。
- 测试：agent 不渲染 travel/cs；`normalize_plan(allowed)` 剔除越权步骤；
  critique LLM 修正无法恢复 travel 步骤且 prompt 无 travel schema；
  缓存键跨产品不同；跨租户 mcp.*（快照注入 + ContextVar 租户）B 租户
  不可见。

### P1 修复：决策 #15 业务域硬隔离（管理面缺口）

审计结论：运行链（路由候选/Planner/Critique/direct_executor/skills/base/
GovernanceRuntime/写复核）产品过滤完整、domain_hint 未参与授权、M1 锁域
handoff 有测试、bundle 表空回退正常；但**管理面无域边界**——bundle 授权
与 MCP 工具授权端点均可把 travel/cs 专属能力授权给 agent，且 Batch 2
「白名单优先于黑名单」语义 + 测试固化了跨域放行，与决策 #15 直接冲突。

- `product_context`：新增 `_DOMAIN_EXCLUSIVE_ROOTS`（agent 集含 cs 域
  历史 root `cs.*`——原黑名单漏该根）与 `domain_exclusive_forbidden_roots`；
  静态分支改为「白名单 ∩ 非域专属」——授权不构成跨域提权；黑名单回退
  语义不变（单一事实源，agent/cs 集合与原值一致 + 补 `cs` 根）。
- `admin_bundles.grant_bundle`：授权方向校验 bundle.domain（travel/cs/
  selection 标签）与静态成员根名，跨域误授权 400 并列明成员（撤销不受限）。
- 已知剩余缺口（下一批）：`ai.mcp_tools` 无业务域字段，动态工具直授
  （`POST /tools/{id}/grants`）无法按域校验——最小路径为治理 JSONB 增
  `domain` 字段（审核 UI 采集）+ grant_tool 校验 + 快照透传；bundle 路径
  已由 bundle.domain 堵住。
- 测试反转：`test_cross_domain_member_allowed_when_granted` →
  `test_cross_domain_member_denied_even_when_granted`（含回退与边界内
  放行对照）；新增 API 三例（travel 包→agent/cs 拒、cs 包→agent 拒/
  cs 过、general 标签夹带 travel 成员拒）。

### 验证（本机，D:/Python/python.exe，真 PG:5433 + mock MCP server）

- `pytest backend/tests/agents/test_planner_critique.py
  backend/tests/orchestration/router/test_product_scope.py` → 22 passed
- `pytest backend/tests/mcp_registry/ backend/tests/orchestration/router/` →
  70 + 145 中 144 passed（1 failed =
  `test_router_has_no_second_registered_domain_mapping_literal`，main
  `953c201` 基线已有：`execution_mode.py:186` 域字面量重复，非本轮引入）
- `pytest backend/tests/api/test_admin_bundles_api.py
  backend/tests/agents/test_planner_critique.py` → 21 passed
- `pytest backend/tests/test_mcp_dynamic_e2e.py` → 14 passed（含导出
  3 例、多工具路径 1 例复跑）
- `pytest backend/tests/api/test_admin_mcp_api.py
  backend/tests/api/test_admin_bundles_api.py` → 18 passed
- 容器级部署验证 UNVERIFIED（改动未提交/未合并，按 AGENTS.md 合并后
  rebuild）。

### 32 决策差异台账与下一批清单

详见本轮交付报告（会话输出）。要点：#14 仅导出（导入/环境映射/差异
预览未建）、#16 仅手动探活（主动健康监测/退避重连/漂移巡检未建）、
#18 复用 tool_selector（独立多轮 Discovery 未建）、#19 AI 语义契约、
#20 AI 失败分析、#25 结果引用+Schema 映射、#27 自动任务/Webhook、
#29 运行中多轮修改、治理配置独立发布/回滚（发布记录页仅系统 Gate）——
均按第 32 题列增强批次，未擅自取消；动态工具域字段为硬隔离增强前置。

## Batch 5-1：硬隔离补全——mcp 工具业务域字段（2026-10-28）

用户授权下一批顺序（硬隔离补全 → 治理配置发布/回滚+导入 → 健康监测/
Discovery 增强）并要求持续推进；main 基线守卫修复已另开 PR #116。

### 分支与事实

- 本批分支 `feat/tool-governance-batch5`（基于 a8b138d，即 #115 head +
  交接包收口修复）；#115 保持 Merge Ready 不动。
- main 基线修复 PR **#116**（`fix/router-domain-literal`，99ce938，
  execution_mode 域字面量复用 _DOMAIN_ALIASES，守卫测试 16 passed，
  与 #115 无文件交集）。

### 改动（64fa7a8，6 文件 +130/−6）

- `core/product_context`：新增 `DOMAIN_LABELS` 业务域词汇表（工具治理
  governance.domain 与能力包 domain 共用单一事实源；空=未分类兼容存量）。
- `infra/mcp_registry/specs`：derive_tool_spec 校验可选 `domain` 标签。
- `admin_mcp.grant_tool`：授权方向按 governance.domain 校验禁行域，
  跨域专属工具授权 agent/cs 直接 400。
- `admin_bundles.grant_bundle`：动态成员逐工具按 governance.domain 校验
  （堵住「bundle 标签无关但成员工具是 travel 域」的洞）；`_VALID_DOMAINS`
  改为引用 DOMAIN_LABELS。
- 测试卫生修复：test_admin_bundles_api 清理 fixture 的 created_by 前缀
  失配（实库为 `user:<id>`），残留行曾致重名 409。

### 验证

- `pytest backend/tests/mcp_registry/ backend/tests/api/test_admin_bundles_api.py`
  → **69 passed**；新增 E2E `TestToolDomainGrantBoundary` 2 passed
  （travel 域授权 agent/cs 拒、general 放行、cs 域 cs 过 agent 拒、
  非法 domain 审核拒绝）。
- 至此决策 #15 的服务端拒绝点闭环：bundle 域标签 + 静态成员根 + 动态
  工具 governance.domain + 运行门白名单∩非域专属，四层校验。

### 下一批剩余

- 治理配置发布/回滚 + 配置导入（#08/#14 收口；/releases 第一档前置）。
- 健康监测/Discovery 增强（#16/#18）。

## Batch 5-2：治理配置发布/回滚/导入 后端（2026-10-28）

### 交付（d1c95d2，7 文件 +906/−65）

- **迁移 117** `ai.governance_releases`：租户内 version 递增，status
  draft/published/rolled_back；payload 与 /admin/mcp/export **同构**
  （凭据红线同源，复用 _export_payload——导出逻辑抽取为共享函数，
  export 端点行为不变，e2e 复跑 3 passed）。
- **新路由 admin_governance_releases.py**（`/admin/mcp` 前缀；与既有
  admin_releases.py=系统发布 Gate 严格区分，V2 两档对应两个文件）：
  - `POST /releases` 快照草稿（空配置 409）；
  - `GET /releases/{id}/diff` 结构化差异（modified/added/removed，
    方向=回滚将发生什么；added 仅提示不删除）；
  - `POST /releases/{id}/publish`（draft→published，重复发布 409）；
  - `POST /releases/{id}/rollback`：连接声明字段恢复（URL 二次净化
    双保险，不恢复凭据）+ bundle 描述/域/成员整体替换 + 授权精确对齐
    快照（多余禁用、快照授权启用/补建）+ **动态成员 mcp_tool_grants
    同步**（运行时契约源）+ **生成新版本**记录（原型语义）；
  - `POST /import`：纯增量导入——连接 disabled+无凭据+凭据 URL 净化
    标注（redacted 透明返回）、bundle 重建不建授权、名称冲突跳过。
  - 写操作全部 run_governance_mutation（幂等+审计）+ 写后刷新双快照。
- 迁移登记：rag_smoke_path_filter（NON_AI）+ init_db（memory），
  守卫测试 12 passed。

### 验证

- `pytest backend/tests/api/test_admin_governance_releases.py` → 3 passed
  （快照-发布-回滚全生命周期含授权对齐断言；导入增量/无凭据/无授权/
  冲突跳过；租户隔离）；回归 admin_mcp+bundles+registry **84 passed**、
  导出 E2E 3 passed。
- 容器级部署 UNVERIFIED（迁移 117 已对本机 5433 库执行并核实表结构）。

### 下一批剩余

- **前端 /releases 第一档转真实**（本批后端已就绪：列表/快照/差异/发布/
  回滚/导入六个交互 + api 封装）——下一个增量。
- 健康监测/Discovery 增强（#16/#18）。

## Batch 5-2 前端：/releases 治理配置发布档（2026-10-28，0524301）

- 页级两档（WorkbenchShell，?tab= 进 URL）：**治理配置发布**（默认）+
  **系统发布与质量 Gate**（原 M8 内容原样保留为第二档）。
- 治理档：快照草稿创建（说明输入 + 计数回显）、版本历史表（状态 chip/
  说明/连接包计数/创建与发布时间/操作）、差异预览抽屉（counts 指标条 +
  字段级 release→current 变更条目）、发布/回滚（确认弹窗，结果回显）、
  **配置导入抽屉**（JSON 粘贴 → created/skipped/rejected/redacted 透明
  结果面板）。
- 发布前检查=差异预览面（五项自动预检属后续增强，不做假检查——记录进
  增强清单，不入壳位）。
- API：新增 `api/governanceReleases.ts`（6 封装，mutationRequest 幂等键）。
- 验证：tsc 0 错误；vitest 全量 **83 文件 / 572 用例全绿**（新增 3 例；
  首次全量单文件失败为仓库已记录的并发抖动，复跑全绿）。

### Batch 5 完成度与剩余

- ✅ 硬隔离补全（域字段）✅ 发布/回滚/导入（后端+前端）✅ 前端批次 0/1
- ⬜ 健康监测/Discovery 增强（#16/#18，运行健康壳位解禁前置）
- ⬜ 演示型壳位现状：/mcp 运行健康、/mcp 连接级版本时间线（治理配置
  版本已由 /releases 承载，连接级时间线仍待后端）、/tools AI 语义

## Batch 5-3：连接健康巡检（2026-10-28，PR #118 待合并）

分支 `feat/toolgov-health`（基于 ac789d6）；#115/#116/#117 已合并进
main 并重建验证（迁移 117 生效、admin 容器归主栈）。

### 后端（fdc38e9）

- 迁移 118：`ai.mcp_health_events`（source=monitor|manual，7 天保留）
  + 连接 `last_checked_at`/`probe_fail_count`；
- `infra/mcp_registry/probe.py`：探活原语（http_api=HEAD 可达性、
  streamable_http=握手+tools/list 只读），`/test` 委托同源；
- `infra/mcp_registry/health.py`：巡检循环（scan 60s / 基础探活 300s ×
  2^min(失败,4) 上限 ×16；成功恢复 connected 不动 enabled；事件裁剪；
  `MCP_HEALTH_MONITOR=0` 关闭）；server startup 挂载（spawn_background_task）；
- `GET /admin/mcp/health` 总览（租户隔离 + 退避/下次探活字段 + 每连接
  最近 5 条事件）；`/test` 统一 `_record_probe_result`（状态+计数+事件）；
- 迁移登记 CI 清单 + init_db（守卫 12 passed）。

### 前端（c2f4d5e）

- 「运行健康」壳位 → 真实数据区：巡检状态/退避说明/事件时间线
  （monitor·manual 标注）/「立即探活」按钮；连接卡片补最近探活与
  连续失败角标；故障模拟四按钮保持 `data-stub` 置灰（演练能力）。

### 验证

- `test_mcp_health_monitor.py` 4 passed：退避曲线断言、手工探活成功→
  断线（计数 1）→恢复（清零）全链 + 事件时间线、巡检器 probe_once
  失败/恢复（enabled 不动）、租户隔离；回归 admin_mcp 15 + e2e 探针/
  导出 5 passed；
- 前端 tsc 0 错误、vitest 83 文件 572 用例全绿（mcp 页壳位断言更新为
  真实健康区断言）；
- 容器级 UNVERIFIED（合并授权后 rebuild；迁移 118 已对本机库执行）。

### 演示型壳位现状（更新）

- ✅ 解禁：/mcp「运行健康」（本轮转真实）
- ⬜ /mcp 连接抽屉「版本历史」（连接级配置时间线待后端）
- ⬜ /tools「AI 语义」Tab（#19）
- ⬜ 故障模拟四按钮（演练/评测能力，#16 深化）

### 下一批剩余

- Discovery 增强（#18）：tool_selector 多轮化差距评估 → 决定形态。

## Batch 7：AI 失败分析——聚类归因与候选评测用例（2026-10-28，决策 #20）

分支 `feat/toolgov-failure-analysis`（基于 84f3823 = origin/main；与
OPEN 的 #121 语义分支并行开发，合并时 registry.py / promptGroups.ts /
test_registry_defaults.py / page.test.tsx 预期有小冲突面，见下）。

### 后端

- 迁移 121：`ai.tool_failure_analyses`（id/tenant_id/window_minutes/
  input_summary/result/model/created_by/created_at；登记 init_db + CI
  清单；已对本机库 5433 执行）。input_summary 只存聚类计数与摘要，
  result 存 LLM 完整 JSON；7 天后参考价值有限，不自动清理（迁移注释
  说明手动裁剪口径）；
- prompt 注册：`tool.failure_analysis`（defaults/tool_failure_analysis.yaml
  + registry PromptSpec；promptGroups.ts「工具治理」白名单同步；本分支
  注册表 61→62 键，#121 合并后 63）；
- `admin_tools.py` 两个端点（require_admin_user + run_governance_mutation
  + security_events 审计）：
  - `POST /admin/tools/failure-analysis`：body {window_minutes 5-1440 默认
    60, limit 10-100 默认 50}，非法 400；聚类复用 /errors 同源数据路径
    （进程内 trace tool_call span 直读），按 (tool, error_class) 聚类计数
    取 top 10 组、每组 2-3 条代表性错误摘要（截断 300 字符 + URL 清洗）；
    **只读 input.tool/capability，绝不读 input.params**；prompt key
    `tool.failure_analysis`，要求严格 JSON clusters（attribution /
    suggestions≤3 / candidate_eval_cases 合计≤6）；解析失败重试 1 次 →
    502、LLM 不可用 → 503，失败不落库；空聚类跳过 LLM 直接落一条空分析；
    结果落 ai.tool_failure_analyses 并返回 {analysis_id, clusters,
    created_at}；
  - `GET /admin/tools/failure-analysis?limit=10`：本租户最近分析列表
    （editor 档，id/时间/窗口/聚类计数/model/完整 result）；
- 防幻觉：LLM 返回的 (tool, error_class) 不在输入聚类中的一律丢弃。

### 前端

- `api/failureAnalysis.ts`：runFailureAnalysis（mutationRequest 幂等键）+
  getFailureAnalyses 两封装与全套类型；
- /tools「运行质量与失败」subTab 新增「AI 失败分析」区块（AssetSection）：
  时间窗选择（60/360/1440 分钟）+「生成分析」；结果面板按聚类卡渲染
  （tool/错误分类/归因/修复建议/候选评测用例表）；最近分析列表点击回看；
  生成中/错误/空态齐全；候选用例区常驻标注「仅建议——应用需走评测中心
  审核与回归」，区块说明明确「不含用户参数原文与凭据」。

### 边界（决策 #20 红线）

- 只生成建议与候选评测集（evalCandidate），不自动应用/改写任何正式
  Prompt / 语义契约 / Golden 数据；
- 送 LLM 的输入只有 (tool, error_class) 聚类计数与截断 300 字符错误摘要
  （URL 已清洗）：span `input.params` 一概不读 + `_redact_urls` 清洗 +
  落库的 input_summary 同口径——测试断言手机号/api_key/URL 主机名均不
  出现在 prompt 与 input_summary 中。

### 验证

- `test_admin_failure_analysis.py`（pg）7 passed：聚类计数 + prompt 数据
  边界（敏感 params/凭据/URL 不进 prompt）+ 落库 + GET 回显；时间窗过滤
  （窗外失败不计入）+ 防幻觉丢弃；空聚类跳过 LLM（fake.calls==0）；
  垃圾 ×2 → 502 且不落库；LLM 抛异常 → 503；非法 window/limit → 400；
  写接口 admin 门禁 / 列表 editor 可读；
- 回归：failure_analysis + discovery + failure_probe + prompts registry +
  bundles **40 passed**；`-k admin` 全口径 188 passed / 5 failed——5 个
  失败（rbac_api 4 + admin_mcp overview 1）经 origin/main 干净基线对照
  实验证实为既有隔离抖动（基线同口径同 5 个失败），与本改动无关；
- 前端 tsc 0 错误；tools 页 12 用例全绿（新增 2 例：AI 区块生成调用 +
  聚类卡/边界标注；空聚类 + 最近分析回看）；vitest 全量 83 文件 /
  576 用例全绿（基线 83 文件 / 574 用例 + 新增 2 例）；
- 容器级 UNVERIFIED（本批为管理端增量端点 + 静态前端；迁移 121 已对本机
  库执行；合并授权后 rebuild 生效）。

### 合并注意（与 OPEN 的 #121 并行）

- registry.py / promptGroups.ts / test_registry_defaults.py（61↔62 键）/
  page.test.tsx 预期小冲突：#121 合并后注册表为 63 键、tools 页测试 mock
  需并集；两批改动文件集不重叠（#121 全在 admin_mcp.py / mcp.ts / 抽屉）。

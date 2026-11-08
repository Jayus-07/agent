# TASK: 安全审计修复实施（security-audit-remediation-20261108）

> 本文档是给实施会话的完整交接。按马斯克五步骤执行：先质疑每项修复是否必要（本文档已给出理由与证据，有疑义先问用户），再删除/简化，不扩大范围。

## 一、前因后果（为什么有这份计划）

1. **审计来源**：2026-11-07~08 用 Cloudflare security-audit Skill 对本仓库做了两轮全仓静态安全审计（run-1 于旧 HEAD `7b0bfc5d`，INCOMPLETE；run-2 于 HEAD `8b2ba9aafa3d5667255892daa5923ada92cc7d98`，已完成六阶段）。审计是**纯只读**的：不运行代码、不改源码、不读 .env。
2. **run-2 终态**：`incomplete`（1 个覆盖单元 rank70 因预算 deferred）。产出 **1 条 confirmed（low）+ 77 条 needs_validation + 3 条 rejected**。全部记录、trace、证据、验证方案都在 findings.json；人类可读版在 NEEDS-VALIDATION.md；汇总在 REPORT.md。
3. **证据目录（全部只读参考，不要修改）**：`C:\Users\wh\security-audit-skill\agent\run-2\`
   - `REPORT.md` — 汇总报告
   - `findings.json` — 81 条结构化记录（fingerprint/trace/evidence/blockers/validation_plan）
   - `NEEDS-VALIDATION.md` — 77 条待验证线索的完整明细
   - `owner-observation-checklist.md` / `owner-observation-decisions.json` — 部署观察结论
   - `coverage-ledger.json` / `run-metadata.json` — 覆盖与运行账目
4. **部署观察已确认的事实（2026-11-08，本机共享栈 agent compose 项目）**：
   - `SENSITIVE_API_GUARD_MODE` 未设置且无 DB 覆盖 → 默认 `enforce` 生效（audit 放行家族未触发，已 rejected；**但切 audit 的能力还在，见 T3**）。
   - 会话闸两层 enforce（`GATEWAY_SESSION_CHECK` env + `JWT_SESSION_GUARD_MODE` DB）。
   - `/metrics` 无凭据 GET 实测 HTTP 200（51931 字节）→ **唯一 confirmed，low**。
   - auth-redis 实际有密码（`AUTH_REDIS_PASSWORD`，64 字符）——注意不是 `REDIS_PASSWORD` 这个变量名。
   - 仓库与 APISIX 配置**均无任何 body size 上限**。
   - 本机库**已存在多租户数据**（default 87 用户 + tenant-p6/tenant-b/stopcs-a/stopcs-b 等）→ 跨租户类问题不是理论风险。
   - GitHub environment `prompt-eval` **无任何保护规则**（gh api 实证）。
   - ToolServer（:8081/:8082）未被编排；business-mock `INTERNAL_API_TOKEN` 未设置（fail-open 无鉴权运行，仅 127.0.0.1 绑定）。
5. **本任务的目标**：按成本与风险消除效果排序，把上述问题中**源码/配置侧可修**的部分修掉。审计会话不修代码是纪律；修复由本任务（实施会话）按项目 Git/PR 规则执行。

## 二、必读文件（开工前按序读完）

1. 本文档全文。
2. `C:\Users\wh\security-audit-skill\agent\run-2\REPORT.md`（~20KB）
3. 所做任务涉及的 `NEEDS-VALIDATION.md` 对应条目（按 fingerprint 搜索）。
4. 项目 `AGENTS.md`（worktree/PR/测试纪律）。

## 三、修复顺序总览与理由

| 序 | 任务 | 成本 | 了结/消除 | 先后理由 |
|---|---|---|---|---|
| T1 | `/metrics` 纳入认证 | 一行级 | 唯一 confirmed | 已实证、成本最低、立即归零 confirmed |
| T2 | GitHub environment 保护 | 配置动作（非代码 PR） | CI 供应链家族 | 无代码风险，立即堵住 secrets 暴露面 |
| T3 | audit 模式加能力白名单/时间窗 | ~0.5-1 天 | 结构性防复活 | 当前 enforce 所以 rejected 才成立；不修它，rejected 随时可翻回 |
| T4 | 跨租户谓词组（4 个子任务） | 每个 0.5-1 天 | 10+ 条线索 | 安全价值最高；本机库已有多租户数据；各子任务文件域独立可并行 |
| T5 | 改密重签从 DB 重读角色 | ~0.5 天 | 权限撤销闭环 | 独立；撤销链缺口会同时削弱 T3/T4 修复的效果 |
| T6 | 请求体上限（边缘 + 源内） | ~1 天 | 7 条缓冲线索 | 需要先定边缘层方案（是否有前置代理），放后 |
| T7 | 运维面杂项（business-mock fail-closed、webhook 身份链、trace 留存） | 按项 | 其余线索 | 依赖部署决策，最后做 |

**依赖关系**：T1、T2 无依赖可立即并行；T3/T4/T5 相互独立；T4c（MCP 通道）依赖对 `service_identity` 租户语义的产品确认（见任务卡）；T6 依赖用户回答"生产入口是否有 APISIX 之外的前置代理"。

**明确不做**（本任务范围外）：审计报告/ledger/findings.json 的任何修改；rank70 补猎（另开审计会话）；生产 ECS 的观察（清单在 owner-observation-checklist.md，等用户执行）；所有 blocker 属"owner 观察类"的线索——已由观察轮了结或等待用户，不是代码修复对象。

## 四、任务卡

### T1 `/metrics` 纳入认证（confirmed-low，先做）

- **指纹**：`server.metrics-outside-auth-middleware`（confirmed）。
- **根因**：`backend/app/server.py:116` 把 `/metrics` 注册在根 app 上，且 `backend/app/api/middleware/auth.py:35` 的 `_SKIP_AUTH_PREFIXES` 含 `/metrics`，绕过 API-key 中间件；APISIX 只处理 `/api/*`，无兜底。
- **实测证据**：无凭据 `GET http://127.0.0.1:8000/metrics` → 200（51931 字节）；对照 `/api/agents` 无凭据 → 401。
- **改动（最小）**：从 `_SKIP_AUTH_PREFIXES` 移除 `/metrics`，让 API-key 中间件接管；或把 metrics 路由注册移入受认证的 router。**不要**顺手重构 metrics 渲染逻辑。
- **注意**：确认 Prometheus/监控侧是否有合法无凭据抓取需求（本机 compose 的 observability 属 profile 储备未激活；若有抓取器，给它发 API key 或改用内部网络 + 保持中间件覆盖——先问用户）。
- **验收**：无凭据 GET /metrics → 401/403；带合法 X-API-Key → 200。新增回归测试覆盖两个分支。
- **规模**：≤10 行源码 + 测试。

### T2 GitHub environment 保护（配置动作，不产生代码 PR）

- **指纹**：`github-actions.workflow-dispatch-unreviewed-ref-secrets`、`ci.pip-upgrade-unpinned-secret-eval` 的部署侧部分。
- **事实**：`rag_regression.yml:49` 挂 environment `prompt-eval`，该 environment 保护规则为空（gh api 实证）；workflow_dispatch 可选任意 ref 且文件内无 main 条件；付费 secrets 注入 job 环境。
- **改动**（GitHub Settings / gh api，需仓库管理员）：
  1. 给 `prompt-eval` environment 加 required reviewers（≥1）；
  2. 评测类 workflow_dispatch 限制 `ref` 为 main（workflow 文件加条件或在 environment 部署分支策略中约束）；
  3. （可选）rag_smoke 的 live job 已限 main+flag，保持不动。
- **验收**：`gh api repos/Jayus-07/agent/environments` 显示 protection_rules 非空；dispatch 非 main ref 被拒。
- **注意**：若顺手改 workflow 文件（如加 ref 条件），那部分走代码 PR（T2b，单独提交）。

### T3 audit 模式能力白名单/时间窗（防复活的结构修复）

- **指纹**：`sensitive-api-guard.audit-non-admin-bypass`（rejected@deploy，但机制在）。
- **根因**：`backend/app/api/deps.py:310-321` 的守卫在 `get_mode('SENSITIVE_API_GUARD_MODE') == 'audit'` 分支把身份类别/角色判定整体降级为 warning 放行；模式可经 sys_config DB 覆盖（`backend/services/sys_config.py:175-186`，PUT /api/sys/config，15s 生效、免重启），无能力白名单、无时间窗、无自动回落。
- **两个方案（实施前问用户选哪个）**：
  - **A（推荐，最小）**：audit 分支改为"只对**只读**端点放行 + 写操作/管理端点仍 enforce"，并在 deps.py 内维护一个写操作判定（现有 `request.method` + 路由谓词即可）。
  - **B（更紧）**：audit 模式加时间窗（如 sys_config 写入 `audit_until` 时间戳，到期自动回落 enforce）。
- **验收**：audit 模式下只读端点放行、写/管理端点拒绝（admin_tasks.py:199 的 retry/revoke/reexecute 必须仍拒绝非管理员）；新增测试覆盖两分支；enforce 路径行为不变。
- **不要做**：不改 sys_config 的 DB 覆盖机制本身；不动 enforce 分支逻辑。

### T4 跨租户谓词组（安全价值最高；4 个独立子任务，可并行）

> 共同背景：本机库已有多租户数据（default 87 + 5 个其他租户）。修复模式统一为"在查询/写入点补 tenant_id 谓词，缺租户上下文时 fail-closed"，与项目 `authorization-standard.md` 对齐。每个子任务独立 PR。

**T4a 客服会话租户谓词**（`cs-claim-close.missing-tenant-predicate`、`cs.admin.close_cross_tenant_waiting_handoff`、`cs_ops.dispatch_stats.supervisor_cross_tenant_aggregate`）
- 改动点：`backend/app/api/routes/cs_admin.py:1822`（claim）、`:2464`（_async_claim 的 CSHandoff 查询/条件 UPDATE）、`:2027`（messages 读取）补租户谓词；`backend/app/api/routes/cs_ops.py:39` stats 查询按 identity.tenant_id 过滤（`backend/customer_service/handoff/dispatch/repository.py:826`）。
- 验收：他租坐席 claim/close/read 他租会话 → 404/403；租户域 supervisor stats 只见本租户；测试用双租户夹具。

**T4b RBAC 会话管理租户谓词**（`rbac.security-sessions.missing-tenant-scope`）
- 改动点：`backend/app/api/routes/auth_local.py:1087/1115/1156-1161` 三个端点的会话查询与撤销加 tenant_id 谓词（平台 super_admin 保留全库视图需显式角色判定）。
- 验收：租户 admin 枚举/强下线他租会话 → 拒绝；super_admin 行为不回归。

**T4c MCP 通道 RAG/SQL 工具租户谓词**（`mcp-rag.tools.missing-tenant-pushdown`、`mcp-sql-list-tables-missing-sql-read`）
- **前置确认（问用户）**：MCP 调用方的租户语义——`backend/security/service_identity.py:39,94` 给机器主体恒定 default 租户；`/api/mcp/call` 对无 X-Tenant-Id claim 的用户绑空租户（identity.py:44,88）。先定契约：MCP 机器主体按 default 租户过滤，还是透传调用方租户。
- 改动点：`mcp_servers/servers/rag.py:177/202` 的 registry.search/list_active 加租户谓词（与 HTTP 通道 `can_read_row` 同源控制）；`mcp_servers/servers/sql.py` list_tables 补 `sql.read` 预检与可见表过滤。
- 验收：同一资源在 MCP 与 HTTP 两条路径的可见性一致；测试覆盖双通道对照。

**T4d 选品漏斗租户/属主绑定**（`selection-funnel-missing-tenant-isolation`）
- 改动点：`backend/selection_funnel/import_pool_pg.py:155`（list_candidates/clear_batch/history_by_keys）与 `import_pool.py:257` 链路补 tenant/owner 维度；docs 已有 P1-8 契约相悖记录，实施前核对 `docs/domains/selection-ecommerce.md` 现行契约。
- 验收：导入/列表/删除按租户隔离；跨租户 clear_batch → 404。

### T5 改密重签从 DB 重读角色（`local-auth.change-password.reissues-stale-roles`）

- **根因**：`backend/app/api/routes/auth_local.py:664-741` 的 change_password 用**旧 token 的 roles/username/dept claim** 重签新令牌（:737-740）；被降权/撤销会话的用户可自铸旧角色新令牌（新会话键可过 enforce 会话闸——独立复核确认此链不依赖 audit 模式）。
- **改动（最小）**：重签前从 `auth.users`/角色表按 user_id 现读角色与部门，禁止透传旧 claim；沿用 rbac.py:555-569 的撤销链口径。
- **验收**：降权后改密 → 新 token 携带新角色；撤销会话后改密被拒；回归测试覆盖"降权→改密→旧权限不可用"。

### T6 请求体上限（边缘 + 源内，先答一个前置问题）

- **指纹**：`bff-arraybuffer-body-unbounded`、`automations.webhook.body-buffer-before-auth-no-size-limit`、`selection-funnel-unbounded-import-body`、`rag-xml-dom-prelimit-allocation`、`rag-pdf-page-raster-area`、`rag-pdf-ocr-region-fanout`。
- **前置问题（问用户）**：生产入口在 APISIX 之前是否有 CDN/WAF/Nginx？（决定边缘 limit 配在哪）
- **改动（两层）**：① APISIX 路由/全局配置加 `client_max_body_size`（webhook 路由单独定值，注意 provider 回调实际大小）；② 源内在**读取前**加 Content-Length 预检上限（BFF route.ts 的 arrayBuffer() 前、automations.py webhook 处理器、selection import）。
- **验收**：超限请求在边缘 413；单测覆盖源内预检分支。

### T7 运维面杂项（逐项小任务，最后）

- **T7a business-mock fail-closed**：`backend/mock/business_service/main.py:188-191` 在 INTERNAL_API_TOKEN 未配置时拒绝启动（仅 dev profile 允许 fail-open）——与 `rag_server.py:88-105` 的 require_internal_boot 同模式。compose 默认注入空值需同步改（docker-compose.yml:405）。
- **T7b webhook 身份链统一**：`apisix/apisix.yaml:144-153` 与 `backend/app/api/middleware/auth.py:63` 的豁免口径对齐，HMAC 密钥分发方式由用户确认后补文档。
- **T7c observability 留存**：trace_summary/analytics 表加时间清理（现有 14/180 天契约），与 `docs/observability/trace-model.md` 对齐。

## 五、Git 与协作规则（项目 AGENTS.md 全文适用，此处强调）

1. **每个 T 任务（或 T4 子任务）独立 worktree + 分支**，从最新 `origin/main` 创建；分支名 `fix/security-audit-<task>-20261108`。
2. **Conventional Commits**：`fix(security): ...`；一个提交一件事。
3. **全部走 PR 合并 main**（含自开自合）；PR 描述引用对应 fingerprint。
4. **测试纪律**：每项修复必须带回归测试（双租户夹具/无凭据对照），相关测试全绿后才能标记完成；新安全行为加进 rag_smoke 定向清单（如适用）。
5. **不做的事**：不修改 `C:\Users\wh\security-audit-skill\` 下任何审计产物；不"顺手"修复任务卡之外的指纹；不绕过/降级既有安全校验；遇到设计取舍（T3 方案 A/B、T4c 契约、T6 前置问题）先问用户再动手。
6. **顺序建议**：T1+T2 一批（当天）→ T5 + T4a/T4b 一批 → T3 → T4c/T4d → T6 → T7。每批合并后同步本文件勾选进度（PROGRESS.md）。

## 六、完成定义

- 涉及指纹在 findings.json 中对应的问题源码已修复，且每条修复的验证方案（NEEDS-VALIDATION.md 的 validation_plan.local 部分）可通过；
- 每项有回归测试且全绿；PR 全部合并 main；
- 本文件勾选进度，最终在 PROGRESS.md 记录"哪些指纹了结、哪些留待部署侧"。

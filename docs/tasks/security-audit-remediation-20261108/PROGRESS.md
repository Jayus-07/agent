# PROGRESS: 安全审计修复实施

## 状态
`IN_PROGRESS`——批次 1 完成（T1/T2/T5/T4a/T4b）；T3/T4c/T4d/T6/T7 待实施。

## 背景
- 审计：run-2（Cloudflare security-audit Skill，HEAD `8b2ba9aa`，2026-11-08 终态 incomplete）
- 审计产物（只读）：`C:\Users\wh\security-audit-skill\agent\run-2\`
- 部署观察（2026-11-08，本机共享栈）：1 confirmed-low（metrics）+ 3 rejected；无 body limit；多租户数据存在；prompt-eval environment 无保护。

## 用户决策（2026-11-08）
1. T3 选 **方案 A**（audit 仅放行只读端点，写/管理端点仍 enforce）。
2. T4c：MCP **机器主体固定 default 租户**并按现有 tool-context 谓词过滤；用户调用透传调用方租户。
3. T6：生产入口**无 APISIX 之外的前置代理**，body limit 落 APISIX + 源内预检。
4. T1：**强制 X-API-Key，暂不改 Prometheus 抓取配置**，在文档标注；observability profile 的 app target 补凭据前 down 属预期。
5. 节奏：先 T1+T2 合并，再 T5/T4a/T4b；本轮不启动 T3 之后的批次。
6. 工作树：本任务一个专用 worktree（`D:\Program Files\workplace\agent-wt-security-audit`），按 T 顺序切分支。

## 任务进度（勾选）
- [x] T0 建 worktree（基于 `origin/main` `5faeee0`）
- [x] T1 /metrics 纳入认证（PR #370，已合 main）
- [x] T2 GitHub environment 保护（配置动作，无代码 PR）
- [x] T5 改密重签从 DB 重读角色（PR #371，已合 main）
- [x] T4a 客服会话租户谓词（PR #372，已合 main）
- [x] T4b RBAC 会话管理租户谓词（PR #373，已合 main）
- [ ] T3 audit 模式白名单（方案 A）
- [ ] T4c MCP RAG/SQL 租户谓词
- [ ] T4d 选品漏斗租户/属主绑定
- [ ] T6 请求体上限
- [ ] T7 运维面杂项（a/b/c 逐项）

## 已知阻塞 / 待用户回答
- 无未回答问题。原 4 个问题已按「用户决策」关闭。

## 记录

### 2026-11-08 T1（/metrics 纳入认证）— PR #370
- 改动：`auth.py` 从 `_SKIP_AUTH_PREFIXES` 移除 `/metrics`；`server.py` 注释、`run_context_budget_eval.py` 抓取带 `X-API-Key`、`docker/prometheus.yml`、`docs/observability/slo.md`、`docs/operations/commands.md` 同步。
- 测试：`test_auth_middleware.py` **11 passed**（新增 4 条）；相邻守卫 **15 passed**。
- 实测（重建后）：无凭据 `/metrics` → 401；带 key → 200（51921 bytes）；`/health` 200；无凭据 `/api/agents` 401。
- 残留：`/ops` 页内 `fetch('/metrics')` 不带凭据；Prometheus app target 需手工配凭据（文档已标注）。

### 2026-11-08 T2（GitHub environment 保护）
- `prompt-eval`：required reviewer（Jayus-07，`prevent_self_review=false`）、仅 `main` 可部署、`can_admins_bypass=false`。
- 影响：`rag_regression.yml` / `prompt_eval.yml`（含 `repository_dispatch`）凭据 job 现在需人工批准。

### 2026-11-08 T5（改密重签从 DB 重读角色）— PR #371
- `change_password` 在同一事务内按 user_id 现读 `username/dept/role/cs_role/tenant_id` 重签；禁止透传旧 claim；`status!=1` → 403；令牌带 `sid` 且会话已吊销/不存在 → 401；旧格式无 `sid` 令牌保持兼容。
- 测试：`test_rbac_user_lifecycle.py`（真实 PG）**1 passed**，含「super_admin 旧 claim → 新令牌 viewer」「stale sid → 401」「禁用账户 → 403」。

### 2026-11-08 T4a（客服会话租户谓词）— PR #372
- `cs_admin.py`：新增 `_resolve_agent_identity_and_tenant`；claim/close/agent-messages 的 handoff/conversation 读写加租户谓词（JWT 通道）；API-Key 服务通道保持跨租户视图。
- `cs_ops.py` + `repository.py`：dispatch stats 对租户域 supervisor 按本租户过滤，缺租户 403；平台 admin 保持全租户。
- 测试：claim/ops 定向 **14 passed**；`test_cs_admin_access_regression.py` **19 passed**。

### 2026-11-08 T4b（RBAC 会话管理租户谓词）— PR #373
- `auth_local.py`：新增 `_session_tenant_scope`；列表/历史/强下线按租户过滤；super_admin 保留全库视图；他租 sessionId → 404 且不撤销。
- 测试：`test_auth_session_family.py::test_security_sessions_tenant_scope` + 全文件 **24 passed**（真实 PG + fake Redis）。

## 下一步（批次 2，待用户确认）
1. T3 方案 A：`deps.py` audit 分支仅放行只读，写/管理端点仍 enforce + 测试。
2. T4c：MCP RAG/SQL 工具补租户谓词与 `sql.read` 预检；双通道可见性对照测试。
3. T4d：选品漏斗 tenant/owner 维度 + 跨租户 clear_batch → 404。
4. T6：APISIX body limit + 源内 Content-Length 预检。
5. T7：business-mock fail-closed、webhook 身份链、trace 留存。


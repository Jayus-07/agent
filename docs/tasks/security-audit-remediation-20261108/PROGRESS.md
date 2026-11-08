# PROGRESS: 安全审计修复实施

## 状态
`IN_PROGRESS`——T1 已实施并验证，T2 已完成配置动作；等待 T1 PR 合并后继续 T5/T4a/T4b。

## 背景
- 审计：run-2（Cloudflare security-audit Skill，HEAD `8b2ba9aa`，2026-11-08 终态 incomplete）
- 审计产物（只读）：`C:\Users\wh\security-audit-skill\agent\run-2\`
- 部署观察（2026-11-08，本机共享栈）：1 confirmed-low（metrics）+ 3 rejected；无 body limit；多租户数据存在；prompt-eval environment 无保护。

## 用户决策（2026-11-08）
1. T3 选 **方案 A**（audit 仅放行只读端点，写/管理端点仍 enforce）。
2. T4c：MCP **机器主体固定 default 租户**并按现有 tool-context 谓词过滤；用户调用透传调用方租户。
3. T6：生产入口**无 APISIX 之外的前置代理**，body limit 落 APISIX + 源内预检。
4. T1：**强制 X-API-Key，暂不改 Prometheus 抓取配置**，在文档标注；observability profile 的 app target 补凭据前 down 属预期。
5. 节奏：先 T1+T2 合并，再 T5/T4a/T4b。
6. 工作树：本任务一个专用 worktree，按 T 顺序切分支。

## 任务进度（勾选）
- [x] T0 建 worktree（`agent-wt-security-audit`，基于 `origin/main` `5faeee0`）
- [x] T1 /metrics 纳入认证（confirmed-low）——PR 待合并
- [x] T2 GitHub environment 保护（配置动作，无代码 PR）
- [ ] T3 audit 模式白名单/时间窗（方案 A）
- [ ] T4a 客服会话租户谓词
- [ ] T4b RBAC 会话管理租户谓词
- [ ] T4c MCP RAG/SQL 租户谓词
- [ ] T4d 选品漏斗租户/属主绑定
- [ ] T5 改密重签从 DB 重读角色
- [ ] T6 请求体上限
- [ ] T7 运维面杂项（a/b/c 逐项）

## 已知阻塞 / 待用户回答
- 无未回答问题。4 个原问题已按上节决策关闭。

## 记录

### 2026-11-08 T1（/metrics 纳入认证）
- worktree：`D:\Program Files\workplace\agent-wt-security-audit`，分支 `fix/security-audit-t1-metrics-20261108`，基线 `5faeee0`。
- 改动：`backend/app/api/middleware/auth.py` 从 `_SKIP_AUTH_PREFIXES` 移除 `/metrics`；同步 `server.py` 路由注释、`run_context_budget_eval.py` 抓取带 `X-API-Key`、`docker/prometheus.yml` 与 `docs/observability/slo.md`、`docs/operations/commands.md` 说明。
- 测试：`python -m pytest backend/tests/test_auth_middleware.py -q --no-cov` → **11 passed**（新增 4 条 metrics 用例）；相邻守卫 `test_access_log.py + test_path_classes.py + test_apisix_b4_direct_routes.py` → **15 passed**。
- 本机重建：`devctl.bat rebuild -y`（compose 项目 `agent`，commit `5faeee0` + 未提交改动）；app/business-mock/worker 等 `Up (healthy)`。
- 实测：无凭据 `GET /metrics` → **401**；带 `X-API-Key` → **200（51921 bytes）**；`/health` 200；`/api/agents` 无凭据 401。
- 残留：`/ops` 看板前端 `fetch('/metrics')` 不带凭据会失败（该页本身也需 API Key，浏览器场景原本即不可用）；Prometheus `observability` profile 的 app target 需手工配凭据，已在文档标注。

### 2026-11-08 T2（GitHub environment 保护）
- `repos/Jayus-07/agent/environments/prompt-eval` 由 `protection_rules: []` 改为：
  - `required_reviewers`：Jayus-07（`prevent_self_review=false`，本人可批准自己的 run）；
  - `branch_policy`：自定义仅 `main` 可部署；
  - `can_admins_bypass=false`。
- 复核 `gh api .../environments/prompt-eval`：两条 protection rule 存在，`deployment_branch_policies` 仅 `main`。
- 影响：`rag_regression.yml`、`prompt_eval.yml`（含 `repository_dispatch` 自动触发）的凭据 job 现在都需人工批准后才执行；这是有意的供应链收紧，运维侧需知悉。

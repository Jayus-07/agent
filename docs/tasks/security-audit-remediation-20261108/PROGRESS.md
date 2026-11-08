# PROGRESS: 安全审计修复实施

## 状态
`COMPLETE`——批次 1 与批次 2 全部任务已合入 main（T1/T2/T3/T4a/T4b/T4c/T4d/T5/T6/T7a/T7b/T7c + 补强）。后续可选 T8/T9 见文末。

## 背景
- 审计：run-2（Cloudflare security-audit Skill，HEAD `8b2ba9aa`，2026-11-08 终态 incomplete）
- 审计产物（只读）：`C:\Users\wh\security-audit-skill\agent\run-2\`
- 部署观察（2026-11-08，本机共享栈）：1 confirmed-low（metrics）+ 3 rejected；无 body limit；多租户数据存在；prompt-eval environment 无保护。

## 用户决策（2026-11-08）
1. T3 方案 A（audit 仅放行只读，写/管理仍 enforce）。
2. T4c：MCP 机器主体固定 default 租户；用户调用透传调用方租户。
3. T6：生产入口无 APISIX 之外的前置代理。
4. T1：强制 X-API-Key，暂不改 Prometheus 抓取配置，文档标注。
5. 工作树：本任务专用 worktree，按 T 顺序切分支。

## 任务进度（勾选）
- [x] T1 `/metrics` 认证（PR #370）
- [x] T2 GitHub environment 保护（配置动作，无代码 PR）
- [x] T5 改密重签从 DB 读角色（PR #371）
- [x] T4a 客服 claim/close + 派单统计租户谓词（PR #372）
- [x] T4b 安全会话端点租户谓词（PR #373）
- [x] 补强：close 双租户测试 + 安全测试进 CI（PR #375）
- [x] T3 audit 只放行只读（PR #376）
- [x] T6 请求体上限（PR #377）
- [x] T7a business-mock 生产 fail-closed（PR #378）
- [x] T7b webhook 豁免口径对齐（PR #379）
- [x] T7c trace_summary + PG 镜像留存清理（PR #380）
- [x] T4c MCP RAG/SQL 租户与 sql.read 谓词（PR #381）
- [x] T4d 选品漏斗租户（PR #382）

## 证据摘要

### 批次 1
- T1：`test_auth_middleware.py` 11 passed；相邻守卫 15 passed；实测无凭据 `/metrics` 401、带 key 200。
- T5：真实 PG 生命周期测试覆盖「旧角色 claim → DB 新角色」「stale sid → 401」「禁用 → 403」。
- T4a：claim/SQL 谓词 14 passed；access 回归 19 passed；close 双租户真库测试 1 passed。
- T4b：真实 PG 双租户会话测试，列表/历史/强下线隔离；全文件 24 passed。
- T2：`prompt-eval` 加 required reviewer、仅 main 可部署、`can_admins_bypass=false`（gh api 复核）。

### 批次 2
- T3：守卫测试 21 passed；受影响 audit 测试 19 passed；配置写测试 33 passed。
- T6：`body_limit` 8 passed；实测网关 webhook 2MB→413、100B→401；app 9MB→413、1MB→401；三端 next build 通过。
- T7a：mock 启动守卫 4 passed；production 无令牌拒启。
- T7b：webhook 豁免测试 13 passed。
- T7c：PG 留存清理 3 passed。
- T4c：MCP/SQL 82 passed；MCP 与 HTTP 同源租户/表域。
- T4d：选品 + 路由门禁 **139 passed**（真实 PG 双租户隔离）；迁移 140 已应用（三表 `tenant_id` + `schema_migrations` 记录）；跨租户删除 404、缺租户 403。

## 剩余与后续建议
1. **T8（未实施）**：Prometheus 抓取凭据（`/metrics` 兼容 `Authorization: Bearer <API_KEY>` + `credentials_file`），当前 observability profile 的 app target 需手工配凭据。
2. **T9（未实施）**：API Key 治理（按调用方拆分、哈希存储、可吊销、审计与告警）。
3. 可选：选品 owner 维度与库存/订单/广告/成本执行面的 tenant+owner 授权（docs EC-05 已标注）。
4. 已知环境噪音：本机 `test_rag_authz.py` 9 条失败源于 KB 注册表被 CS 种子污染；CI 隔离库不受影响。
5. 本地 worktree 与任务分支可在确认后清理。

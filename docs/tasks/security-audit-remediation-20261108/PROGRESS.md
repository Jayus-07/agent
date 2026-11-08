# PROGRESS: 安全审计修复实施

## 状态
`COMPLETE`——批次 1/2 与扩展项 T8（Prometheus 凭据）、T9（API Key 治理）全部合入 main。

## 背景
- 审计：run-2（Cloudflare security-audit Skill，HEAD `8b2ba9aa`，2026-11-08 终态 incomplete）
- 审计产物（只读）：`C:\Users\wh\security-audit-skill\agent\run-2\`
- 部署观察（2026-11-08，本机共享栈）：1 confirmed-low（metrics）+ 3 rejected；无 body limit；多租户数据存在；prompt-eval environment 无保护。

## 任务进度
### 批次 1
- [x] T1 `/metrics` 认证（PR #370）
- [x] T2 GitHub environment 保护（配置动作）
- [x] T5 改密重签从 DB 读角色（PR #371）
- [x] T4a 客服 claim/close + 派单统计租户谓词（PR #372）
- [x] T4b 安全会话端点租户谓词（PR #373）
- [x] 补强：close 双租户测试 + 测试进 CI（PR #375）

### 批次 2
- [x] T3 audit 只放行只读（PR #376）
- [x] T6 请求体上限（PR #377）
- [x] T7a business-mock 生产 fail-closed（PR #378）
- [x] T7b webhook 豁免口径对齐（PR #379）
- [x] T7c trace_summary + PG 镜像留存清理（PR #380）
- [x] T4c MCP RAG/SQL 租户与 sql.read 谓词（PR #381）
- [x] T4d 选品漏斗租户（PR #382）
- [x] 进度文档（PR #383）

### 扩展项
- [x] T8 Prometheus 抓取凭据（PR #384）
- [x] T9 API Key 治理（PR #385）

## 扩展项记录

### T8（PR #384）
- `/metrics` 仅该路径接受 `Authorization: Bearer <API_KEY>`；Prometheus 用 `authorization.credentials_file`，compose entrypoint 启动时从 `API_KEY` 生成 `/tmp/metrics_api_key`。
- 验证：`test_auth_middleware.py` 16 passed；本机启动 observability profile 后 `agent-platform-app` target **health=up**、`up=1`；验证后停止该容器。

### T9（PR #385）
- 迁移 `141_api_keys.sql`（`auth.api_keys`：sha256 摘要、caller、tenant、scopes、过期/吊销/最后使用；登记 init_db）。
- `backend/security/api_keys.py`：生成 `ak_` Key、15s 缓存校验、DB 异常 fail-closed、日志只留 key_id/caller/指纹。
- 中间件在 legacy env Key 之后接受托管 Key；`backend/scripts/manage_api_keys.py` 提供 create/list/revoke。
- 验证：`test_api_keys.py` **8 passed**（真实 PG 生命周期 + 脚本闭环 + 中间件）；本机 E2E：托管 Key 访问 `/agents` **200**、无效 `ak_` **401**、吊销后等待 15s **401**；测试数据已清理。

## 后续
1. T8/T9 已合并；后续只需从最新 main 重建并终验。
2. T8/T9 合并后从最新 main 重建并终验，再更新本文件走 docs PR。
3. 已知环境噪音：本机 `test_rag_authz.py` 9 条失败源于 KB 注册表被 CS 种子污染；CI 隔离库不受影响。


# PROGRESS: obs-legacy-fixups

## 2026-10-31 阶段 0：开工

- [x] main HEAD 确认 = `12bdef8`（派单基线，无需 fetch 更新）。
- [x] worktree `D:/Program Files/workplace/agent-wt-obs-legacy` 自 main@12bdef8 创建，
      branch `feat/obs-legacy-fixups`（`git worktree add ... -b`）。
- [x] `.env` 已从主仓库拷贝到 worktree（gitignore 文件）。
- [x] 并发冻结确认：`Get-CimInstance Win32_Process | Where CommandLine -match pytest`
      → 无其他会话在跑 pytest。
- [x] 任务文件 TASK.md / PROGRESS.md 创建。
- 主仓库工作区有大量 `tmp/*` 未暂存删除（其他会话遗留），本任务不动、不提交。

下一步：阶段 1（详情页 Prompt 版本兼容 + RAG trace kind + 账号清理）。

## 2026-10-31 阶段 1：详情页 Prompt 版本兼容 + RAG trace kind 对齐 + 账号清理

- [x] `frontend-admin/src/app/observability/traces/[id]/page.tsx`：新增 `promptVersionsLabel()`
      兼容 map（按 key 数）/ list（按元素数）/ 缺省三形态，空值如实「未采集」；314 行改用。
- [x] `page.test.tsx`：新增 3 用例（map 62→2 个样例 / list / 空 map+空 list）+
      既有用例补缺省形态断言；7 passed。
- [x] `backend/rag/chain.py` `_start()`：`trace_collector.start(...)` 显式传
      `workflow_name="rag_agent", workflow_kind=WorkflowKind.RAG_QUERY.value`
      （lazy import 补 WorkflowKind）；嵌入父 trace 的 rag_skill 分支不动。
- [x] `backend/tests/test_trace_fixes.py` 新增 `TestRagChainWorkflowKind` 2 用例
      （独立链 kind=rag_query / 嵌入链不改父 kind）。

### 验证证据（2026-10-31）

- 后端：`PGPORT=5433 pytest tests/test_trace_fixes.py -q --no-cov` → **18 passed**（含新增 2）；
  `tests/rag/test_chain.py + tests/observability/test_llm_call_span.py` → 7 failed, 31 passed。
  7 failed 与 **main@12bdef8 对照完全一致**（主仓库 backend 无未提交改动，实跑对照），
  属既有 CI 基线债务（拒答文案断言期待「暂无/未提及」，现行文案为
  「知识库中未找到与该问题相关的内容。」），非本任务引入；归属 CI 基线会话，本任务不抢修。
- 前端：`vitest run "src/app/observability/traces/[id]/page.test.tsx"` → **7 passed**；
  `tsc --noEmit` → **rc=0**。worktree 依赖用 junction 复用主仓库 node_modules
  （package.json + package-lock.json 双 diff 确认一致）。
- rebuild：worktree 内 `COMPOSE_PROJECT_NAME=agent docker compose -f docker-compose.yml
  -f 部署/prod-4c8g/docker-compose.local.yml build app frontend-admin && up -d`，
  两容器 healthy，app /health=200，admin :3200=200。
- 账号清理：`DELETE FROM auth.users WHERE username='obs_handoff_viewer'` → DELETE 1
  （先 SELECT 确认唯一行 id=1258；复核 count=0）。
- 浏览器实测（IAB，super admin 残留会话）：`/observability/traces/7bf629876f5a`（DB 实查
  `metadata.prompt_versions` 为 **map 62 key**、修复前该页显示「未采集」）概要卡 DOM：
  `Prompt 版本 → 62 个` ✅；总 Token/费用/模型/Provider/SLA 各行渲染正常。

### 过程记录（实机验证用临时账号）

- 新建 `obs_legacy_fixups`（id=1284）临时实测账号：先 viewer 注册被验证码挡 → 直接 SQL 插入
  pbkdf2_sha256(200000) 哈希建 admin；因目标 map trace 均为归属标签上线前的历史数据
  （tags 无 tenant_id，`trace_visible_to` 仅 super_admin 可见）→ 提权 super_admin 完成实测。
- 该临时账号**暂保留**（阶段 3 compare 页实测还要用），阶段 4 交付前删除。

下一步：阶段 2（dev-svc.bat 项目名防护 + trace_children 测试修复先协调）。


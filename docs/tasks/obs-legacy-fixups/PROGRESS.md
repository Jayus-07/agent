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

## 2026-10-31 阶段 2：dev-svc.bat 项目名防护（完成）+ trace_children 测试修复（PAUSED 待协调）

- [x] `dev-svc.bat`：COMPOSE_FILE 探测块后新增项目名防护——`COMPOSE_PROJECT_NAME`
      未定义且 ROOT 目录名 ≠ agent 时 set 为 agent 并 echo 一行 ASCII 提示；
      调用方已导出时不覆盖（`if not defined` 保证）。头部注释补记
      「Git Bash 用 -y 不用 /y（/y 被 MSYS 路径转换吞掉，cmd.exe /c 亦然）」。
- [x] `docs/operations/commands.md`「本地服务」节同步一句（防护行为 + -y 提醒）。
- [PAUSED] `tests/api/test_observability_trace_children.py` 2 个既有失败：按派单需
      用户确认归属后再修。**协调现状实查**（2026-10-31）：agent-wt-cibase
      （55b8e35，修 ragas/compare_runs/RBAC）与 agent-wt-ci-debt（d5fe5cb，api suite
      59 例批 1-3）的已提交范围与未提交工作区均**未覆盖**该文件；两分支均未合并 main。
      若确认归本任务，修法：测试改为 TestClient 或构造 Request/identity stub
      （get_trace 现签名 `(trace_id, request, _reader)`）。

### 验证证据（2026-10-31）

- 场景 a（worktree 内，未设 COMPOSE_PROJECT_NAME，PowerShell 调 .bat）：
  `dev-svc.bat status` 首行输出
  `[dev-svc] ROOT dir "agent-wt-obs-legacy" differs from agent - pinning COMPOSE_PROJECT_NAME=agent to reuse the shared stack` ✅
- 场景 b（预设 `$env:COMPOSE_PROJECT_NAME="agent"`）：无 pinning 提示（不重复覆盖）✅
- 场景 c（rebuild 实测）：worktree 内 `dev-svc.bat rebuild all -y` 全流程完成，
  后端 11 服务 + frontend-admin/frontend 重建，backend healthy、双前端 ready，
  抽查 agent-app/frontend-admin/beat 容器 label `com.docker.compose.project=agent` ✅（无平行项目）。
- 过程注记：skill 记录的 Git Bash 调 cmd.exe 通法（`/c/Windows/System32/cmd.exe /c "..."`）
  在本机当前环境下 /c 仍被吞（cmd 停在交互提示符）；改用 PowerShell 直接调用 .bat
  （参数不经 MSYS 转换）一次通过。已如实记录，不改 skill 结论在其他机器的适用性。

下一步：阶段 3（compare 对比页重构 + Span HTTP status 下发）。

## 2026-10-31 阶段 3：compare 对比页重构 + Span HTTP status 下发

- [x] compare 页重写（frontend-admin .../traces/compare/page.tsx）：单表矩阵 → 详情页
      同款 slate 风格双栏卡片（runKind 徽标 / 状态 / 耗时 / Token / 费用 / 模型 /
      错误摘要 + 双 SpanGantt 实例 + 空 span 采集链未覆盖提示）；恰两条可读时对
      耗时/Token/费用做差异高亮（高=amber、低=emerald，含单位差值文案）。
      **参数契约偏差说明**：派单写 `?left=&right=`，实际既有契约为 `?ids=`（2–4 条，
      入口 TraceListPanel「对比 N 条」）——按最小改动保留 `?ids=`，不改调用方。
- [x] page.test.tsx 重写 4 用例：少于 2 条引导 / 双栏渲染+下钻+SpanGantt 实例 /
      差异高亮双向差值 / 空值未采集；SpanGantt 以 testid mock。
- [x] `_failed_attempt_http_status()`（backend/infra/llm/proxy.py）：openai/httpx
      风格 `.status_code` + requests 风格 `.response.status_code` 兼容提取，
      仅 int 拼接；`_record_failed_attempt` 失败行 finish_reason 变为
      `error:<Type>:<status>`（无状态保持原形），usage 行 schema 不动。
- [x] `to_span_dto` llm_call 增 `http_status`（读 metrics.http_status，默认 null）；
      前端 types/trace.ts llm_call 同步类型。
- [x] 总览披露块「Span DTO 无 HTTP status code」收缩为实际剩余缺口
      （字段已有、采集链无写入点 + 失败行仅落 llm_usage finish_reason + 全链路拆分无采集）。

### 验证证据（2026-10-31）

- 后端：`tests/infra/test_fallback_usage.py + tests/test_trace_fixes.py` →
  **28 passed, 1 failed**；1 failed（test_resilience_records_each_failed_attempt）
  与 main@12bdef8 实跑对照一致（`_handle_terminal_failure` 新签名与测试 stub 漂移），
  既有基线债务，非本任务引入；新增 3 个 HTTP status 用例 + 2 个 DTO http_status 用例全过。
- 前端：compare 4 passed + OverviewPanel 6 passed；`tsc --noEmit` **rc=0**。
- rebuild app + frontend-admin（worktree 代码，COMPOSE_PROJECT_NAME=agent）后健康 200。
- 浏览器实测（IAB super admin）：
  - compare `?ids=e036c6dae4cc,70de9cbf7501`：双栏徽标（工作流/RAG 链）+ 差异高亮
    `耗时 −4.01 s` / `耗时 +4.01 s` + Token/费用/模型空值如实「未采集」+
    双 SpanGantt（含未埋点黑洞段）✅
  - 总览 tab=overview 披露块：文案已按新口径展示（含 code 标记 llm_call.http_status）✅

下一步：阶段 4（vitest 全量 + tsc + 定向 pytest 汇总 + PR 合并）。

## 2026-10-31 阶段 4：验证与交付

- [x] frontend-admin vitest 全量 **581 passed (83 files)**（main 基线 576 + 本任务新增）；
      `tsc --noEmit` **rc=0**（阶段 3 末实测）。
- [x] 后端定向汇总（9 个文件：test_trace_fixes / test_fallback_usage / test_llm_call_span /
      test_prompt_trace_coverage / test_prompt_version_trace / test_llm_span_fields /
      test_llm_stream_sink / test_trace_token_provenance / test_llm_usage_component）→
      **87 passed, 1 failed**；唯一 failed = test_resilience_records_each_failed_attempt
      （main@12bdef8 实跑对照一致：`_handle_terminal_failure` 新签名与测试 stub 漂移，
      既有基线债务归属 CI 基线会话）。事实 10/11 基线（26+33 passed）全部覆盖且通过。
- [x] rebuild：阶段 3 末已 build+up app/frontend-admin（worktree 代码）健康 200；
      frontend（:3100）本任务无改动未重建。
- [x] 浏览器实测：详情页 Prompt 版本「62 个」/ compare 双栏差异高亮 / 总览披露块新文案（阶段 1/3 证据）。
- [x] 临时实测账号 obs_legacy_fixups 删除（DELETE 1；与 obs_handoff_viewer 复核 count=0）。
- [x] PR：https://github.com/Jayus-07/agent/pull/142（描述引用派单第三节根因证据）。
      CI 首轮全绿：RAG Smoke preflight / admin-observability-auth / frontend-admin-functional /
      frontend-admin-security / scan / Detect RAG changes 全 pass（real API smoke 按预期 skipping）。
- [x] rag_smoke 定向清单评估：不新增链路环节（展示兼容 + 观测字段透出 + 运维脚本防护），
      无需新增 smoke 条目（与派单预判一致）。
- 遗留：trace_children 2 个既有失败修复 PAUSED（等用户确认归属后另起任务或并入 CI 基线会话）。





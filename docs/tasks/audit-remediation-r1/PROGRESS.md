# audit-remediation-r1 — PROGRESS

## 环境与位置

- Worktree：`D:\Program Files\workplace\agent-wt-audit-r1`（从 main@70f6351 创建）
- 分支：`fix/audit-remediation-r1`
- .env：已从主仓库拷贝（worktree 默认无 .env）

## 已完成

- [x] P0-1 `backend/app/api/routes/data.py`：
  - filename 守卫（拒绝分隔符/空/隐藏/NUL，不静默改名）
  - realpath 归一 + commonpath 包含校验（双层防线，模板对齐 rag_upload）
  - 流式读取 + `DATA_UPLOAD_MAX_BYTES`（50MB）上限
  - 响应与日志改用 `safe_name`，不再回显原始 filename
- [x] P1-2 `backend/tasks/error_taxonomy.py` + `backend/observability/error_taxonomy.py`：
  - `service_unavailable` 进 `TASK_ERROR_TYPES`（取值域补齐）与 `_RETRYABLE_TYPES`（口径对齐）
  - `_TASK_MAP` 增加 `service_unavailable → network_error`（与 Tool 层 UNAVAILABLE 同口径；导入期 fail-fast 通过）
  - 模块 docstring 的 Retry 口径同步
- [x] P1-3 `backend/orchestration/graph/runner.py` `_worker`：
  - 绑定段单独包层 try/except；失败走 `_runner_error_event` 同口径 error 帧 + 部分绑定 ContextVar 清理 + done sentinel 后 return
  - 正常路径（图执行主体 try/except/finally 与 sentinel 投递）零改动
- [x] 回归测试三份：
  - `backend/tests/api/test_data_upload_path_guard.py`（新增，7 例）
  - `backend/tests/rag/test_pipeline_not_ready.py`（追加 cancel/retry 口径一致性用例）
  - `backend/tests/orchestration/graph/test_stream_events_flow.py`（追加绑定失败终结流用例）

## 验证证据

- `py_compile`：全部改动源文件 + 测试文件通过。
- pytest 定向（worktree，非全量），全部通过：
  - P0-1/P1-2 初版：`test_data_upload_path_guard.py`（7 例）+ `test_pipeline_not_ready.py`（含新口径用例）→ 11 passed
  - P1-3/词表：`test_stream_events_flow.py` + `test_error_taxonomy_unify.py` + `test_task_phase2_step2_error_taxonomy.py` + `test_task_cancel.py` → 102 passed
- **与 main 合并（eb405b3）后复验**：main 侧 0f74940（S3/S4，另一会话）已修复 data.py 路径穿越并加 admin 鉴权，且 runner.py 增加 copy_context worker 包裹。据此：
  - data.py 冲突以 main 版为基，仅叠加仍缺失的流式 50MB 大小上限（S4 未覆盖的内存缺口）；
  - `test_data_upload_path_guard.py` 重写为锁定合并后语义（净化不逃逸 + admin 桩 + 超限拒绝）→ 6 passed；
  - `test_stream_events_flow.py`（copy_context × 绑定段包层组合语义）→ 30 passed。
- PR CI（#283）全绿：RAG Smoke preflight、Evaluation V2 free contract / admin build、secret-scan、Detect RAG changes。

## 提交与合并记录

- 1cb8d04 fix(data)：P0-1（初版，后与 main S4 合流）
- 9d410b3 fix(tasks)：P1-2
- 8a5945f fix(runtime)：P1-3
- 6b80db6 docs(tasks)：立项档案
- eb405b3 merge origin/main + data.py 冲突消解（main S3/S4 为基 + 大小上限）+ 测试语义重写
- PR：#283（CI 全绿后合并 main）

## 未完成 / 并行协调备注

- 另一会话的审计线（0f74940 S3/S4、`docs/tasks` 回填「W4/router 收官与 W5 实施」）与本项目后续轮次可能重叠：**启动 P1-5（competitor 域鉴权）、P2 批次前必须先核对 main 最新任务档案与其 S/W 编号清单，避免同题两套实现**（本次 data.py 即发生一次重叠，靠合并消解）。
- ~~P1-1（checkpointer 连接池）~~ → 第二轮已实施，见下节。
- P1-4（网关直通面）、P2 批次待后续轮次立项。

## 第二轮：P1-1 checkpointer/审批存储连接池收口（fix/audit-r2-checkpointer-pool）

**实现**：
- 新增共享构建 `backend/orchestration/checkpoint/pg_saver.py`（psycopg_pool + 借出前探活 + `connect_timeout=5` + 幂等 setup），四处 PostgresSaver 站点（主图/travel/CS/task_executor）全部接入；task_executor 保留自身进程级单例与锁。
- 同病收口：`backend/security/tool_approval.py` `_PgApprovalStore`（治理关键面：审批单存储是进程级单例 + 长活裸连接）同步池化，API 不变（`_cursor` contextmanager 包装池借出）。
- server.py:180 的裸连接是一次性 DDL + 立即关闭，用法正确，不动。

**过程中的环境级发现（重要）**：
- 首跑恢复套件 4 例 PoolTimeout——**独立脚本复现证明与本次重构无关**（同配置直连 OK、池 30s 拿不到连接；旧 task_executor 模板同参数，属存量隐患）。
- 根因：vpnkit（Docker Desktop→宿主 PG）半开路径上无超时的 TCP 握手会**无限等待并卡死池 worker 线程**（连 pool.close() 都停不下来，pyproject/pytest.ini 对该病态有既有记载，但只做了测试级看门狗，连接级无防护）。池的 open=True 后台建连踩中即全量 PoolTimeout。
- 修复：连接 kwargs 加 `connect_timeout=5`（快速失败交给池自动重试，正是池的设计意图）；守卫测试锁定该参数防回退。

**验证（全绿）**：
- `test_pg_saver_pool.py`（新增 4 例：探活/autocommit/connect_timeout 参数锁定、task_executor 单例、三站点防回退文本守卫）+ `test_checkpointer_degrade_policy.py`（故障注入夹具 psycopg→psycopg_pool 适配）→ 25 passed
- `test_task_checkpoint_recovery.py`（真 PG 崩溃恢复演练）→ 4 passed（6.5 min，修复前 4 ERROR PoolTimeout）
- `test_task_executor_lease_interface` + `security/test_tool_approval` + `test_email_durable_idempotency` + `test_startup_validation` → 82 passed
- `travel/test_checkpointer` + `travel/test_persistence` + `travel/test_travel_graph`（含池守卫）→ 84 passed

## 阻塞

- 无。

## 第三轮：P1-5 competitor 域角色闸收口（fix/audit-r3-competitor-authz）

**撞车核对结论**：main `45cdbdd`（并行审计线）已给 9 个写端点建「可信身份 + 审批 + 幂等」边界（`_run_idempotent_write`）并给 `test-cookies` 挂 admin——但**缺角色闸**（viewer 也能过身份校验），且 6 个读端点（含 GET /cookies 凭据状态）与 qr-login/start 完全无闸。P1-5 剩余缺口据此收口。

**实现（backend/app/api/routes/competitor.py，纵深双层中的应用层权威闸）**：
- admin 档（Depends(require_admin_user)）：GET/POST/DELETE `/cookies`、`/qr-login/start`、`/qr-login/poll`（凭据面 + 外呼副作用；与既有 test-cookies=admin 口径一致）
- editor 档（Depends(require_editor_user)）：watchlist 读/增/删/改、`/history`、`/stats`、`/analyze`、`/scan`、`/retry-blocked`、`/anti-ban/stats`、`/anti-ban/resume`、`/qr-login/platforms`
- 公开读（有意不挂闸，docstring 裁决说明）：GET `/recommendations`（与 /selection/recommendations 同口径业务读，挂闸会锁死用户端选品推荐）
- 灰度语义不变：SENSITIVE_API_GUARD_MODE=audit 下低角色放行（前端 viewer 用户流不打破），enforce 下 403

**记录为后续项（本轮不做）**：store 层租户列改造——`competitor.store_pg` 的 watch/snapshot/config 表均无 tenant 维度（url UNIQUE 全局），需迁移 + list/add/snapshot/history 全链路改造；在角色闸收口后风险等级已大幅下降（越权者先被角色挡住）。

**验证**：
- 新增 `backend/tests/api/test_competitor_authz_gate.py`（5 passed）：18 端点分档结构断言（APIRoute dependant 反射，防回退）+ enforce 拒绝/放行行为断言 + audit 灰度锁死 + recommendations 公开读锁死
- 既有套件（test_competitor / api_idempotency_contract / cookie_manager_tenant_safety）→ **204 passed**（零回归；1h50m，竞品爬虫测试含真实网络等待）

## 下一步

1. ~~回填 runner/词表/任务取消套件测试结果~~（已回填：102 passed）。
2. 按 Git 并行开发规范展示 Diff + 验证结果，等用户确认后路径限定提交（`git add -A -- <paths>` + `git commit -- <paths>`），走 PR 合并 main。
3. 后续轮次立项（P1-1 / P1-4 / P1-5 / P2 批次），见 TASK.md 范围节。

## 备注

- Mimosa PreToolUse hook 对 `os.path.join(base, f"...")` 内联 f-string 拼路径会拦截；改为先提取 `stored_name` 变量再 join + realpath 包含校验后通过。后续在此仓库写路径相关代码时沿用该形态。

## 第四轮：P1-4 网关直通面收口（fix/audit-r4-gateway-b4，PR #287）

**实现（apisix/apisix.yaml，纯网关配置）**：
- 移除三条无鉴权直通路由：`/metrics`（Prometheus 实际抓 host.docker.internal:8000，不经网关）、`/openapi.json`（侦察面，BFF 只代理 /api/*）、`/`（无消费价值）——网关侧无匹配即 404
- 保留 `/health` 直通：内容不含敏感配置（安全审查已验证），供外部探活；容器 healthcheck 走容器内 127.0.0.1:8000，不依赖网关
- 兜底 `/api/*` gateway-auth 不动；头部注释记录 B4 收口裁决与消费方事实
- 后端 middleware/auth.py 豁免清单未动（app:8000 仅绑 127.0.0.1 + API-Key fail-closed，直连面已有纵深）

**验证**：
- 新增 `backend/tests/api/test_apisix_b4_direct_routes.py`（3 例：三条直通不存在 + /health 保留 + 兜底鉴权锁死）+ 既有 ws_route 契约 → 4 passed；YAML 语法校验通过
- PR #287 CI 全绿，合并 809c5c0（11:21:03Z）
- **网关级实测**（重启 agent-apisix 加载新 yaml，归属核查=主栈 compose 管理非孤儿）：`GET /metrics → 404`、`GET /openapi.json → 404`、`GET / → 404`、`GET /health → 200`、`POST /api/auth/login → 400`（转发+参数校验正常）、`GET /api/competitor/stats → 401`（gateway-auth 正常，第三轮角色闸在网关后工作正常）

**遗留（非仓库代码，部署检查清单项）**：生产 ECS 确认 9080 不出宿主机/安全组。

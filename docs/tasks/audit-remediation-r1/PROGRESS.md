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

## 第六轮：P2 批次·核心链路工程质量四项（fix/audit-r6-engineering-fixes）

**实现**：
1. **任务流 SSE 订阅窗口补帧**（tasks.py）：入口快照与 `subscribe_events` 之间窗口内发布的终态事件在 Redis pub/sub（无重放缓冲）永久丢失 → 订阅建立后重查 DB 权威状态，已终态即补终态帧收口（幂等，未终态零开销）——客户端不再依赖 heartbeat 无限等待。
2. **会话守卫锁过期释放**（agent_chat_tasks.py）：守卫体内 seed_task+submit_task 可能超锁 30s TTL，`release` 抛 `LockNotOwnedError` 会把已成功提交的任务掩盖成 500 → 吞 LockNotOwnedError（正常路径，info 日志）；LockError 记 warning。
3. **task_service 池化**：新增 `_pooled_conn()`（psycopg_pool，借出前探活 + connect_timeout，惰性建池兼容 Celery prefork），全部 33 处 with 场景切换；`_conn()` 保留裸连接语义供事务所有权场景（evaluation/managed_tasks 显式事务执行器）。修复心跳路径 renew_lease 每 15s/任务新建完整 TCP+auth 握手。
4. **内网 embedding 超时**（embedding_singleton.py）：自建 httpx client 显式 `timeout=timeout`——openai SDK 传入自定义 client 时请求级超时取自该 client（默认近乎无限），此前 `EMBEDDING_REQUEST_TIMEOUT` 对内网直连路径失效。

**验证**：
- 新增 `backend/tests/api/test_engineering_p2_fixes.py`（5 passed）：补帧收口行为（终态帧 + 不进心跳循环）/ 锁过期吞与业务异常照常上抛 / 池化源码防回退（with 场景全池化、探活参数、_conn 保留）/ embedding 超时源码断言
- 受影响面既有套件（agent_chat_tasks / task_admission_runtime / phase2_runtime_contract / task_cancel）→ **39 passed**（2m17s，零回归）

## 第五轮：P2 批次·认证面加固（fix/audit-r5-authn-hardening）

**撞车核对**：main 9159e1b 上登录无任何失败计数/锁定机制、demo 两端点无闸——P2 各项未被并行线覆盖。

**实现四项**：
1. **登录账号级防爆破**（auth_local.py）：`auth:lockout:{tenant}:{username}` 计数（认证 Redis 6380），15 分钟滚动窗口内失败 5 次 → 锁定 15 分钟（429）；成功登录清零；命中阈值落 `LOGIN_BRUTE_FORCE_LOCKED` 安全事件；Redis 不可用 fail-open（与 logout 黑名单同哲学，记 warning 不静默）。**计数只对「存在的账号」触发**——不存在的用户名永不计数/锁定：既有契约测试 `test_repeated_invalid_login_attempts_do_not_lock_account` 钉死了「不锁账号」语义（防「知道用户名即可锁死他人登录」的第三方 DoS 向量），据此把计数限定在「用户存在但凭据错误」，爆破防护对真实账号完整生效、既有契约原样保留（该测试同步注入认证 Redis 桩保证确定性隔离）。
2. **注册查重租户化 + 迁移 131**：查重 SQL 加 `AND tenant_id = :tenant_id`（枚举面从全平台缩到单租户）；**连带发现活库 username 全局唯一约束实际缺失**（初始化差异）——迁移 `131_auth_username_tenant_unique.sql`：幂等删列级全局 UNIQUE（有则删无则跳）+ 空租户存量收口 default + 列默认值 default + `(tenant_id, username)` 联合唯一索引兜底。
3. **验证码 CSPRNG**：签发码 `random.choice` → `secrets.choice`（验证码是安全凭证；SVG 干扰线角度/颜色保留 random）。
4. **demo 角色闸**：`/demo/seed`、`/demo/run/{id}` 挂 `require_admin_user`（写库 + workflow/LLM 消耗面）。

**验证**：
- 新增 `backend/tests/api/test_authn_hardening.py`（10 passed）：锁定阈值/租户分桶/成功清零/Redis 宕 fail-open/安全事件落点 + 登录级行为（ghost 永不锁定 8×400 / alice 阈值后 429）+ login 接线/查重租户化/验证码 CSPRNG 源码级防回退 + demo 闸结构断言
- `test_auth_local.py` 全套恢复通过（含既有不锁定契约，注入 Redis 桩）→ 新旧合计 15 passed
- 迁移 131 活库事务干跑（BEGIN→执行→ROLLBACK）通过，合并后由 db-migrate 自动执行
- auth 关联套件（session_family / refresh_claims / rbac_user_lifecycle / security_ops / sys_config_admin）→ 54 passed（仅本次受影响的 login 用例外全部通过）

## 第七轮：P2 收官三项（fix/audit-r7-p2-wrapup）

**实现**：
1. **OOXML 部件实体扩展防护**（pptx_parser.py）：新增 `_parse_ooxml_xml`（前置 DTD/ENTITY 检测 fail-closed + feed/close 解析，同 xml_parser 口径），5 处裸 `ET.fromstring(archive.read(...))` 全部替换。合法 OOXML 部件全为 namespace 化元素，零误伤。xml_parser 同类问题已由 1458070 修复（核对确认），本轮补齐 pptx 侧 5 处。defusedxml 离线不可用，零依赖方案（Mimosa hook 拦截后按其建议口径调整形态通过）。
2. **OCR 用量接入统一预算链路**（ocr.py）：`_record_ocr_usage` 成功分支追加 `record_model_usage` 结算——存在请求级预算上下文（/chat 内 RAG 技能实时解析）时 token 计入预算；后台索引任务无上下文自然跳过（no_request_budget）。软失败不影响主流程；视觉调用不走 proxy 为既有裁决（文本 chat 角色通道，改造风险大于收益）。
3. **/chat/messages DTO 契约**（schemas.py + chat.py）：`ChatMessagesRequest`（session_id ≤128、messages ≤200 条、单条 role ≤32 / content ≤100k 白名单）——裸 dict 直透持久层收口为 422 语义。

**competitor 租户列裁决（暂缓）**：当前单租户部署现实（网关注入 default、迁移 131 刚收口空租户），租户列改造需多表迁移（watch/snapshot/config/events）+ 爬虫管线扫描语义重构 + watch url 全局 UNIQUE 改联合唯一——为不存在的多租户场景做高风险迁移不符合最小改动原则。**标注为「多租户启用前必做」前置项**；角色闸（第三轮）已把越权者挡在门外，现阶段风险可接受。

**验证**：
- 新增 `backend/tests/api/test_p2_wrapup_fixes.py`（8 passed）：实体爆炸 payload 拒绝 / 合法部件零误伤 / OCR 预算结算与 error 跳过 / messages 超批超长缺字段 422 + 合法放行
- 既有套件（chat_memory_persistence / enterprise_document_structure / ocr_registered_model / session_ownership）→ 26 passed，零回归

## P3 批次 B（fix/audit-p3-b，基线 ab22fb2）：B1-B4 四项小修

### B1 xml_parser 对齐 `_parse_ooxml_xml` 形态（CWE-776 扫描盲区清扫）
- `rag/preprocessing/parser/xml_parser.py`：`ET.fromstring` → `XMLParser().feed()+close()`，
  前置 DTD/ENTITY 拒绝守卫原样保留（与 pptx_parser 口径一致，安全性由前置拒绝保证）。
- 回归：合法 XML 仍 parse_status=complete（新增功能断言）+ 源码防回退（禁止 `ET.fromstring`）。

### B2 列表分页收口
- `routes/tasks.py`：GET /tasks 新增 `offset: Query(0, ge=0)`；`services/task_service.py`
  `list_tasks_for_user` 加 offset 参数（SQL `OFFSET %s`，负数钳 0）。向后兼容（默认 0）。
- `routes/agent_chat_tasks.py`：GET 列表 limit 从固定 100 改 `Query(100, ge=1, le=100)`。
- `routes/memory.py`：extractions 列表 limit 从裸 int=100 改 `Query(100, ge=1, le=200)`
  （与 service 层 `safe_limit≤200` 同口径）。
- 说明：`agent_chat_tasks._cancel_previous` 内部清扫的 limit=100 为实现细节非 API 分页，
  保持不动。

### B3 /admin/email/channel 挂 admin 闸
- `routes/admin_email.py`：router 级 `Depends(require_admin_user)`——SMTP host/账号存在性
  属运维敏感面，此前 viewer 可见；与 admin_* 其他路由同口径。只读语义不变。

### B4 tenant 空值回退改 fail-closed
- `routes/plan_revisions.py:_tenant`：header 与 legacy 双分支空租户一律 403「缺少可信租户身份」
  （对齐 `cs_admin._resolve_tenant_scope`，绝不回落 'default'）。
- `routes/travel.py`（审计原 :909，现 :918 偏移）：`/plan`（retired V1）在路由层
  `require_identity` 后加空租户 403 守卫——worker 闭包内异常会被吞成计划错误，
  必须在启动前拦截；worker 内 `or "default"` 回退移除。
- **遗留如实标注**：travel.py 其余 17 处 `or "default"`（多为 retired V1 读路径与 v2 流）
  不在本批范围（handoff 仅点名 :909）；如需全域 fail-closed 应独立批次逐路由补守卫。

### 验证
- 新增 `backend/tests/api/test_audit_p3_fixes.py`（11 passed）：xml 功能回归+源码防回退 /
  三端点 limit·offset 参数契约 / service OFFSET 钳 0 / admin_email 角色闸结构断言 /
  plan_revisions._tenant 双分支 403 + 合法透传 / travel 路由守卫位置 + worker 无回退。
- 受影响面既有套件：test_enterprise_document_structure + test_plan_revision +
  test_engineering_p2_fixes（tasks/agent_chat_tasks 补帧/锁语义）→ 43 passed 零回归；
  test_apisix_b4_direct_routes → 3 passed。
- py_compile 全部改动文件通过。


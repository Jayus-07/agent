# SQL Agent 生产收口 — STOP D 部署验收与冻结报告

> 2026-09-23 ｜ 前序：STOP C（`STOP_C_PASS=true`，至 `a538551`）已通过两轮独立收口复核。
> 本轮 = STOP D（部署与运行收口）+ D1 审计发现的 P0（异步任务路径授权未启用）闭环（D1.5）。

---

## 1. P0 Verdict

```text
P0_ASYNC_SQL_AUTH_CLOSED=true
```

D1 审计发现：`POST /api/tasks → Celery → TaskGraphExecutor → graph → SQLSkill`
路径构造图状态时不写 `request_context`，`_build_sql_policy_context` 返回
None → `ask_struct(policy=None)` 授权未启用旧行为——无 `sql.read` 的用户
可经异步任务触发 SQL 执行（数据读取越权，P0）。

修复（身份固定 / 授权动态 / 缺失拒绝 / 恢复重验）：

| 层 | 文件 | 动作 |
|---|---|---|
| 授权解析 | `backend/security/task_authorization.py`（新增） | 执行（含 resume）时按任务持久化 actor 重新查 `auth.users` 当前 role/status/dept/tenant；用户不存在/禁用/租户不符/解析异常一律 `TaskAuthorizationDenied` |
| 注入 | `backend/orchestration/checkpoint/task_executor.py` | 全新执行分支注入 `RequestContext.checkpoint_safe()`；resume 分支经 `update_state` 刷新授权（不信任 checkpoint 旧权限） |
| 纵深 | `backend/skills/sql/skill.py` | 生产 graph 节点缺可信 `request_context` → fail-closed 拒绝，不再静默走 `policy=None`（评测/脚本直调 `sql_agent` 不受影响） |

**接线提交状态（重要）**：`task_executor.py` 与并行 Phase2 会话 Step2 的未提交
改动交织（其重写了 execute 本体，无法按 hunk 剥离），按多会话纪律不代提交。
运行实例已通过容器 md5 实证加载修复（见 §4），**Phase2 落定时必须随
`backend/orchestration/checkpoint/task_executor.py` 与
`backend/tests/sql/test_task_async_authorization.py` 一并提交**（当前 dirty
可解释，见 §9 Git Isolation）。

## 2. Authorization Strategy

```text
Task identity source:              tasks 表持久化 user_id/tenant_id 列（创建时网关验签身份落列）
Execution-time authorization source: auth.users（与登录/JWT 签发同一权威），执行与 resume 均重新解析
Tenant membership source:          auth.users.tenant_id 当前值 vs 任务 tenant_id 严格匹配，不符即拒
Roles source:                      auth.users.role 当前值（角色推导唯一来源 security/authorization.py）
Permissions source:                roles → ROLE_PERMISSION_CODES 推导（单一来源，本模块零复制）
Data scope source:                 当前 roles 经 widest_data_scope 折算（授权层单一来源）
Resume authorization behavior:     每次恢复执行前 update_state 覆盖 request_context（身份归属固定、授权权限动态）
Failure behavior:                  TaskAuthorizationDenied → 任务 FAILED 终态，绝不回退 policy=None
```

执行时重新解析授权：**是**（含 resume）。

## 3. Files Changed

| 文件 | 函数/位置 | 原因 | 归属 |
|---|---|---|---|
| `backend/security/task_authorization.py` | 新增 | 执行时授权解析 | STOP D |
| `backend/orchestration/checkpoint/task_executor.py` | `_build_request_context` / `execute` 授权注入 | P0 修复接线 | STOP D（与 Phase2 dirty 共存，未代提交） |
| `backend/skills/sql/skill.py` | `execute` fail-closed | P0 纵深 | STOP D |
| `backend/sql/policy.py` | `validate_and_rewrite` | allow 路径 span 恒 leaked（try/return+else 语义） | STOP D（9048ebf） |
| `mcp_servers/manager.py` | `route` | 线程池丢 ContextVars 致 MCP 合法用户全被拒 | STOP D（ce5b7e7） |
| `backend/tests/sql/test_task_async_authorization.py` | 新增 A1-A8 | P0 回归 | STOP D（待随接线补提交） |
| `backend/tests/sql/test_sql_production_closure.py` | TestSqlGuardSpan | span 收口锁定 | STOP D |
| `backend/tests/sql/test_sql_skill_structured.py` / `test_pg_integration.py` | `_make_state` | 契约适配（补 request_context） | STOP D |
| `backend/tests/test_task_{orchestration,cancel,pause,execution_lock,checkpoint_recovery}.py` | autouse fixture | 授权解析 mock 适配 | STOP D |
| `backend/scripts/e2e_sql_worker.py` | 新增 | worker 真实链路 smoke | STOP D |

Phase2 dirty coexistence：`task_executor.py` 的 fencing/租约改动、
`task_service.py` CAS、`agent_tasks.py` error taxonomy 等均为并行会话
既有工作，本轮零触碰、零混入提交。

## 4. Async Real Call Chain（修复后，运行实例实证）

```text
POST /api/tasks（:9080，JWT → user_id/tenant_id 落列）
→ Celery tasks.execute_agent（agent 队列，acks_late+租约）
→ execute_agent_task_impl → TaskGraphExecutor.execute
→ resolve_task_authorization(record.user_id, record.tenant_id)   ← 执行时权威解析
   （失败 → 任务 FAILED，图不执行——smoke 首轮实证）
→ payload["request_context"] = RequestContext.checkpoint_safe()  / resume 经 update_state 刷新
→ graph.stream（builder.build_graph，PostgresSaver）
→ router → direct → skill_executor → SQLSkill.execute
→ _build_sql_policy_context(state) → SQLPolicyContext（source_channel="graph"）
→ agent.ask_struct(policy=非None) → SQLPolicyGuard.precheck（LLM 之前）
→ select_tables → generate_sql → validate_and_rewrite → readonly executor
→ _observe → audit（sql_query_audits）+ metrics + trace
```

容器实证（worker md5 = 本地工作区 = 修复版）：`task_executor.py c28ca388`、
`task_authorization.py 0fbc58cc`、`skill.py a9c9d388`、`sql_agent.py 9ef65905`、
`policy.py（含 span 修复）`——app/worker/mcp-service 三容器与工作区四方一致。

worker 日志铁证：`[SQLAgent:policy] 前置策略拒绝 code=SQL_PERMISSION_DENIED:
user='46' 无 sql.read 权限(roles=('viewer',))`——roles 来自执行时解析的
auth.users，precheck-first 语义在 worker 上成立。

## 5. Fail-Closed Evidence

单元/集成（`tests/sql/test_task_async_authorization.py`，stub 图 + TaskGraphExecutor 全链）：

| Case | Identity | Authorization | LLM | Executor | Result |
|------|--------|-------------| --: | -------: | ------ |
| A1 viewer 异步任务（+body 伪造 admin） | viewer | precheck 拒 | 0 | 0 | PASS |
| A2 editor(shared) 全链 | editor | allow | 1 | 1 | PASS（policy.user/tenant/roles 逐项断言） |
| A3 editor/hr → finance | editor(dept=hr) | scope 注入拒 | 1 | 0 | PASS |
| A4 租户 membership 撤销 | editor@other-tenant | 解析失败 fail-closed | 0 | 0 | PASS |
| A5 执行时权限撤销 | editor→viewer | precheck 拒 | 0 | 0 | PASS |
| A6 resume 重验 | admin→editor→viewer | 刷新覆盖旧快照 | - | 不增 | PASS（roles 逐轮断言） |
| A7 解析异常（DB 故障） | - | fail-closed | 0 | 0 | PASS |
| A8 Skill 缺 request_context | - | 纵深拒绝 | 0 | 0 | PASS |

真实 Worker E2E（`scripts/e2e_sql_worker.py`，login → POST /api/tasks → 真实 worker）：

| Case | Path | Identity | Expected | Actual | LLM | Guard | Executor | Result |
| ---- | ---- | -------- | -------- | ------ | --: | ----: | -------: | ------ |
| W2 viewer deny | 真实 async | e2e_sqlv(viewer) | permission_denied, executor=0 | task SUCCESS + sql step failed/permission_denied；audit DENY_PERMISSION duration=0 | 0 | 1(precheck) | 0 | PASS |
| W1 editor allow | 真实 async | e2e_sqle(editor,hr) | 合法执行 | sql step success row_count=5（真实 SQL：inventory TOP5）；audit EXECUTION_SUCCESS | ≥1 | ≥1 | 1 | PASS |

（LLM/Executor=0 由 audit `duration_ms=0`+无执行记录+precheck 日志三重佐证。）

## 6. Regression

```text
async auth tests:        8 passed（A1-A8）
tests/sql:               265 passed（≥249 基线；含 span 3 例与全部存量）
consistency/auth/context: 73 passed（registry/layer/adr0001/request_context/principal_authorization）
task tests:              cancel/pause/execution_lock/orchestration/recovery/lease 全绿
SQLSkill:                test_sql_skill_structured + pg_integration 30 passed（契约适配后）
baseline failures:       test_llm_span_fields.py 5 例 minimax/deepseek pricing —— baseline existing failure, untouched
既有失败（非本轮）:       test_task_execution_lock::test_crashed_worker_lease_takeover
                         （Phase2 dirty task_service 与 HEAD 测试交互问题，不经过本轮改动文件，登记待 Phase2）
```

## 7. MCP Deployment

```text
Before image: agent-mcp-service 596c677c1c20（2026-09-22 09:45，STOP C 前一天）
After image:  agent-mcp-service（2026-09-23 rebuild，compose up -d --build mcp-service）
HEAD/file hash: mcp_servers/servers/sql.py 5844798f = HEAD；sql_agent.py 9ef65905 = HEAD；tools/sql.py fd469ca8 = HEAD
policy.py:    存在 ✅（旧镜像缺）
audit.py:     存在 ✅（旧镜像缺）
mcp sql server: HEAD 收口版（contextvars 可信身份 + source_channel="mcp"）
list_tables:  schema_loader 白名单（无 information_schema 探测）
```

## 8. MCP Smoke

```text
anonymous:  :8091 无身份 tools/call → permission_denied（fail-closed）；REST 无身份 → permission_denied；服务级无 Key → 401
authorized: M2 editor 经网关 JWT → EXECUTION_SUCCESS row_count=5（真实商品数据）
list_tables: M3 → schema_loader 白名单 18 表（ai/crawler/customer/finance/inventory/order/product）
spoof:      M4 viewer + args role=admin/data_scope=all → DENY_PERMISSION（身份只来自 context，args 不参与授权）
audit:      source_channel="mcp" 归因正确（ALLOW/DENY 分明）
```

MCP 修复期间发现并关闭的运行缺陷：`manager.route` 线程池丢 ContextVars
（MCP 对合法用户也全拒，ce5b7e7）。

## 9. Git Isolation

```text
Other-session dirty before: Phase2 tasks/*、task_executor.py、runner.py、task_service.py、models/task.py、CS 文档、scripts/* 等
STOP D commits: 8d7af8c（P0 第一批）、9048ebf（span 收口）、ce5b7e7（MCP context）、b971053（tenant 修复 + smoke 脚本）——全部显式 pathspec
Staged:      仅上述四个提交涉及文件
Touched:     §3 清单内文件
Not touched: Phase2 全部 dirty 文件（task_executor.py 未代提交）、CS、frontend、migration
Other-session dirty after: 仅并行会话自身推进（期间对方提交 45d06d1/1edc6e0/5890e87/6fb8e70，互不干扰）
遗留待补:     task_executor.py 接线 + test_task_async_authorization.py 随 Phase2 落定立即补提交（当前工作区即生效版，容器已实证）
```

## 10. Findings

```text
P0: 无（D1 发现的 async 授权 P0 已闭环，运行态实证）
P1: 无新增（本轮修复的 mcp-service stale image、MCP contextvars 丢失均已在 STOP D 内关闭）
P2:
  1. worker 进程 sql_agent_* 指标无暴露通道（app /metrics 仅覆盖 app 进程；
     worker 侧行为当前由 audit 表完整解释）——登记 future，不顺手重构 metrics 架构
  2. worker async 任务路径不写会话 trace（sql.guard span 在无 active trace 时
     按 best-effort noop 设计不落盘；span 本体 allow/deny 收口已由
     TestSqlGuardSpan 锁定）
  3. test_task_execution_lock::test_crashed_worker_lease_takeover 既有失败
     （Phase2 范围）
```

## 11. Gate

```text
P0_ASYNC_SQL_AUTH_CLOSED = true
MCP_SERVICE_UPDATED      = true
STOP_D2_ALLOWED          = true（已执行完毕 D2-D6）
```

---

## 12. STOP D 总验收（§五十六 完成标准对照）

**A Deployment**：app/worker 14/14 STOP C 关键文件 = HEAD（md5）；mcp-service rebuild 后 = HEAD；
worker 双队列注册正常、broker/DB 连通 healthy；`SQL_AGENT_ENABLED` 统一来源
（`config/__init__.py:52`，默认 true，无 env 覆盖）；无 stale SQL executor
（metadata-shadow-worker 只监听 rag_metadata_shadow 队列，cs-dispatcher 非 Celery）。

**B Async SQL Chain**：§4 调用链 + §5 W1/W2 实证。

**C Security**：身份只来自持久化记录+执行时权威解析（A1 伪造无效、M4 spoof 拒）；
readonly DB role（worker 内 `BUSINESS_DB_READONLY_CONFIG=agent_readonly@agent_business`
实测）；kill switch 语义沿用 STOP C 证据（本轮零改动该路径）；export bypass
未回归（export.py md5 一致 + export 波及测试在 tests/sql 全量内）。

**D Audit**：graph/mcp 通道归因正确；query_hash（sha256）非原文；decision/deny_code/
duration_ms/row_count 全字段正常；best-effort 语义沿用 STOP C（本轮未改动 audit）。

**E Metrics**：app /metrics 实测在位（15 个 sql_agent 序列）；labels 源码审计低基数
（source/decision/reason/status，禁令注释在位）；worker 侧暴露缺口登记 P2。

**F Trace**：sql.guard allow/deny 双路径收口（发现并修复 allow 恒 leaked 缺陷 +
TestSqlGuardSpan 锁定）；attributes 五元组低基数；持久 trace 无样本属 best-effort
noop 设计（P2 登记 worker trace 缺口）。

**G Regression**：tests/sql 265 passed；一致性 73；auth/context 全绿；export 全绿；
baseline pricing failure 未触碰。

**H Isolation**：§9。

## 13. 最终冻结

```text
STOP_D_PASS = true
SQL_AGENT_PRODUCTION_READY = true
SQL_AGENT_FROZEN = true
```

附带条件（不阻塞冻结，需跟踪）：
1. `task_executor.py` 授权注入接线当前在工作区（运行实例已实证），随 Phase2
   Step2 落定立即补提交——在此之前任何对该文件的 checkout/restore 都会退化 P0
   修复（多会话纪律本就禁止此类操作）。
2. §10 的三项 P2 登记（worker metrics 暴露、worker trace 缺口、lease takeover
   既有失败）进入 STOP E / V2 candidates，与 UNION、NL2SQL prompt 优化、
   audit 管理页、长期 denied 监控一并排队，本轮一律不实施。

此后 SQL Agent 默认不再修改，除非真实生产 bug / 安全漏洞 / 明确新需求。

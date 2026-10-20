# 常用命令

以下命令以当前仓库根目录执行；仅在本地或已获授权的环境运行会停止、重建服务或访问真实 Provider 的命令。服务定义以 `docker-compose.yml` 和脚本为准。

## 本地服务

`devctl.bat` 是本地开发服务入口：`backend` 对应 Compose 的 `app`，`admin`、`cs`、`web` 分别启动对应前端。

~~~powershell
.\devctl.bat status
.\devctl.bat start backend
.\devctl.bat start web
.\devctl.bat stop web /y
.\devctl.bat restart all /y
.\devctl.bat rebuild /y
~~~

`rebuild` 会重建后端服务；日常查看状态和日志优先使用下面的 Compose 命令。共享环境中不要擅自停止或重建服务。

## Docker Compose 与排障

~~~powershell
docker compose ps
docker compose up -d apisix app
docker compose stop app
docker compose logs --tail=100 app
docker compose logs -f app agent-worker cs-dispatcher
docker compose --profile observability up -d prometheus grafana
~~~

停止或重建前先确认服务是否被其他会话使用。不要使用 `down -v` 清理共享数据卷。观测栈的启停配置见根目录 Compose 文件中的 `observability` profile。

### 数据库连接耗尽排障（2026-10-21 事故）

症状：登录/查询报 500，后端日志见 `asyncpg.exceptions.TooManyConnectionsError`。

~~~bash
# 连接分布（按 application_name 归因到组件；连不上说明保留槽也满，先重启占用大户）
docker exec agent-postgres-1 psql -U postgres -d agent_memory -c   "SELECT application_name, client_addr, count(*) FROM pg_stat_activity WHERE backend_type='client backend' GROUP BY 1,2 ORDER BY 3 DESC;"
~~~

治理层（本分支起生效）：Celery 任务一律经 `run_on_worker_loop`（常驻 loop，禁 `asyncio.run`）；
memory 异步引擎带 `application_name=agent-memory-async`；PG 侧 `idle_session_timeout=10min`、
`idle_in_transaction_session_timeout=5min` 兜底；PgBouncer 容器已进 compose（默认空转），
`.env` 设 `PG_VIA_PGBOUNCER=true` + rebuild 后池化引擎改经 PgBouncer（transaction 模式），
会话级锁路径（checkpointer 抢主 / travel 恢复锁）始终直连不受影响。

## 后端与前端检查

后端局部测试从仓库根目录执行，并指定受影响测试路径：

~~~powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/<受影响路径> -q --no-cov
~~~

本地单独启动后端时：

~~~powershell
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.server:app --reload --host 127.0.0.1 --port 8000
~~~

前端在受影响的 `frontend/`、`frontend-admin/` 或 `frontend-cs/` 目录执行：

~~~powershell
npm test -- <测试文件>
npx tsc --noEmit
~~~

前端测试和类型检查按改动目录选择；`frontend-mp/` 使用自己的 Taro 构建脚本，不套用上述 Vitest 命令。

## RAG 与 SQL 评测

先用离线冒烟检查验证数据和规则：

~~~powershell
.\.venv\Scripts\python.exe -m backend.evaluation rag --smoke --no-ragas
.\.venv\Scripts\python.exe -m backend.evaluation sql --smoke
~~~

真实 LLM、Provider 或 SQL 执行需显式使用 `--live`，会访问外部服务或测试数据；仅在已配置且隔离的环境运行。评测选项以 `backend/evaluation/cli.py` 为准。

## 日志、Trace 与运行契约

- Compose 服务状态与日志使用上面的 `docker compose ps` 和 `docker compose logs`。
- Trace / Span 字段见 [Trace 模型](../observability/trace-model.md)；主图、SSE 和 Checkpoint 入口见 [AI Runtime](../architecture/ai-runtime.md)。
- 测试范围遵循唯一的[测试策略](../development/testing-guide.md)，不默认运行全量回归。

## 依赖与缓存检查

~~~powershell
# 前端依赖已存在时先检查，不运行 npm ci 重装
Set-Location frontend
npm ls --depth=0
npm config get cache

# Python 依赖与 Playwright 版本、浏览器清单（均不安装）
Set-Location ..
.\.venv\Scripts\python.exe -m pip cache dir
.\.venv\Scripts\python.exe -m pip cache info
.\.venv\Scripts\python.exe -m playwright --version
.\.venv\Scripts\python.exe -m playwright install --list

# 检查本机镜像与 BuildKit 缓存；构建前确认所需基础镜像已经存在
rg -n '^FROM ' Dockerfile frontend/Dockerfile frontend-cs/Dockerfile frontend-admin/Dockerfile
docker compose config --images
docker image ls
docker buildx ls
docker buildx du
docker compose build --progress=plain app
~~~

Playwright 的 Windows 默认浏览器目录是 `%LOCALAPPDATA%\ms-playwright`；Linux 使用各自的 Linux 缓存目录，不能把 Windows 浏览器缓存挂给 Linux。前端锁文件和后端依赖未变化时，继续使用已有 `node_modules`、虚拟环境和 BuildKit 层；只有确认需要下载浏览器或缺失基础镜像时才请求授权。BuildKit 构建日志中的 `CACHED` 可用于确认层缓存命中。简单前端修改先运行 TypeScript 与 Vitest；浏览器交互风险确实存在时再运行 Playwright，缺少所需浏览器则报告 `BLOCKED`。

## Tool / Provider 失败定向诊断

先根据 trace_id、capability/tool_name、provider、status、error_code、retry_count、fallback_used 和业务终态还原故障；不得只看 HTTP 200。配置阈值与精确指标名称到当前 `backend/core/tool_runtime/`、`backend/observability/` 查；不要照抄历史报告中的默认值冒充当前线上配置。

~~~powershell
# 从仓库根目录执行：执行层与旅游域已有定向测试
.\.venv\Scripts\python.exe -m pytest backend/tests/tool_runtime/test_governance.py -q --no-cov
.\.venv\Scripts\python.exe -m pytest backend/tests/travel/test_tool_failure_policy.py -q --no-cov
.\.venv\Scripts\python.exe -m pytest backend/tests/skills/test_tool_contract_boundary.py -q --no-cov
~~~

上面是**可选验证命令，不代表已执行**；只运行与本次变更相关的用例。副作用状态未知、账本不可用或客服确认记录卡住时，**先读** [幂等手册](idempotency-runbook.md) 再处理，不重复触发支付、退款或下单。故障决策参见 [统一失败规范](../architecture/failure-degradation.md)。

## 故障恢复、备份与演练标准

这里只提供安全检查入口和 runbook 导航，不复制未经测试的破坏性恢复命令。更完整的数据完整性见 [生命周期规范](../governance/data-lifecycle.md)，任务/Lease/重试见 [异步任务治理](../architecture/async-task-governance.md)，副作用未知见 [幂等手册](idempotency-runbook.md)。

| 故障 | 首先只读确认 | 后续恢复前的门禁 |
| --- | --- | --- |
| API / APISIX 登录 401/403 | 网关/后端健康、可信身份模式、当前请求 Trace | 不禁用认证守卫以“恢复可用” |
| PostgreSQL 断线或迁移失败 | 目标 DB、连接池、schema_migrations、近期备份状态 | 只在隔离库试恢复；禁 `init_db.py --reset` |
| Redis / Celery 积压 | Worker、QueueRouter 路由、pending/lease/defer、消息年龄 | 不能直接删除队列或重复投递未知副作用 |
| Tool/MCP/Provider 超时 | ToolStatus、业务结果、用户硬约束和熔断状态 | 读故障可合法降级，写 UNKNOWN 先查 Ledger |
| LLM 额度/供应商故障 | DB 模型角色绑定、凭据探测、预算预占、最近单价快照 | 不偷偷改 model 或绕预算；避免敏感数据发到未授权 Provider |
| RAG 不能检索 | 文档审核状态、active_generation、向量/BM25 指针及权限范围 | 不能把 pending_review 当 active；不能删除旧版恢复证据 |
| Grafana 无数据/告警未发 | Prometheus target、/metrics、PromQL、规则状态 | No Data ≠ 0，Webhook 未配置则不宣称通知已通过 |

**演练证据**：触发/故障时间、影响租户与操作范围、业务状态、错误/Trace ID、只读诊断、恢复决策、备份版本、回滚或人工裁决、数据一致性和最终负责人。恢复脚本运行前核对容器名称、数据库目标、共享 Worktree/Volume 及备份落点；不使用 `down -v` 和强制清理 Worktree 来“修好”系统。

### DR-01～06 验收

1. 常见单点故障在模拟环境能被发现，错误码/Trace/指标可定位。
2. Redis/Celery Worker 重启不会造成任务状态幻觉和重复副作用。
3. 数据备份可以在隔离环境真实恢复并检查依赖索引/账本。
4. 提供每类故障的回滚、阻断与恢复决策；不可执行时标 BLOCKED。
5. 恢复后重新执行授权/失败路径和核心业务终态的定向回归。
6. 重大事故有时间线与后续防复发测试，不把日志清空当解决方案。

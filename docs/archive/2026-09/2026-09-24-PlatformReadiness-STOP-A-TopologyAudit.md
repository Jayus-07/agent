# Platform Readiness STOP A — Production Topology & Deployment Audit（拓扑与部署审计）

> 日期：2026-09-24 ｜ 只读审计，禁大改
> 基准：工作区 @ 2c04d3d；运行栈 23 个 agent-* 容器（app 当日 07:10 重建、db-migrate 03:48 镜像刚跑完 Exited(0)）
> 方法：3 路并行代码/配置审计（compose+Dockerfile+启动链 / 入口矩阵 / 配置-Secret-镜像追踪）+ 迁移三层实况核验（git/镜像/DB 逐层取物证）

---

## 1. Deployment Topology（A1）

单一网络 `agent-net`；compose 默认 profile 23 服务 + observability profile。**无 MinIO/对象存储**（compose/Dockerfile 全文零命中；存储=pgvector + app_data 卷）。

| service | 镜像(构建时刻) | 端口(宿主) | healthcheck | depends_on | restart |
|---|---|---|---|---|---|
| apisix | apache/apisix:3.13.0-debian | 127.0.0.1:9080 | **无** | **无** | unless-stopped |
| app | build:.（**07:10**） | 127.0.0.1:8000 | curl /health 30s/3次/start 90s | **db-migrate completed** + pg/redis healthy + rag started | unless-stopped, grace 700s |
| rag-service | build:.（03:48） | 127.0.0.1:8090 | /healthz（live）+/readyz（ready）二分 | **无 depends_on** | unless-stopped |
| mcp-service | build:. | 127.0.0.1:8091 | /health | db-migrate + rag started | unless-stopped |
| agent/rag-index/report/maintenance/metadata-shadow worker | build:.（02:19~05:40，**互不相同**） | 无（指标 :9809 网络内） | celery inspect ping | db-migrate + pg/redis healthy（前三个 +rag started） | unless-stopped |
| beat | build:.（05:40） | 无 | /proc/1/cmdline 含 beat | db-migrate + pg/redis | **未声明（默认 no）** |
| cs-dispatcher ×2 | build:. | 无 | cmdline 检查 | db-migrate + pg/redis | unless-stopped |
| db-migrate | build:.（**03:48**） | 无 | 无（one-shot） | postgres healthy | **restart:"no"** |
| postgres | pgvector/pgvector:pg16 | 127.0.0.1:**5433** | pg_isready agent_memory | 无 | unless-stopped |
| redis / redis-broker | redis:7-alpine | 6379 / 6380 | ping（带/无密码双试） | 无 | unless-stopped（broker=noeviction+AOF） |
| business-mock | build:. | 8081 | /health | 无 | unless-stopped |
| pg-backup | pgvector:pg16 | 无 | 无 | db-migrate + postgres | unless-stopped |
| prometheus/grafana/alertmanager/exporters ×4 | 官方镜像（**部分 :latest**） | 9090/3001/9093 | 无 | prometheus started 链 | unless-stopped（observability profile） |
| oa-auth-* ×5 | 旧 Java 栈 | — | — | — | **Exited(137) 8 天**（与 py 平台无依赖） |

前端三件套不在 compose 内：宿主机 `next dev` 3100/3200/3300，BFF (`app/api/[...path]/route.ts`) 服务端注入 X-API-Key 转发 :9080。

## 2. Endpoint Matrix（A2，全量前缀；网关兜底 /api/* 挂 gateway-auth + 600/min 限流 + StripPrefix）

完整矩阵见附录数据源（router.py:64-116 include 清单 + apisix.yaml 路由表 + middleware/auth.py 豁免集）。要点：

- **网关层**：`/api/auth/*`、`/api/sys/users/register`、`/ws/cs/*`、`/health /metrics / /openapi.json` **无 gateway-auth**（auth 靠应用层；ws 靠一次性 ticket）；其余 `/api/*` 全部 gateway-auth（enforce，九伪造头剥离）。
- **应用层底线**：除豁免前缀（/health /docs /redoc /openapi.json /metrics /internal /auth /sys /）外一律 X-API-Key（fail-closed）。
- **Tenant 强消费点**：/auth 登录四端点（缺可信租户头 401）、/cs/*（会话/工单/坐席）、/budgets/me、/idempotency、/competitor 写、/rag ask。
- **应用层无身份守卫前缀**（仅 API-Key+网关 JWT 兜底）：`/report /data /assets /pipeline /agents /capabilities /workflows /inventory /demo /reports /schedules /evaluation /selection /map`、`/llm`（除 switch）、`/observability`（除 3 个 admin 端点）、`/chat/messages`、`/chat/abort`、`/rag/keywords GET`——登记 P1-16（`/chat/messages|abort` 涉及按 session_id 读他人历史的水平越权面，STOP C 实测）。
- `/internal/ai` 不经网关，应用层 `require_internal_token` fail-closed。

## 3. 启动依赖与 fail-open/closed（A3）

- **决定性 fail-closed 在编排层**：PG down→pg_isready 不过→db-migrate 不跑或 rc=2→`depends_on: service_completed_successfully` 不满足→app/5 worker/beat/mcp/cs-dispatcher/pg-backup **容器从不创建、端口不开放**（Domain Runtime E8 已实测印证）。
- **代码层全 fail-open**：server.py 18 个 startup handler 全部 try/except+后台重试（registry 15s 轮询、prompt seed、checkpointer 预热、kafka/gateway-log ingest 均不阻塞）；`docker restart app` 绕过 depends_on 时 PG down 仍会绑 8000（此时靠请求期 MemoryDatabaseUnavailable handler 兜底）。Redis 无启动探测，各后台线程退避重试。
- **migration 未完成时 app 不会启动**（db-migrate 门）——但该门的有效性依赖镜像同源，见 §5。
- beat 无 restart 策略（宿主/daemon 重启后不自动拉起，孤儿风险已知）；rag-service 无 depends_on（靠自身重试，App 侧用 /healthz|readyz 二分 fail-fast）。

## 4. Migration 架构与三层实况（A4，物证）

机制：`db-migrate` one-shot 跑 `scripts/init_db.py`（唯一入口，alembic 已退役）；按 `MIGRATION_TARGETS` 分派 agent_memory/agent_business 两库；**未登记文件 fail-fast rc=2**；已应用+checksum 一致 skip；「已存在」类错误降级逐语句补齐；状态表 `schema_migrations(filename PK, checksum, status, applied_at)`。

**三层实况（2026-09-24 实测取物）**：

| 层 | 状态 |
|---|---|
| git | 048_memory_scope_and_versioning（tracked）、049_task_pending_recovery（**untracked**，Phase3 会话所有） |
| 镜像 | app 镜像(07:10) **含 048+049 SQL**；db-migrate 镜像(03:48) **只到 047**；两者 init_db.py 都只登记到 047 |
| DB | schema_migrations 止步 047×2；但 048 的列（memory_key/superseded_by/tenant_id）**已被 Memory 会话手工落到实库** |

结论：**§2.1 所述「镜像漂移静默跳过」正在发生**（本次 Exited(0) 即旧镜像看不到 048）——但因 048 对象已被手工落库，运行时无实害；真正的一触即发点是：**从当前工作区重建 db-migrate → 目录含未登记 048/049 → rc=2 → 整栈拒绝启动**。修复归 STOP B（登记 048/049 + preflight + build identity）。

## 5. Image/Commit 可追踪性（A8/A9）

- **运行容器无法知道自己的 commit**：Dockerfile 无 ARG/LABEL/版本文件注入；无启动打印；/health 无版本字段（仅 FastAPI version="2.0.0" 静态值）。**A8 判定 P1 成立**。
- **镜像版本漂移正在运行**：app=07:10 / db-migrate=03:48 / beat,maintenance=05:40 / agent-worker=02:22 / report,rag-index=02:19——同 compose `build: .` 无 tag 无 digest，按 service 分别构建，构建时刻不同即内容漂移。`dev-svc.bat start_backend` 日常只 `--build app`，五 worker/beat/db-migrate 沿用旧镜像——**mixed-version 是日常路径而非异常**（A9 P1 成立）。共享契约风险面：tasks 表演进列（双方 ensure_schema 幂等，旧进程不识新列=兼容）、Celery 任务名/队列注册表（新任务名+旧 beat=静默停摆，历史事故在案）、checkpoint 序列化（travel checkpointer 开启侧）、LLM 角色快照（旧 worker fail-open 用代码 default → 同任务双进程解析不同模型）、Redis 控制面键语义。

## 6. Configuration Source Matrix（A5，摘要）

DB 连接=env（PGHOST/PGPORT/MEMORY_PGDATABASE/BUSINESS_PGDATABASE；PGDATABASE 不参与）；Redis=env（REDIS_ENABLED 代码 default **false**）；**LLM role→provider=DB 六表+15s 快照轮询**（凭据 DB Fernet 加密，绝不回落 env）；Context Budget=env（代码 default 齐全）；APISIX=静态 YAML（改路由须重建容器）；Prometheus=file（**metadata-shadow-worker 缺席 task-workers 抓取目标**——P2）；feature flags 三方对照发现 8 处 env/compose/代码 default 冲突，典型：CS_ENABLED/TRAVEL_ENABLED compose `:-true` vs 代码 false（宿主机裸跑与容器行为漂移）、METADATA_CASCADE_ENABLED compose 默认全量、REDIS_ENABLED 宿主机静默关、LLM_CONTEXT_LENGTH 4096 vs 8192、OLLAMA_HOST 注入的是死变量（代码消费 OLLAMA_BASE_URL）、obs-postgres 端口 5433 未绑回环（与主库冲突+局域网暴露）。

## 7. Secret Matrix（A6）

API_KEY/PGPASSWORD/REDIS_PASSWORD/JWT_SECRET/SECRETS_ENCRYPTION_KEY/provider keys 全部在 .env（**git 未跟踪**，.gitignore 生效）；provider 密钥 DB Fernet 加密无 env 回退；metrics/trace/日志的密钥泄漏面已有防线（metrics label 禁令、redaction PII 模式、E15 硬门通过）。**在册瑕疵**：PG_READONLY_PASSWORD 代码硬编码 dev 缺省 `agent_readonly_dev`（tracked，生产漏配 fatal 兜底）、Grafana admin/admin、obs-postgres 弱缺省口令在 tracked 文件、postgres/redis exporter DATA_SOURCE_NAME 明文口令（docker inspect 可见，单机 dev 语义）。判定：无 P0；P2 登记。

## 8. Health / Readiness（A7）

app `/health` **恒 200**（status 硬编码 ok），只带 rag/conversation_context/travel_providers 软信息；**migration 缺失不影响 200**（无 schema 版本检查）；无 readiness/liveness 区分（compose healthcheck 即用它）。rag-service 有正确的 /healthz|/readyz 二分。**风险记录**：migration 漂移时 app 健康（本次实况正是如此）——STOP B 以「preflight 门 + /health 暴露 migrations 状态」收口。

## 9. 缺陷台账

**P0**（本轮内闭环）：P0-2 迁移三层漂移活体+重建炸弹（048/049 未登记；§4）。
**P1**：P1-16 运行容器无 commit 可追踪（A8）；P1-17 mixed-version 为日常路径（单服务 rebuild 惯例；A9）；P1-18 `/chat/messages|abort` 等无身份守卫前缀的水平越权面（STOP C 实测后定级）。
**P2**：P2-12 beat 无 restart 策略；P2-13 metadata-shadow-worker 缺席 Prometheus 抓取；P2-14 config 三方 default 冲突 8 处（compose `:-true` 惯例）；P2-15 obs-postgres 5433 未绑回环+弱缺省口令；P2-16 rag-service 无 depends_on；P2-17 exporters :latest 镜像。
**KEEP**：db-migrate fail-fast 门（未登记文件 rc=2 阻断下游）设计正确且有效；server.py 全 fail-open + 编排层 fail-closed 的双层语义；无 MinIO（存储=pgvector+卷，D9 演练改测 app_data 卷语义）；/healthz|/readyz 二分（rag-service）。

---

```text
STOP_A_PASS    = true   （审计完成；P0-2 为部署一致性问题，非架构缺陷，修复方案已明确归 STOP B）
STOP_B_ALLOWED = true
```

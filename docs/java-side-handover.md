# Java 侧交付清单（项目分离后 Enterprise_OA 需自行完成的工程）

> 2026-09-15 项目拆分生效。py 项目（本仓库）与 Java 项目从此独立部署。
> **唯一保留的联系**：Java 客服系统调用 py agent 获取回复结果（见 §4 接口契约）。
> Java 源码备份：`agent/.workbuddy/java-legacy-backup/`（含未提交修改），git 历史亦可追溯（tag `apisix-gateway-b4-final` 之前）。

## 1. 已从 py 项目移除、归 Java 侧管理的内容

| 内容 | 原位置（本仓） | Java 侧去向 |
|---|---|---|
| api-gateway（SCG）源码 | `api-gateway/` | Java 仓库 + Java compose |
| business-service 源码 | `business-service/` | Java 仓库 + Java compose |
| Java 启停脚本 | `start_java/stop_java/restart_java/build_java.bat` | Java 项目根目录 |
| SCG 容器 | agent compose `api-gateway` 服务 | Java compose |
| business-service 容器 | agent compose `business-service` 服务 | Java compose |

注意：备份目录含拆分当日**未提交**的修改（pom/config/application.yml 等），
迁入 Java 仓库时请以此为准 diff。

## 2. Java 侧必须自建的依赖（原来全部寄生在 py 侧）

| 依赖 | 原状 | 拆分后 |
|---|---|---|
| **数据库** | 直连 py postgres：`agent_memory`（customer_service schema，MEM_DB_URL）+ `agent_business`（BIZ_DB 只读） | Java 自建 PostgreSQL/库并**迁移数据**；schema DDL 可从 py 库导出（`pg_dump -n customer_service` + agent_business 相关表） |
| **Kafka** | 直连 py `agent-kafka`（KAFKA_BOOTSTRAP_SERVERS=kafka:9092） | Java 自建 Kafka 集群（compose 已可参考：apache/kafka:3.7.0 单机配置） |
| **Redis（黑名单旧库）** | oa-auth-redis:16379 | 已随 oa-auth 栈归 Java，无需动（py 已切自建 auth，不再读它） |

## 3. 认证体系（重大变化，Java 侧需知）

- py 已**自建用户体系**（`backend/security/local_jwt.py` + `routes/auth_local.py` + `008_local_auth.sql`）：
  - issuer = `agent-platform`（HS512，py 签发验签闭环）
  - 用户表 `auth.users`（agent_memory 库），存量 Java 用户**未迁移**——py 侧用户需重新注册
  - 黑名单：py 写 agent-redis（logout 时），APISIX 插件只读
- Java 侧 business-service 若仍要**校验 py 签发的 JWT**（识别调用方用户）：
  需要拿到 `JWT_SECRET`（py `.env`）与 issuer=agent-platform 自行验签；或改走
  py 提供的用户信息接口（建议后者，避免密钥外泄）。
- oa-auth（auth-service/system-service）不再被 py 任何链路引用；Java 侧可继续
  自用于自己的前端/管理面（容器停于 2026-09-15 21:00，按需 start）。

## 4. 唯一保留的联系：Java 客服系统 ↔ py agent

| 方向 | 通道 | 说明 |
|---|---|---|
| Java → py（同步工具调用） | `POST http://<py-host>:8000/internal/ai/call` + `X-Internal-Token` | AI 工具网关，契约不变（`internal_ai.py`） |
| Java → py（客服对话） | `POST :8000/api/chat/stream`（X-API-Key + Bearer 或按需新签服务凭据） | 现走 APISIX:9080，生产建议直连 8000 + 内网收口 |
| py → Java（WhatsApp 发送/工单等） | **待 Java 侧重建通道** | 原 Kafka `ai.reply.events` 随 Kafka 分家失效；短期方案：py 侧 cs_admin 保留 local 模式，WhatsApp 发送改由 Java 提供 HTTP 回调接口后割接 |

## 5. 网络与部署要点

- py 容器全部在 `agent_agent-net`；Java 容器在 `oa-auth_default` 及未来自己的网络。
  跨项目调用走**宿主机映射端口**（py: 8000 仅 127.0.0.1，生产需加内网监听；Java: 8081/8006/8002/16379）。
- py 侧 APISIX(9080) 为 py 唯一入口；Java 的 SCG 归 Java 自己管理，与 APISIX 无任何关联。
- 本机 Docker 曾多次全量容器 137（引擎/WSL 层），两侧部署后请配置 `.wslconfig` 内存上限
  并检查 Docker Desktop 健康状态。

## 6. 遗留

- [ ] Java 侧数据迁移（customer_service + agent_business 相关表）
- [ ] Java 侧 Kafka 自建 + WhatsApp 闭环割接（原 topic 消费者迁移）
- [ ] 存量 Java 用户是否导入 py `auth.users`（需重置密码，哈希算法不兼容）
- [ ] `/api/channels/*`（business-service 的 webhook 入口）在 py 侧无对应——Java 自建接入时自行处理

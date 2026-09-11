# 微服务架构总览（Microservices Architecture Overview）

> 2026-09 架构升级：单体 FastAPI 拆分为「Java 业务系统 + Python AI 系统」，新增 API Gateway、Kafka 事件总线与 WhatsApp 渠道接入。

## 1. 架构图

```
用户 ── Web(Next.js) / App / WhatsApp
                │
        API Gateway（Spring Cloud Gateway, :8080）
        ├─ /api/cs/conversations/**、/api/channels/** ──→ business-service（Java 17 + Spring Boot 3, :8081）
        └─ /api/chat|rag|observability|... 其余 ──→ app / ai-service（Python FastAPI, :8000）
                │                                        │
        Kafka（KRaft 单节点, :9092 容器内 / :9094 宿主机）←── 事件双向流转 ──→│
                │
        PostgreSQL：agent_memory（customer_service schema）+ agent_business（订单，只读）
```

## 2. 职责边界

### business-service（Java，`business-service/`）
- **会话管理 API**：`GET /cs/conversations`（keyset 分页列表）、`GET /cs/conversations/{id}`（详情）——响应结构与原 cs_admin.py 完全一致，前端 `cs.ts` 无需改动
- **三个状态机**：会话双维度（conversation_status × handling_mode）、转人工 5 态、确认 8 态（直译自 `backend/customer_service/state_machine.py` / `handoff.py` / `confirmation.py`）
- **统一状态转换**：`POST /internal/state-transitions`、`GET /internal/state-snapshot`（对齐 Python `StateTransitionService`）
- **审计 / 风控 / 安全守卫**：audit_logs 落库、四级风险评估、InputGuard/OutputGuard/PermissionChecker（正则规则一比一移植）
- **订单查询**：只读业务库 agent_business（`agent_readonly` 账号，数据库层强制只读）
- **WhatsApp 渠道**：`/channels/whatsapp/webhook`（GET 验证 + POST 收消息 + HMAC 签名校验），消息归一化后落库 + 发 Kafka；回复经 Graph API v20.0 发送（无 token 时仅收不发）
- **Kafka 事件发布**：见 §4

### ai-service（Python，`backend/`，即原 app）
- RAG 问答、embedding 路由（coarse/fine/domain/cs_router）
- LangGraph 编排（supervisor + experts + CS 子图）
- chat 全部端点（含 SSE 流式）、workflows、evaluation、observability
- **新增**：Kafka producer（`backend/infra/messaging/kafka.py`）发布会话状态/消息事件

## 3. API Gateway 路由（`api-gateway/src/main/resources/application.yml`）

| 路径 | 目标 | 说明 |
|---|---|---|
| `/api/cs/conversations/**` | business-service:8081 | StripPrefix=1 |
| `/api/channels/**` | business-service:8081 | WhatsApp webhook 等 |
| 其余 `/api/**` | ai-service:8000 | StripPrefix=1（前端 chat.ts 已硬编码 `/api` 前缀） |

- CORS 统一在网关处理；`X-API-Key` 原样透传，鉴权仍在下游服务
- `GATEWAY_USER_HEADER_ENABLED=true` 时注入 `X-User-Id`（对接 Python `TRUST_USER_HEADER` 机制）

## 4. Kafka 事件契约

| Topic | 生产者 | 事件 |
|---|---|---|
| `cs.conversation.events` | 双方 | conversation.state_changed / confirmation.state_changed |
| `cs.message.events` | 双方 | message.created |
| `cs.handoff.events` | 双方 | handoff.requested / handoff.state_changed |
| `cs.action.events` | 双方 | action.executed |
| `channel.whatsapp.inbound` | business-service | whatsapp.message.inbound |
| `ai.reply.events` | ai-service（预留） | AI 生成的渠道回复 |

事件 JSON 结构（双方一致）：

```json
{
  "event_id": "uuid",
  "event_type": "conversation.state_changed",
  "occurred_at": "2026-09-12T08:00:00Z",
  "source": "ai-service | business-service",
  "conversation_id": "...",
  "user_id": "...",
  "payload": { "...": "维度相关数据" }
}
```

key = conversation_id（保证同会话事件有序）。发布为 fire-and-forget：Kafka 不可用不阻塞主流程（Python 侧 `KAFKA_ENABLED` 默认 false，Redis 客户端同款降级模式）。

## 5. 部署与启动顺序

```bash
# 1. 配置环境变量（.env）：PGPASSWORD、PG_READONLY_PASSWORD 必填；
#    可选：WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_VERIFY_TOKEN / WHATSAPP_APP_SECRET
#         INTERNAL_API_TOKEN（Python ↔ Java 内部调用令牌）
# 2. 一键启动
docker compose up -d --build
#    启动顺序由 depends_on 保证：postgres → kafka → app / business-service → api-gateway

# 3. 验证
curl http://localhost:8080/actuator/health        # 网关
curl http://localhost:8081/health                 # 业务系统
curl http://localhost:8000/health                 # AI 系统
```

前端：`frontend/.env.local` 已将 `NEXT_PUBLIC_API_URL` 指向网关 `http://localhost:8080`；回滚时注释该行即退回 Next.js rewrite 代理模式。

本地开发（不跑容器）时 Kafka 宿主机端口为 `127.0.0.1:9094`（KAFKA_BOOTSTRAP_SERVERS 默认值）。

## 6. Cutover 路线（状态写入权切换）

当前为**过渡态**：Python AI 服务仍直写 customer_service 表 + 双发 Kafka 事件；Java 业务系统为管理面读源与渠道入口。

1. **阶段 A（现状）**：`CS_ADMIN_SOURCE=local`（默认），cs_admin 读本地 PG；Java 已就绪
2. **阶段 B（验证 Java）**：`CS_ADMIN_SOURCE=java`，cs_admin 列表/详情代理到 business-service；前端无感
3. **阶段 C（写权切换）**：Python 的 `state_transition.py` / `conversation_store.py` 改调 `POST /internal/state-transitions`、`POST /internal/messages`（`BUSINESS_SERVICE_URL` + `X-Internal-Token`），Python 停止直写
4. Java 侧按 `(conversation_id, 状态)` 幂等消费 Kafka 事件，双写窗口期数据安全

## 7. 新增组件速查

| 组件 | 位置 | 说明 |
|---|---|---|
| 业务系统 | `business-service/` | Maven 工程，`mvn spring-boot:run` 本地跑 |
| 网关 | `api-gateway/` | Spring Cloud Gateway（WebFlux） |
| Kafka 配置 | `backend/config/messaging.py` | topic 契约 + 业务系统地址 + cutover 开关 |
| Kafka 客户端 | `backend/infra/messaging/kafka.py` | 单例 + 降级 + fire-and-forget 发布 |
| 事件挂点 | `backend/customer_service/state_transition.py`、`conversation_store.py` | 状态变更/消息落库后发布 |

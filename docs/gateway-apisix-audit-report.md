# APISIX 迁移 · 实施前审计报告（B0）

> 日期：2026-09-15 10:34~10:45 · 执行：迁移负责人（只读扫描 + 基线脚本实跑）
> 结论：**未发现必须暂停的安全/兼容性问题**，但修正方案 v2.0 两处技术细节（见 R3/R4），并有一项归属待确认（R1）。
> 基线产物：`.workbuddy/baseline_fastapi_direct.json`（11 探针全绿，3 个重量级探针按设计跳过待显式开启）。

---

## 1. 实际流量路径（三条，网络边界收口已生效）

| 路径 | 链路 | 现状 |
|---|---|---|
| 业务 API | 浏览器 → Next.js(:3000) 同源 → rewrites 剥 `/api` → **FastAPI(8000) 直连** | SCG 不在业务链路上 |
| 登录/注册 | 浏览器 → Next.js 同源 → rewrites 保留 `/api/auth|sys` 前缀 → SCG(8080) → auth-service(8006)/system-service | Cookie 设在 Next 同源域 |
| 服务间 | business-service(容器网络) → FastAPI `/internal/ai/*`（X-Internal-Token） | 不经任何网关 |

端口实测（netstat）：8080 LISTENING(0.0.0.0，SCG 运行中)、8000 LISTENING(127.0.0.1)、16379 LISTENING(127.0.0.1，oa-auth-redis)、9080/9443 空闲（APISIX 无冲突）。`apisix/` 目录不存在，无历史残留配置。

## 2. 实际路由清单（权威来源：运行实例 /openapi.json 实测）

- **共 159 条 path**。**FastAPI 无 `/api` 前缀**——Next rewrites 与 SCG 的 `StripPrefix=1` 都在剥前缀（`/api/chat/stream` → `:8000/chat/stream`）。
- 一级前缀摘录：`/chat /sql /rag /stream /upload /workflows /approvals /prompts /schedules /reports /traces /ops /health /metrics …`（完整清单见基线 JSON `openapi_route_inventory.first_level_prefixes`）。
- 含参数化一级段 `/{key} /{name} /{report_id} /{request_id} /{workflow_name}`——APISIX 兜底路由必须整段转发，不得做前缀白名单式过滤。

## 3. 认证与身份头行为（以 Java 实测实现为准，非方案假设）

**JWT（`HmacJwtVerifier.java`，权威）**：
- JJWT `Keys.hmacShaKeyFor(secret)`，**算法按密钥长度自动选择（注释实测 64 字节 → HS512）**；密钥 ≥32 字节，UTF-8 编码。
- Claims：`userId`（必填，数字会转字符串）、`username`、`dept`（dept_code，可为空）、`type`（access/refresh，过滤器校验）、`deviceId`。
- issuer=`hongmeng-oa`（requireIssuer）、exp + clockSkewSeconds=60、双密钥轮换（主密钥签名失败才试 previous，仅验签不放宽）。
- 拒绝原因枚举（ APISIX 插件需同粒度）：expired / issuer / signature / malformed / unsupported / invalid / missing-user-id / keystore-unavailable。

**黑名单**：Key = `auth:blacklist:<token>`，写侧 = auth-service PermissionCache（网关只读）。Redis 实测：`127.0.0.1:16379` TCP 可达，回 `NOAUTH`（密码在 Enterprise_OA `.env.oaauth`，**跨项目禁读**，B2 经 `AUTH_REDIS_PASSWORD` env 注入，不进 Git）。

**身份头**：伪造头剥离清单（代码默认）= `X-User-Id / X-User-Name / X-User-Dept / X-Auth-Type` **四头，不含 X-Trace-Id**——X-Trace-Id 是透传复用（有则沿用、无则生成 UUID）。白名单（docker 挂载 yml 实际生效版）：`/api/auth/login`、`/api/auth/refresh`、`/api/sys/users/register`、`/api/channels/whatsapp/webhook`。SCG 自身 401 体含判别符 `未认证：`（下游 401 不含）。

**FastAPI 侧**：`api_key_middleware` fail-closed（实测运行实例**已配置 API_KEY**：无凭据 → 401 `{"error":"Unauthorized","detail":"无效或缺失 X-API-Key"}`）；**鉴权中间件先于路由执行——未知路径也 401，404 永不可见**（基线实测确认，APISIX 404 语义需对齐此行为或明确接受差异）。

## 4. SSE 与上传限制现状

- Next.js `compress: false`（gzip 缓冲事故教训，注释在案）→ APISIX `/api/chat` 必须同样关 gzip + proxy_buffering。
- SSE 事件类型（代码/文档在案）：meta/status/log/delta/thinking/done/error；`chat.py` 当前有未提交修改（见 §5），**事件枚举以 B0 `--with-sse` 实测为准**（待运行）。
- 上传：后端 `upload_size_limit_middleware` + `RAG_MAX_FILE_SIZE`（config 读取在案）；确切当前值与端点路径（`/rag/upload` 族）待 `--with-upload` 实测确认。

## 5. 文件修改范围与工作区并发状态

- **工作区有 38 个未提交变更**（并发会话活跃，HEAD=de8b9c2）。其中与本迁移直接相关：
  - `frontend/next.config.js` 的 **AUTH_GATEWAY_URL 分流机制本身就是未提交成果**——归属另一会话，**本迁移不得覆盖**，切流时只改环境变量值。
  - `api-gateway/config/application.yml`（挂载版）有未提交 diff（SYS_SERVICE_URL 默认值调整）——同样不动。
  - `backend/app/api/routes/chat.py` 等后端文件有未提交修改——B0/B1 期间不碰 backend/。
- 本阶段新增（无冲突）：`scripts/gateway_baseline_check.py`、`docs/gateway-apisix-audit-report.md`、`.workbuddy/baseline_fastapi_direct.json`。
- 预期后续修改面：`docker-compose.yml`、`apisix/`（新建）、B4 时 `AUTH_GATEWAY_URL` 值、`docs/contracts/identity-header-protocol.md`（新建）。

## 6. 风险、冲突与待确认事项

| # | 类型 | 内容 | 处置 |
|---|---|---|---|
| R1 | 待确认 | AUTH_GATEWAY_URL 机制归属并发会话未提交变更 | 不动代码，只做值切换；提交前与该会话协调 |
| R2 | 风险（高置信） | Next standalone 的函数式 rewrites 在**构建期**固化（routes-manifest/required-server-files），运行时改 AUTH_GATEWAY_URL 对已构建产物无效 | 切流需**重建前端镜像**而非仅改 env；B1 用现有 `.next-smoke-login` 产物实测确认 |
| R3 | 方案修正 | FastAPI **无 `/api` 前缀** | APISIX 全路由需 StripPrefix（`/api/(.*)` → `/$1`，含 auth/sys → `/auth`、`/system`）；已写入方案 v2.0 补丁 |
| R4 | 方案修正 | 伪造头剥离实为**四头**（X-Trace-Id 是透传不是剥离） | 身份头契约文档按四头+trace 透传编写 |
| R5 | 低 | 现有 `smoke_gateway_auth.sh` 用 `未认证：` 判别 SCG 自身 401 | B2 复用该脚本矩阵时改用 APISIX 401 判别符（detail 前缀），脚本资产直接省一半工作量 |
| Q1 | 待实测 | SSE 事件枚举、首事件延迟、逐 chunk 行为 | `--with-sse` 显式运行（触发真实 LLM） |
| Q2 | 待实测 | RAG_MAX_FILE_SIZE 当前值、上传端点精确路径 | `--with-upload` + 读 config |
| Q3 | 待确认 | SCG 兜底 `/api/**`→ai-service 的现存消费方 | 询问用户/观察 8080 访问日志；不影响本次（不动 SCG） |

## 7. B0 验收自检

- [x] 基线脚本可重复运行（对 8000 实跑两轮：首轮暴露 2 个真实行为差异，修正后 0 error / 0 assert fail）
- [x] 关键结果有明确断言（拒绝形态 401/503、CORS 头形状、404 语义、SSE content-type/事件序列）
- [x] 失败输出具体原因（error 字段含 traceback，无 except:pass）
- [x] 重量级探针（SSE/上传）默认关闭、显式开启——不触发副作用
- [x] 路由清单取自运行实例 openapi.json（159 条），非静态代码推断

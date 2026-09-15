# APISIX 迁移方案 v2.0：Python 项目独立入口

> 版本：v2.0（2026-09-15）
> 范围（按任务书）：**仅 Python 项目**。Java 项目（SCG、auth-service、system-service、business-service）零改动。APISIX 成为 Python 项目独立入口，不合并两项目入口。
> v1.x 的"统一入口替换 SCG"方案作废，保留于 git 历史备查。
> 本版基于 2026-09-15 实际代码扫描（api-gateway/、backend/app/server.py、backend/config/auth.py、backend/app/api/{router,identity,middleware/auth}.py、frontend/next.config.js、frontend/src/lib/auth.ts、docker-compose.yml），差异按任务书规则 9 处理。

---

## 0. 扫描结论：任务书前提与实际代码的 4 处差异（规则 9，先说明再实施）

| # | 任务书假设 | 实际代码 | 影响 |
|---|---|---|---|
| 1 | Python 项目有"现有网关"可替换 | **不存在独立 Python 网关**。业务流量路径：Next.js 服务端 rewrites 把 `/api/auth/*`、`/api/sys/*` 代理到 Java SCG(8080)，**其余 `/api/*` 直连 FastAPI(8000)**；SCG 的 `/api/**`→ai-service 兜底路由另有消费方。本次实质是"**新建 APISIX 独立入口**" | 无"现有 Python 网关行为"可基线；基线对象改为"前端 rewrites + FastAPI 中间件行为" |
| 2 | 两项目登录态完全独立 | **登录态由 Java auth-service(8006) 签发**（JWT + HttpOnly refresh Cookie），前端经 Next 代理到 8080 获取。Python 侧 `IDENTITY_SOURCE=header/strict` 模式**不验 JWT**，只信任网关注入的身份头（`backend/config/auth.py` 明示"app 侧不再自行验 JWT"） | APISIX 必须自己承担 JWT 验签 + 黑名单（复用 oa-auth-redis:16379，只读不写）；`/api/auth/*`、`/api/sys/*` 路由保留指向同一 Java 上游——这是"不改 Java 认证流程"前提下的唯一兼容解 |
| 3 | `/api/sql`、`/api/mcp` 可能是预留 | **两者均已存在**（`api/router.py` 含 sql、mcp 路由器），且另有 mcp-service 容器(8091，标准 MCP 协议端点，X-API-Key + DNS rebinding 防护)与 rag-service(8090) | 路由表按真实存在接入；FastAPI 实际路由前缀约 25 个（见 §2），不能只配 5 条，用 `/api/*` 兜底 + 显式列举特殊路由 |
| 4 | 网关迁移不破坏现有认证 | FastAPI 已有**三层自有防线**：`api_key_middleware`（X-API-Key，fail-closed：未配置 503/错配 401，豁免 `/health /docs /metrics /internal /`）、`upload_size_limit_middleware`（RAG_MAX_FILE_SIZE）、`concurrency_limit_middleware`；另有 `/internal/ai/*`（X-Internal-Token，Java business-service 服务间调用） | APISIX 不得破坏这些中间件语义；X-API-Key 模型原样保留（双层认证：APISIX 管用户 JWT，FastAPI 管服务 Key）；`/internal/*` **不经 APISIX**，保持容器网络直连 |

另两个必须写进设计的实测事实：
- 前端 SSE 已有教训：Next.js `compress: false` 是为防 gzip 把流式响应缓冲成整块（next.config.js 注释）。**APISIX 对 `/api/chat/*` 必须关闭 gzip 与 proxy_buffering，否则复现同类事故**。
- 8000 端口在 docker-compose 绑定 `127.0.0.1`（信任边界=网络边界）。APISIX 加入后收口策略不变：8000/8090/8091 保持 loopback 或容器网络内，仅 APISIX 对外。

---

## 1. 架构

```
浏览器/前端(Next.js)
      │  同源 /api/*
      ▼
  APISIX (9080，独立入口，Python 项目所有)
      │
      ├─ /api/auth/* ──────► auth-service:8006 (Java，不改)
      ├─ /api/sys/* ───────► system-service:8001 (Java，不改)
      ├─ /api/* (兜底) ────► app:8000 (FastAPI)
      │     ├─ X-API-Key 中间件（保留）
      │     ├─ identity: header/strict（保留）
      │     └─ /internal/* 由容器网络 Java 直连，不经 APISIX
      └─ (可选，后置) MCP 外部 client → mcp-service:8091，host 路由 + MCP_ALLOWED_HOSTS 追加

外部不可达：8000 / 8090 / 8091 / 8006 / 8001 / 8081（loopback 或容器网络）
```

APISIX 职责：JWT 入口验签 + Redis 黑名单（只读 oa-auth-redis）+ 身份头注入/伪造头剥离 + CORS 收口 + 限流 + 访问日志/指标 + 转发。**不判业务权限、不选 Skill、不重试副作用请求。**

## 2. 路由表（按 B0 审计实测修正）

| 路径 | 上游 | 配置要点 |
|---|---|---|
| **全局改写** | — | **所有 `/api/*` 路由 StripPrefix：`/api/(.*)` → `/$1`**（B0 实测：FastAPI 路由无 `/api` 前缀，Next rewrites 与 SCG StripPrefix=1 都在剥前缀；auth→`/auth`，sys→`/system`） |
| `/api/auth/*` | auth-service:8006 | JWT 白名单（login/refresh/logout 本就不带 Bearer）；透传 Cookie |
| `/api/sys/*` | system-service:8001 | register 白名单；Cookie 透传 |
| `/api/chat/*` | app:8000 | **gzip off + proxy_buffering off + read timeout 600s + retries=0**；SSE 直通 |
| `/api/rag/*` | app:8000 | 请求体上限对齐 RAG_MAX_FILE_SIZE（后端还有一道 upload_size_limit 兜底） |
| `/api/sql/*`、`/api/mcp/*` | app:8000 | retries=0（副作用）；权限二次校验在 FastAPI 业务层（已存在） |
| `/api/observability/*` | app:8000 | 只读 |
| `/api/*`（兜底） | app:8000 | openapi 实测 **159 条 path**，含参数化一级段（`/{key}` 等）——兜底整段转发，不做前缀白名单过滤 |
| `/internal/*` | **不经 APISIX** | X-Internal-Token + 容器网络，Java→Python 服务间通道保持现状 |
| `/metrics`、`/health` | — | 不对外；Prometheus 抓 APISIX 自身 `/apisix/prometheus/metrics` |

前端改动（唯一确认的必要修改）：`AUTH_GATEWAY_URL` 从 8080 → APISIX 地址。**B0 修正：该 rewrites 是另一会话未提交成果（勿覆盖）；且 Next standalone 函数式 rewrites 构建期固化，切流 = 重建前端镜像，不是运行时改 env。** Cookie 同源经 Next 代理，域不变，refresh 链路不断。

## 3. 认证设计（对齐实际行为，不复制不存在的逻辑）

- **JWT 验签**（B0 已按 `HmacJwtVerifier.java` 核实）：JJWT `hmacShaKeyFor` 按密钥长度自动选 alg（64B→HS512，≥32B）；claims=`userId`(必填)/`username`/`dept`/`type`/`deviceId`；issuer `hongmeng-oa` + skew 60s；双密钥轮换（主密钥签名失败才试 previous）；拒绝原因枚举 expired/issuer/signature/malformed/unsupported/invalid/missing-user-id。Lua 插件需支持按密钥长度匹配 HS256/384/512，拒绝原因对齐该枚举。
- **黑名单**：`EXISTS auth:blacklist:<token>` @ oa-auth-redis:16379（B0 实测 TCP 可达、NOAUTH；密码经 env 注入不进 Git）。独立可测组件：连接超时/命令超时/池上限显式配置；超时/耗尽/拒连三类故障 → 401 fail-closed + 独立 reason + 指标 + 单测。**Redis 故障不得被误判为"用户未登录"（reason 必须可区分）。**
- **身份头**（B0 修正）：剥离清单实为**四头** `X-User-Id/X-User-Name/X-User-Dept/X-Auth-Type`；**X-Trace-Id 是透传复用（有则沿用、无则生成 UUID），不剥离**。注入同四头 + Trace。`X-User-Dept` 仅为上下文，权限校验在 FastAPI（现状保持）。契约文本落 `docs/contracts/identity-header-protocol.md`。
- **401 行为**：FastAPI 现有 `{"error":"Unauthorized","detail":...}` 风格优先于 v1.x 的 Java Result 格式——**本入口的 401 合同以 FastAPI 侧风格为准**（前端消费的是它），不逐字节复制 Java 格式。B0 另实测：FastAPI 鉴权中间件先于路由执行，未知路径也 401（404 被掩盖）——APISIX 对 `/api/*` 的 404 语义需在 B1 记录并对齐。
- **X-API-Key 保留原样**：FastAPI 中间件不动；APISIX 不接管服务级 Key（避免双写密钥管理）。
- **测试矩阵**：v1.2 §4.1 十二场景全部保留为合同，跑的对象改为"APISIX vs 前端-FastAPI 直连现状"。

## 4. 阶段计划与进度（B0~B4，B2 硬闸门）

| 批次 | 内容 | 退出条件 | 状态 |
|---|---|---|---|
| B0 基线 | 固化"Next rewrites + FastAPI 中间件"行为：路由清单、X-API-Key/身份头/CORS/上传限制/SSE 事件格式实测；`scripts/gateway_baseline_check.py`（打 8000 录制，后续打 APISIX 比对） | 脚本对现网直连全绿 | **已完成**（审计报告 `docs/gateway-apisix-audit-report.md`；基线 `.workbuddy/baseline_fastapi_direct.json` 11 探针全绿；SSE/上传两个重量级探针待显式开启补录） |
| B1 最小部署 | docker-compose 加 apisix 服务（standalone 声明式，`apisix/apisix.yaml` 进 git；不引 Nacos/etcd）；路由+CORS+转发，鉴权旁路；NEXT_DIST_DIR 同类坑检查 | 基线脚本（鉴权段旁路）打 9080 全绿 | **已完成**（容器 `agent-apisix` 运行中；基线比对除一项已知 404 语义差异外全绿；SSE 经网关逐块传输实测通过。实现细节：需挂载 config.yaml 声明 `role: data_plane`、timeout 用数字秒、勿加 host-gateway extra_hosts、系统直通路由 4 条，见 audit 报告 B1 段） |
| B2 认证插件 | gateway-auth（JWT+黑名单+头注入）+ 12 场景矩阵 + Redis 故障注入 + 伪造头测试 | 矩阵全绿，**未过不切流** | **已完成**（12/12 全绿 + 三类 Redis 故障 fail-closed + 20 并发拒连风暴全 401；身份头契约 `docs/contracts/identity-header-protocol.md`；测试实例 9081/enforce 隔离验证；prod 9080 以 shadow 挂载插件零行为变更。实测修正：nginx_config 键名 envs、插件标准字段 schema/priority/version、lua-resty-jwt 轮换需重新 load_jwt、cjson.null 判空、app upstream 容器名直连，详见任务日志） |
| B3 专项测试 | SSE 六维矩阵（首事件延迟/顺序/断连/超时/异常/并发；gzip off 验证打字机效果）；上传（正常/超限/中断/大文件）；网关重启/上游故障/配置回滚；副作用接口隔离回放（禁镜像写操作） | 矩阵全过 | **已完成**（SSE 对比 4/4 + 并发 3 流全含 done + 客户端断连网关不崩溃；上传 6/6 直连/网关一致含真实正常上传→探针文档已删；只读 SQL 经网关通过；上游故障 503 瞬时失败无重试风暴 + 自愈约 1-2min（dns_resolver_valid=5 已配）；APISIX 重启恢复 200；坏配置→加载失败门禁实证；回滚→0 错误；双副本 9080/9082 行为一致。600s 超时为配置级验证，真实长流留 B4 观察期。修正：prod 实际 mode=enforce（.env 已设，非 shadow）） |
| B4 切流 | 配置发布五步流程演练（构建/校验/分发/滚动/回滚）→ 前端 `AUTH_GATEWAY_URL` 切 APISIX（先 dev/test 后 prod）→ 观察错误率/认证拒绝/SSE 中断；SCG 8080 保留原样为回滚路径（回滚=环境变量还原） | 回滚演练成功；24h 观察零异常 | 待开始 |

配置发布（standalone 多副本一致性）：PR → `apisix` 容器内 dry-run 校验（失败禁合）→ git tag 版本分发 → 逐副本摘除/重载/健康检查/回入 → git revert 回滚。APISIX ≥2 副本 + LB 健康检查；健康检查、配置一致性与故障恢复在 B4 实测，不把"多副本"当作天然 HA。

## 5. 验收清单

P0 安全与兼容：
- [ ] Java 项目零改动（git diff 仅含 frontend env、docker-compose、apisix/、scripts/、backend 无关代码不动）
- [ ] 8000/8090/8091 外网不可达；伪造身份头直连上游无效
- [ ] 12 场景矩阵全绿；X-API-Key 中间件行为与迁移前逐项一致
- [ ] `/internal/*` 不经 APISIX 且 X-Internal-Token 校验保持
- [ ] FastAPI 业务权限/部门隔离/SQL 行安全/MCP 授权未被绕过
- [ ] 网关不重试非幂等请求（retries=0 实测：杀上游副本无重复副作用）

P0 功能：
- [ ] 全部实际路由经 APISIX 转发正确（含 ~25 个前缀抽样）
- [ ] `/api/chat` SSE：事件类型全、顺序对、无缓冲（对比直连逐 chunk）、断连语义文档化
- [ ] 上传限制与迁移前一致（不放宽不收紧）
- [ ] Trace ID：APISIX access log → FastAPI → LangGraph span 可关联；Token 统计口径零改动

P1 运维：
- [ ] 配置进 git + 发布前校验 + 回滚演练
- [ ] 多副本健康检查/故障恢复验证
- [ ] Prometheus 指标可用；关键测试接入 CI
- [ ] 部署说明 + 配置说明 + 测试报告交付

## 6. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| APISIX gzip/缓冲复现 SSE 事故 | 高 | B1 起对 /api/chat 关 gzip+buffering；B3 打字机逐 chunk 比对 |
| 前端 Cookie/refresh 链路受影响 | 高 | Cookie 同源经 Next 代理，域不变；B3 专项回归 login/refresh/logout |
| X-API-Key 与 JWT 双模型职责混乱 | 中 | 明确分工：APISIX=用户 JWT，FastAPI=服务 Key；文档化 |
| 兜底路由误伤（25 前缀中未列举的特殊路径） | 中 | 兜底指 app:8000 保持全部可达；基线脚本覆盖抽样 |
| SCG 兜底 `/api/**`→8000 路由仍有消费方 | 低 | 本次不动 SCG；后续确认无消费方后再议下线 |

## 7. 决策记录

1. 范围收窄为 Python 独立入口；v1.x 统一入口方案作废存档。
2. 登录态兼容解：APISIX 保留 `/api/auth/*`、`/api/sys/*` 路由指向 Java 上游，JWT 验签参数对齐 Java（issuer hongmeng-oa），黑名单 Redis 只读复用。
3. 401 合同以 FastAPI 错误风格为准，不复制 Java Result 格式。
4. `/internal/*` 与 rag-service/mcp-service 不经 APISIX；MCP 外部暴露为后置可选项。
5. 不引 Nacos/etcd；standalone + 五步发布流程；HA 在 B4 实测而非假设。

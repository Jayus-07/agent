# 网关可观测性与限流（2026-09-16 P1 增强）

> 范围：APISIX standalone 入口（`apisix/`）。对应优化清单 GAP 1（指标接入）与 GAP 2（网关限流）。
> 全部变更经 9081 测试台架验证后上生产（认证矩阵 12/12、限流专项 4/4、baseline 回归 errors=0）。

## 1. 指标接入 Prometheus（GAP 1）

### 数据通路

```
APISIX prometheus 插件（逐路由挂载）
    └─ 导出服务 9091（plugin_attr.export_addr.ip=0.0.0.0，仅容器网络可达，
       compose 未发布该端口到宿主机——暴露面收敛不变）
         └─ Prometheus 抓取 job=agent-platform-apisix（docker/prometheus.yml）
              └─ 告警规则 docker/prometheus-alert-rules.yml（Gateway* 4 条）
```

三个此前"看起来配了但实际没生效"的坑（全部实测修复）：

1. **导出服务默认绑容器内 127.0.0.1**：Prometheus 容器跨网络够不着。
   `config.yaml` 加 `plugin_attr.prometheus.export_addr.ip: "0.0.0.0"`。
2. **prometheus 插件只在全局清单声明、没挂到路由**：`apisix_http_status{route,code}`
   等路由级指标需要逐路由启用。两个路由文件所有路由已补 `prometheus: {}`
   （含白名单 auth/sys 路由——观测与认证无关）。
3. **gateway-auth 自定义 counter 从未生效**：旧代码
   `require "apisix.plugins.prometheus.metrics"` 在 3.13 不存在，pcall 静默吞掉；
   且 APISIX 按 priority 降序跑各插件 init_worker，gateway-auth(2500) 先于
   prometheus(500)，init_worker 期 exporter 单例还是 nil。修复为 **access 阶段
   惰性初始化**（首次计数时注册 counter，之后走缓存）。

### 指标清单

| 指标 | 含义 |
|---|---|
| `apisix_http_status{code,route,...}` | 逐路由状态码计数（限流 429、认证 401、上游 5xx 都在此） |
| `apisix_gateway_auth_denied_total{route,reason}` | 认证拒绝，reason 对齐插件枚举（no-credential/expired/signature/blacklist/...） |
| `apisix_gateway_auth_would_deny_total{route,reason}` | shadow 模式"本该拒绝"计数（灰度观察用） |

### 告警规则（4 条）

- `GatewayDown`（critical）：指标端点失联 1 分钟
- `GatewayAuthDeniedSpike`（warning）：10 分钟拒绝 >100 次，按 reason 排查
- `GatewayRateLimitedSpike`（warning）：10 分钟 429 >50 次
- `GatewayUpstream5xxHigh`（warning）：上游 5xx 持续 >1 rps（网关 retries=0，5xx 即上游真实失败）

## 2. 网关限流（GAP 2）

### 设计

- **限流键 `$http_x_user_id`**：gateway-auth(priority=2500) 先注入身份头，limit 插件后执行。
  键为空（api-key 通道 / OPTIONS 预检）时 limit-count **自动回退 remote_addr**
  （插件源码 `limit-count/init.lua` 实测语义）——用户配额与 IP 配额天然分桶。
  未认证请求在 gateway-auth 层已被 401，不消耗限流配额。
- **limit-conn 的 `key` 是必填字段**（无空值回退语义——生产坏配置门禁实测抓到），
  按 IP 限流必须显式写 `key: remote_addr`。
- **policy=local**（进程级计数）：当前单副本语义正确。
  **扩多副本时必须切 redis 策略**，否则配额按副本数放大。
- 阈值刻意宽松（后端还有 MAX_CONCURRENT_REQUESTS=5 并发门兜底）：
  网关层定位是反滥用与按用户公平，不是主容量防线。

### 路由配额（apisix/apisix.yaml）

| 路由 | 限流 | 理由 |
|---|---|---|
| chat-sse `/api/chat/*` | conn 3+burst 2 / count 30/min，按用户 | SSE 长连接最贵；LLM 调用配额防脚本刷 |
| sql/mcp 副作用 | conn 10+burst 5，按 IP | api-key 服务调用为主，防并行风暴 |
| app-fallback `/api/*` | count 600/min（≈10rps），按用户 | 反滥用基线 |
| auth/sys 白名单、系统直通 | 不限流 | 登录链路不能被限流锁死；直通路由无认证头 |

拒绝行为：429（含 `X-RateLimit-*` 头），与后端并发门 503+Retry-After 区分——
429=你请求太快（网关），503=服务器忙（后端排队超时）。

### 回归测试

- `scripts/gateway_rate_limit_check.py`：4 场景（计数限流/用户隔离/并发限流/IP 回退），
  对 9081 台架 `/limited` 专项路由（小配额让断言快）。
  注意 limit-count 窗口 60s，脚本用随机 userId 避免上一轮配额污染。
- `scripts/run_gateway_test_stack.sh`：重建 9081 台架（echo 桩 + enforce 测试实例），
  历史启动脚本失传后的固化版本。`RUN_MATRIX=1` 直接跑 12 场景矩阵。
- echo 桩新增 `?delay=N`：limit-conn 需要慢上游保持连接才能触发并发拒绝。

## 3. 验证记录（2026-09-16）

| 验证项 | 结果 |
|---|---|
| 9081 台架认证矩阵 | 12/12（新插件代码零回归） |
| 9081 台架限流专项 | 4/4（count 200×5→429；用户隔离；conn 容量 3→第 4 并发 429；api-key 回退 IP 桶） |
| 生产坏配置门禁 | 抓到 limit-conn 缺 key（修复后零 error） |
| baseline 回归（9080） | 11 探针 errors=0，failed_asserts=[]（404 语义差异为 B1 已知项；SSE/上传/api-key 为跳过项非失败） |
| Prometheus targets | agent-platform-apisix / agent-platform-app 均 up |
| 告警规则 | Gateway* 4 条加载成功 |
| 自定义指标端到端 | `apisix_gateway_auth_denied_total{route,reason}` Prometheus 可查 |

## 4. 访问审计日志（2026-09-16 补齐：GAP 5 第一层）

回答"哪个用户从哪个 IP 访问了后端、做了什么"——两层留痕，用户 ID 与 IP 首次落到同一条记录：

### 网关层（apisix/config.yaml → /dev/stdout）

每请求一行 JSON（`docker logs agent-apisix` 直接查）：
`time / client_ip / user_id / auth_type / trace_id / method / uri / query / status / bytes / duration / upstream_time / ua`。

- `client_ip` 可信：`real_ip_from` 仅信任 loopback，伪造 `X-Real-IP` 不影响 `$remote_addr`。
- `user_id` 可信（gateway-auth 验签后注入，限流键同源）；**例外**：auth/sys 白名单
  路由不挂插件，客户端自带头原样透传，该段路由的 user_id 不可当真。
- 被网关 401/429 拒绝的请求同样留痕（`upstream_time` 为 "-" 即网关层拒绝）。

### 后端层（backend/app/api/middleware/access_log.py）

每请求一行 `[Access] ip= user= auth= METHOD path?query -> status Xms trace=`（logger "rag_system"）。

- IP 取 `X-Forwarded-For` **最后一跳**（APISIX `$proxy_add_x_forwarded_for` 追加，
  客户端自带的左侧前缀可伪造）；直连 8000 调试无 XFF 时回退 peer。
- 注册在 api_key/concurrency **之后**（Starlette 后注册的在外层执行），
  401/503/413 短路也会被打点；回归测试 `backend/tests/api/test_access_log.py`。
- 后端容器需重建镜像后生效（代码 COPY 进镜像）。

与 `X-Trace-Id` 关联即可从 access log 跳到 `ai.trace_records` 的 agent 执行链路。
集中访问日志平台（GAP 5 完整形态）仍属遗留，见下。

## 5. 遗留（不在本次范围）

- GAP 3 TLS / GAP 6 多副本：对外暴露 9080 前的决策项（当前 loopback-only）。
  多副本时除 limit 切 redis 策略外，按 migration-plan §4 五步发布流程滚动。
- GAP 5 WAF / 集中访问日志：无日志平台支撑前不引入（stdout JSON + 后端
  [Access] 日志为第一层，见 §4；接入 Loki/ELK 时按此结构直采即可）。
- 疑点待确认：生产容器 `JWT_ISSUER=agent-platform`，但 B0 审计记录 Java 签发方
  issuer 为 `hongmeng-oa`（9081 台架合同值）。若真实前端流量走 Java 签发的 JWT，
  enforce 下会全量 401 issuer——切真实流量前需与 auth-service 实际签发值核对。

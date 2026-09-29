# Domain Runtime STOP E — Real Production Failure Drill（实机故障演练报告）

> 日期：2026-09-24 ｜ 前置：STOP D PASS（cfc8389）
> 环境：真实 APISIX :9080 / FastAPI :8000 / Redis / PostgreSQL 5433 / Celery worker 池 / DeepSeek LLM。
> 纪律：共享栈容器零改动；依赖故障用**隔离实例法**（本机 :8001 独立真实实例 + env 指向死依赖，
> 直连模式与 e2e_travel_runtime.py 实例级验收同一先例）；主实例只做非侵入演练。
> 工具：`e2e_domain_failures.py` / `e2e_domain_tenant.py` / `e2e_domain_runtime.py`（已提交）/ 冻结层 `e2e_async_runtime.py`。

```text
STOP_E_PASS    = true
STOP_F_ALLOWED = true
```

---

## E16 E2E Matrix（全部真实命令/真实结果）

| Case | Domain | Fault | Expected | Result | 证据来源 |
|---|---|---|---|---|---|
| Normal ×10 轮 | General/RAG/SQL/CS/Travel/Selection | none | success | **PASS 10/10** | STOP C 矩阵（逐轮 trace 拓扑） |
| Cross-domain | CS→Travel→CS→Selection | none | isolated | **PASS** | STOP C（active_domain 分桶 + C9 落库核查） |
| Tenant | 双租户同 session_id | none | no cross-tenant | **PASS** | 本轮 E3：Redis 键分桶（default/厦门 vs tenant-b/杭州）+ 跨租户任务 404 |
| Auth | SQL（viewer 任务） | denied | no execute | **PASS** | STOP D R2：sql step permission_denied、0 执行 |
| Recovery | async domain task | worker 死亡（lease 回拨 sweep） | recovered | **PASS** | STOP D R3：E1≠E2、旧 worker fencing 退出、SUCCESS 归属 E2（Phase2 另有真实 SIGKILL 记录） |
| Pause/Resume | async task | pause | resume 原 workflow | **PASS** | STOP D R4：换发 execution、劫持写被拒 |
| Router | 主图路由 | embedding/rag-service 死端口 | safe fallback | **PASS** | E5：隔离实例 200 + RAG 拒答兜底，未随机选域 |
| Redis | 全局 | REDIS_URL 死端口 | fail-safe | **PASS** | E7：隔离实例照常出完整厦门行程（ConversationContext memory fallback） |
| PostgreSQL | 全局 | PGHOST/PGPORT 死端口 | 无半成功 | **PASS** | E8：实例启动期 fail-closed（端口不开放），done=0，不可能「前端成功 DB 失败」 |
| Domain node | workflow step | 行级权限拒绝（promotions） | 限定本域 | **PASS** | STOP C T3：fetch_promotions permission_denied → workflow 失败如实返回，未跳域 |
| LLM | Model Governance | timeout/fallback | governed fallback | **结构+指标 PASS**（见 §2 披露） | llm_requests_total=51+ 持续计量；llm_fallback_total/llm_failures_total 指标族在 registry（E14 抓取） |
| Client disconnect | 主实例 | SSE 3 帧后断连 | 服务端收尾+健康 | **PASS** | E12：断连后主实例后续请求 200（trace 收尾由 runner._finalize_trace 幂等保证） |
| Duplicate | 主实例 | 同幂等键 ×2 | 读允许重复 | **PASS** | E13：[200,200]；副作用侧 STOP D R 矩阵 duplicate-delivery SUCCESS_NOOP |
| Observability | 指标面 | — | 可关联 | **PASS** | E14：routing_domain_total=10、chat_request_total 32→33 随请求增长、llm_requests_total=51、label 卫生抽查无长文本 |
| Log safety | 日志面 | 金丝雀 | 密钥不落日志 | **PASS（密钥硬门）** | E15：API Key 无泄漏；query 文本进路由 INFO 日志=既有策略，登记 P2-11 |
| Context | 预算面 | 7.3K 字超长输入 | budget protected | **PASS** | E11：Input Guard 干净拦截（≤20000 字文案、无崩溃）；正常轮 done.context_usage={input_budget:7168, usage_ratio:0} 计量活跃，阈值未动 |

## 逐场景真实输出摘要

- **E5**（隔离实例，EMBEDDING_API_BASE/RAG_SERVICE_URL→127.0.0.1:9）：
  `http=200 answer='## 抱歉 …未能找到与「查一下最近的销售额数据」相关的信息。'` —— 路由依赖死亡时规则层照常守门、RAG 层降级拒答，绝无随机跳域。
- **E7**（REDIS_URL→redis://127.0.0.1:9/0）：
  `http=200 answer='# 杭州 2 天行程 … ¥1042'` —— Redis 不可达，ConversationContext 落 memory fallback，旅游域图完整出单。
- **E8**（PGHOST/PGPORT→9）：`health=None served=False` —— 启动期 fail-closed，端口不开放；唯一失败态（done 成功帧）实测为 0。
- **E12**：读 3 帧后主动断连（682ms）→ 8s 后主实例 `http=200` 正常应答。
- **E13**：同 idempotency_key 两次 → `[200, 200]`。
- **E14**：`routing_domain_total before=10 after=10（域问题才增量，口径正确）`、`chat_request_total 32→33`、`llm_requests_total 51`；worker(:9809) 宿主机不可达（端口未发布）→ 任务指标以 STOP D R1-R5 实证为准，如实记录。
- **E15**：金丝雀 prompt 出现在路由 INFO 日志 = 既有日志策略（路由观测记录 query，非密钥/非 RAG 文档/非全量上下文）→ **P2-11 登记**；API Key / Bearer 值零泄漏（硬门）。
- **E3**：`A default/50 → 厦门`、`B tenant-b/51 → 杭州`，同 session_id 两把 Redis 键互不可见；跨租户任务 404；memory_records 按 tenant 分域（default|8）。
- **E11**：7313 字输入 → `## 无法处理该输入（单次提问不超过 20000 字…）`干净拦截；正常轮 `context_usage={"used_tokens":0,"input_budget":7168,…}`——预算计量每轮可见，threshold 未动（context_budget 测试族在 STOP B 八目录回归全绿）。

## 披露（不为 PASS 隐藏）

1. **E10 LLM 故障未做实机注入**：Model Governance 冻结后 provider/凭据由 DB registry 解析，env 注入无效键不再构成故障路径；故障注入需改共享 DB 治理数据（影响他人会话），故只做结构与指标验证（proxy fallback 层 + llm_failures/llm_fallback 指标族存在）。风险：provider 级故障的实机演练债。
2. **E9 采用 lease 回拨 sweep 而非真实 SIGKILL**（共享 worker 池不可杀）；真实 SIGKILL 恢复已由 Phase2 收口记录（SIGKILL→visibility_timeout→Recovery 闭环）。
3. **E11 未触发 L5 压缩实机**（需巨型会话，成本高）；压缩管线由 context_budget 测试族覆盖，实机只验证入口保护与计量面。
4. worker :9809 指标端口未对宿主机发布（compose 内部），E14 以 app 指标 + STOP D 实证合并覆盖。

## 移交 STOP F

P2 新增：P2-11（query 文本进路由 INFO 日志——既有策略，建议生产切 WARNING 采样）；P2-10（trace PG 镜像空）延续。

```text
STOP_E_PASS    = true
STOP_F_ALLOWED = true
```

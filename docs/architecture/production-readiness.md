# 企业生产化改造路线图

> 文档目的：从 **Demo 级** → **生产级** 的差距清单与验收标准。
> 范围：架构层、运维层、流程层（不动业务逻辑）。
> 优先级依据：CLAUDE.md「P0 立即修 / P1 建议修 / P2 锦上添花」。

---

## 0. 总览：当前 vs 目标

| 维度 | 当前状态 | 目标状态 | 差距等级 |
|------|---------|---------|---------|
| **可观测性** | 自建 Tracer + 文本日志 | 指标 + 结构化日志 + Trace 三件套 | **P0** |
| **稳定性** | 重试 + 降级 + 超时 | 重试 + 限流 + 熔断 + 隔离 + 降级 | **P0** |
| **安全合规** | SQL 注入防护 + 行级权限 | + 审计日志 + 密钥管理 + 数据脱敏 | **P1** |
| **部署运维** | .bat 脚本 + .env | 容器化 + 健康检查 + 配置外部化 + 灰度 | **P1** |
| **质量保障** | 单元测试 + 手动验收 | + 契约测试 + 负载测试 + SLA 验收 | **P1** |
| **文档协作** | 架构 + 命令文档 | + Runbook + Postmortem + ADR | **P2** |

> 任何一项未完成 → 不能上生产。
> P0 必须 100% 达标，P1 ≥ 80%，P2 按预算选做。

---

## 1. 可观测性（Observability）— **P0**

### 1.1 现状
- **Trace**：自建 `backend/rag/tracer.py`（Span 模型 + 内存 + 落库），详见 `docs/observability/trace-model.md`
- **告警**：`backend/orchestration/supervisor/alerts.py`（代码内 alert，未对接外部系统）
- **日志**：`backend/rag_system.log`（文本格式，无 trace_id 串联）
- **指标**：✅ `backend/app/api/middleware/metrics.py`（PR-0.3）— 4 metric + `/metrics` 端点
- **健康检查**：`GET /health`（极简版，只确认进程存活）

### 1.2 缺口 & 行动项

| # | 行动项 | 验收标准 | 工作量 | 状态 |
|---|--------|---------|--------|------|
| O1 | 引入 `prometheus_client` + `/metrics` 端点 | Grafana 能拉到 4 类黄金指标 | 中 | [x] PR-0.3 ✅ 4 metric 已暴露（缺 Grafana 仪表盘） |
| O2 | 结构化日志（JSON，含 `trace_id`/`session_id`/`capability`） | 一条请求可串起所有日志 | 小 | [ ] 待办 |
| O3 | OpenTelemetry 集成（替换自建 Tracer） | Trace 可导出到 Jaeger / Tempo | 大 | [ ] 待办（路线图阶段 2）|
| O4 | 关键 SLI 定义文档化（见下表） | 每个 SLI 有采集点 + 告警阈值 | 小 | [x] ✅ §1.3 已定义（缺告警规则实现）|

### 1.3 SLI 定义（首批 P0 指标）

| SLI | 类型 | 采集点 | 告警阈值 |
|-----|------|--------|---------|
| `chat_request_duration_seconds` | Histogram | `/chat/stream` 全链路 | P99 > 30s |
| `chat_request_total{status}` | Counter | `/chat` 入口 | error_rate > 5% |
| `skill_execution_duration_seconds{skill, capability}` | Histogram | BaseSkill.execute | P99 > 60s |
| `skill_failure_total{skill, error_type}` | Counter | BaseSkill.execute | 突增 > 2x 基线 |
| `llm_tokens_total{model, direction}` | Counter | infra/llm/proxy.py | 单用户 > 100k/min |
| `rag_retrieval_recall_at_k` | Gauge | retrieval 评估脚本 | < 0.7 触发告警 |
| `workflow_run_duration_seconds{workflow}` | Histogram | Scheduler | P99 > 300s |

### 1.4 验收清单
- [x] `curl /metrics` 返回 Prometheus 格式（PR-0.3 ✅ 7/7 测试通过）
- [ ] Grafana 仪表盘覆盖 7 个 SLI（待办）
- [ ] 错误日志 100% 含 `trace_id`（待办，路线图 O2）
- [ ] 至少 3 个 SLO 告警规则上线（待办，路线图 O4 告警规则实现）

---

## 2. 稳定性（Resilience）— **P0**

### 2.1 现状
- **重试**：`BaseSkill.execute` 自带 2 次重试 + 指数退避（`skills/base.py`）
- **超时**：60s 软超时（`OVERALL_REQUEST_TIMEOUT`）
- **降级**：`supervisor/degradation.py`（步骤失败时降级到替代步骤）
- **限流**：⚠️ `backend/infra/llm/rate_limiter.py`（PR-0.4）— TokenBucket 已实现，**仅日志未返 429**（PR-2.4 接 HTTP 429）
- **熔断**：❌ 无
- **隔离**：✅ LangGraph 节点级隔离（Skill 失败不影响 Supervisor）

### 2.2 缺口 & 行动项

| # | 行动项 | 验收标准 | 工作量 | 状态 |
|---|--------|---------|--------|------|
| R1 | LLM 限流（按用户/全局 QPS + Token Rate） | 突发流量可被削峰 | 中 | [ ] PR-0.4（仅日志）→ PR-2.4（接 429）|
| R2 | 下游熔断（DeepSeek / PG / Chroma） | 失败 N 次自动断开 | 中 | [ ] 待办 |
| R3 | 隔离舱（Bulkhead）：LLM / DB 独立线程池 | 一方阻塞不拖垮另一方 | 中 | [ ] 待办 |
| R4 | 全局超时链路（ContextVar 串联） | 子调用继承根超时 | 小 |
| R5 | 优雅停机（uvicorn SIGTERM → 等待 in-flight） | 重启 0 丢请求 | 中 |

### 2.3 推荐工具
- 限流：`slowapi`（FastAPI）或自研 Token Bucket
- 熔断：`pybreaker` 或 `tenacity` + 自研状态机
- 隔离：`asyncio.Semaphore` + 独立线程池

### 2.4 验收清单
- [ ] 注入故障测试（chaos）：LLM 503 时熔断器打开
- [ ] 压测 5x 峰值流量不雪崩
- [ ] 重启时 0 丢请求（in-flight 全部完成）

---

## 3. 安全合规（Security & Compliance）— **P1**

### 3.1 现状
- **SQL 注入**：`backend/sql/` 6 层硬校验（行级权限 + 敏感列拦截）
- **会话隔离**：session_id 隔离 RAG 检索
- **审计**：❌ 无
- **密钥管理**：`.env` 明文（dev 可接受）
- **数据脱敏**：日志可能含 PII（用户 ID/邮箱）

### 3.2 缺口 & 行动项

| # | 行动项 | 验收标准 | 工作量 |
|---|--------|---------|--------|
| S1 | 审计日志（谁/何时/调了什么/用了多少 Token） | 7×24 可追溯 | 中 |
| S2 | 密钥外部化（Vault / AWS Secrets Manager） | .env 不再含密钥 | 中 |
| S3 | PII 自动脱敏（邮箱/手机/身份证） | 日志 + Trace 自动脱敏 | 小 |
| S4 | API 鉴权 + RBAC（按角色限制 capability） | 未授权调用 403 | 大 |
| S5 | 数据保留策略（Trace/日志/会话 TTL） | 30/90/180 天自动清理 | 小 |

### 3.3 验收清单
- [ ] 所有写操作可追溯到用户
- [ ] 任何密钥不在 Git 仓库
- [ ] 审计日志保留 ≥ 180 天

---

## 4. 部署运维（Deployment & Ops）— **P1**

### 4.1 现状
- **启动**：`restart_all.bat` + uvicorn reload（dev 模式）
- **配置**：`backend/config/settings.py` + `.env`
- **容器化**：❌ 无 Dockerfile
- **健康检查**：`/health` 极简
- **灰度**：❌ 无

### 4.2 缺口 & 行动项

| # | 行动项 | 验收标准 | 工作量 |
|---|--------|---------|--------|
| D1 | Dockerfile + docker-compose | 一键启动完整栈 | 中 |
| D2 | 深度健康检查（DB / LLM / Chroma 全部 ping） | 任一依赖异常 → 503 | 小 |
| D3 | 配置分层（dev/staging/prod） | 切换环境不改代码 | 中 |
| D4 | 启动探针（readiness）vs 存活探针（liveness） | K8s 能正确滚动 | 小 |
| D5 | 灰度发布（按 session_id / user_id 路由） | 新旧版本并存验证 | 大 |
| D6 | 备份恢复（PG / Chroma 定期快照） | RPO ≤ 1h | 中 |

### 4.3 验收清单
- [ ] `docker compose up` 启动完整系统
- [ ] `/health/ready` 检查所有依赖
- [ ] 环境变量覆盖全部配置项
- [ ] 故障演练：DB 挂掉 → readiness 503

---

## 5. 质量保障（Quality Engineering）— **P1**

### 5.1 现状
- **单元测试**：`backend/tests/` 部分覆盖
- **手动验收**：浏览器跑一遍（CLAUDE.md 强制）
- **契约测试**：❌ 无（依赖 Mock）
- **负载测试**：❌ 无
- **回归测试**：❌ 无

### 5.2 缺口 & 行动项

| # | 行动项 | 验收标准 | 工作量 |
|---|--------|---------|--------|
| Q1 | 契约测试（API ↔ Mock 一致性） | 见 `docs/observability/mock-as-api-contract.md` | 中 |
| Q2 | E2E 自动化（playwright 跑关键流程） | CI 必跑 4 个核心场景 | 中 |
| Q3 | 负载测试（locust/k6） | P95 延迟 ≤ SLO | 中 |
| Q4 | 回归测试集（Golden Dataset） | 每次发布 100% 通过 | 中 |
| Q5 | RAG 评估自动化（Recall@k, MRR） | 每次索引重建跑评估 | 小 |

### 5.3 验收清单
- [ ] CI 流水线含：lint + type + unit + contract + e2e
- [ ] 负载测试报告归档
- [ ] Golden Dataset 覆盖 6 个核心场景

---

## 6. 文档与协作（Documentation & Collaboration）— **P2**

### 6.1 现状
- **架构**：`docs/architecture/` 完整
- **命令**：`docs/operations/commands.md`
- **交接**：`docs/operations/HANDOVER-*.md` 多次记录
- **故障排查**：`docs/operations/troubleshooting-checklist.md`
- **Runbook**：❌ 无
- **Postmortem**：❌ 无
- **ADR**：❌ 无

### 6.2 缺口 & 行动项

| # | 行动项 | 验收标准 | 工作量 |
|---|--------|---------|--------|
| C1 | Runbook（每个告警对应处置手册） | 新人 5 分钟内能响应 | 中 |
| C2 | Postmortem 模板（无指责、5 个 Why） | 每次 P0 故障产出 | 小 |
| C3 | ADR 目录（重要架构决策记录） | 决策可追溯 | 小 |
| C4 | On-call 轮值 + 升级路径 | 7×24 有人响应 | 小 |

### 6.3 验收清单
- [ ] 每个 P0 告警有对应 Runbook
- [ ] 至少 3 篇 ADR（重大决策）
- [ ] On-call 手册覆盖升级路径

---

## 7. 实施路线（建议 3 个月）

### Phase 1（Month 1）— P0 全覆盖
1. **O2 结构化日志**（1 周）
2. **O1 Prometheus 指标**（1 周）
3. **R1 LLM 限流**（1 周）
4. **R2 下游熔断**（1 周）
5. **R5 优雅停机**（1 周）

**里程碑**：能监控、能限流、能熔断、能平滑重启。

### Phase 2（Month 2）— P1 核心
1. **D1 容器化 + D2 健康检查**（1 周）
2. **D3 配置分层**（1 周）
3. **S1 审计日志**（1 周）
4. **S3 PII 脱敏**（1 周）

**里程碑**：可部署、可追溯。

### Phase 3（Month 3）— P1 余项 + P2
1. **Q1-Q3 测试体系**（2 周）
2. **C1 Runbook**（1 周）
3. **O3 OTel + O4 SLO 完善**（1 周）

**里程碑**：测试完整、文档完整、可长期维护。

---

## 8. 与现有文档的关系

| 本文引用 | 路径 |
|---------|------|
| Trace 模型 | `docs/observability/trace-model.md` |
| Mock 契约 | `docs/observability/mock-as-api-contract.md` |
| 测试规范 | `docs/development/testing.md` |
| 重构规范 | `docs/development/refactoring.md` |
| 优先级 | `docs/development/priority.md` |
| 工作流架构 | `docs/architecture/workflow-engine.md` |
| 启动命令 | `docs/operations/commands.md` |

> 本文档是**索引 + 路线图**，具体实施时再下沉到独立子文档。

---

## 9. 验收总入口

完成所有 P0 + P1 后，才能宣称"达到企业生产级"。判断标准：

1. **可观测**：能 5 分钟内定位任何 P0 故障
2. **可恢复**：故障注入测试通过率 ≥ 95%
3. **可扩展**：单机压测 3x 当前峰值不雪崩
4. **可追溯**：任何操作可追溯到人/时间/原因
5. **可交接**：新人按 Runbook 可独立 On-call
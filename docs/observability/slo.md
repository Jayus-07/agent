# SLO — 服务质量目标与告警闭环

> 定义"目标 → 指标 → 告警"三层闭环：本文给出目标值，
> `backend/observability/metrics.py` 提供指标，
> `docker/prometheus-alert-rules.yml` 按本文阈值配置告警。

## 1. 延迟 SLO（Chat 链路）

LLM 生成走云 API（DeepSeek/Qwen），延迟拆成三段独立考核——
**TTFT（首 token 前的 prefill + 链路开销）**、**TPOT（decode 阶段每 token 耗时）**、
**E2E（请求总耗时）**：

| 指标 | 定义 | 目标 | 数据来源 | 告警规则 |
|---|---|---|---|---|
| TTFT P99 | 首 delta 事件距请求开始 | **< 3s** | `chat:ttft_p99_seconds` | `ChatTTFTP99High`（warning, for 10m） |
| TPOT P99 | (末 delta − 首 delta) / (delta 数 − 1) | **< 200ms/token** | `chat:tpot_p99_seconds` | `ChatTPOTP99High`（warning, for 15m） |
| E2E P99 | 请求总耗时 | **< 60s** | `chat:latency_p99_seconds` | `ChatLatencyP99High`（warning, for 10m） |
| E2E P95 | 请求总耗时 | < 30s | `chat_request_duration_seconds` | `ChatLatencyP95High`（warning, for 10m） |

TTFT 与 TPOT 受不同因素影响，分开考核才能正确定位：

- **TTFT 变差** → 查 Router 是否频繁落到 LLM 兜底层（`router_layer_total`）、
  并发排队（`request_concurrency_wait_seconds`）、LLM 上游排队。
- **TPOT 变差** → 查 LLM 上游限流/降速、输出长度分布、网络吞吐。

> 自托管 vLLM 部署后可将目标收紧至 P99 TTFT < 500ms（单请求 prefill 预算），
> 当前云 API 链路达不到该量级，故按 3s 设定。

## 2. 吞吐 SLO

| 指标 | 定义 | 说明 |
|---|---|---|
| QPS | `chat:qps:rate5m`（recording rule） | 基线容量参考；持续高于压测基线时评估扩容 |
| 错误率 | error / total（5m 窗口） | **< 1%**，> 5% 触发 `ChatErrorRateHigh`（warning） |

## 3. 可用性与饱和度

| 指标 | 目标 | 告警规则 |
|---|---|---|
| 熔断器状态 | 无 open | `LLMCircuitBreakerOpen`（critical） |
| 降级话术 | 10m 内 < 3 次 | `LLMDegradedAnswerSpike`（critical） |
| 并发排队 | `request_concurrency_queued` 不长期 > 0 | `ConcurrencyQueueSustained`（warning, for 10m） |
| 并发拒绝 | `request_concurrency_reject_total` 增长近 0 | 排队超时 503（超过 `CONCURRENCY_QUEUE_TIMEOUT`，默认 10s） |

## 4. 优先级与公平性（并发中间件）

重量端点并发满载时（`MAX_CONCURRENT_REQUESTS`，默认 5）：

- **high**：`/chat/stream`、`/chat`、`/cs` 交互式对话 —— 进高优先级等待队列，
  槽位释放时优先唤醒，保 TTFT SLO。
- **normal**：上传 / 重索引 / 导出等批量操作 —— 同 FIFO 排队，
  但高优先级等待者存在时永远后拿到槽位。
- 等待超过 `CONCURRENCY_QUEUE_TIMEOUT`（默认 10s）→ 503 + `Retry-After: 5`。

观测指标：`request_concurrency_active` / `request_concurrency_queued` /
`request_concurrency_wait_seconds{priority}` / `request_concurrency_reject_total`。

## 5. Prefix Cache 与冷启动（本地 Ollama 链路）

- **冷启动**：Ollama 模型默认几分钟空闲后卸载，下次请求需重新加载权重（TTFT 飙升）。
  通过 `OLLAMA_KEEP_ALIVE`（默认 `30m`）延长模型驻留，见
  `backend/infra/llm/providers/ollama.py`。
- **Prefix Caching**：Ollama（llama.cpp）对相同 prompt 前缀自动复用 KV Cache，
  同会话多轮请求 TTFT 显著下降——这是应用层无需改造即可享受的引擎侧优化；
  切换 vLLM 后对应 `--enable-prefix-caching`。
- 应用层的 `backend/rag/answer_cache.py` 是**结果缓存**（按查询哈希），
  与引擎侧 prefix cache（KV 级复用）互补：前者省掉整次生成，后者省掉 prefill。

## 6. 告警级别约定

- **critical**：服务不可用，立即处理（page）
- **warning**：降级运行，工作时间处理（ticket）

## 7. 怎么看这些指标

| 方式 | 入口 | 适用 |
|---|---|---|
| 原始值 | `curl http://localhost:8000/metrics` | 快速确认指标存在 |
| 曲线/告警 | Prometheus `http://localhost:9090`（`docker compose --profile observability up -d prometheus grafana`） | 查 recording rules、看 Alert 页签 |
| 看板 | Grafana `http://localhost:3001`（数据源已自动装配） | 长期趋势 |
| 成本明细 | `GET /observability/tokens/calls` | 每次调用的 token/成本 |

抓取配置：`docker/prometheus.yml`（目标 `host.docker.internal:8000`）。
注意：TTFT/TPOT 只在 `/chat/stream` 流式请求中采样；Histogram 首次观测后才会出现在 /metrics。

# 任务契约：LLM Proxy 核心模型网关解耦重构 (Tier 2)

## 1. 目标与背景
`backend/infra/llm/proxy.py` 单文件长达 2710 行，作为系统的核心模型调用网关，内联了以下异构职责：
1. 厂商适配与请求组装（OpenAI 兼容协议、DashScope、DeepSeek、Anthropic 等不同入参映射）；
2. 实时计费与 Token 审计核算（`_record_tokens` 300+ 行、汇率/阶梯定价换算、预扣预占结算）；
3. 配额治理拦截（租户级、模型级、请求级 Quota 校验与熔断恢复）；
4. 流式转换与增量格式化（SSE chunks 解析、流式打字机 delta 累积、tool_calls 碎片组装）；
5. 容灾熔断与降级重试（CircuitBreaker、备选模型路由切换）。

本任务严格遵循单一职责原则（SRP）与马斯克五步骤，通过 **责任链模式 (Chain of Responsibility) + 适配器模式 (Adapter Pattern) + 门面模式 (Facade Pattern)** 进行物理模块化拆解，消除单体网关巨兽，确保 100% 导入兼容性、Token 计费精度无损与流式/非流式响应零漂移。

## 2. 改造范围与职责划分
建立专职子包 `backend/infra/llm/gateway/`（或专职模块）：
- `proxy.py`：作为轻量门面协调器（Facade），代码控制在 350 行以内，协调各 Interceptor 与 Adapter；
- `backend/infra/llm/gateway/accounting.py`：Token 消耗记录、审计日志与财务预结算（下沉 `_record_tokens` 及相关指标统计）；
- `backend/infra/llm/gateway/streaming.py`：流式响应转换器、分片组装与流式计费回调；
- `backend/infra/llm/gateway/resilience.py`：重试策略、熔断器（CircuitBreaker / ProviderBreaker）适配与模型降级路由；
- `backend/infra/llm/gateway/payload.py`：统一请求结构构造、参数清洗与模型别名映射。

## 3. 验收标准
1. `proxy.py` 单文件行数从 2710 行削减 80% 以上（目标 < 450 行）；
2. 拆分出的各专职子模块单文件行数均控制在 150~500 行以内；
3. 新增架构守卫单测 `test_llm_proxy_modular_architecture.py`，守卫行数与符号契约；
4. 全量 LLM 核心单测（`test_circuit_breaker.py`, `test_llm_budget.py`, `test_llm_resilience.py`, `test_stream_usage.py`, `test_usage_billing.py` 等）全部通过；
5. PR CI 门禁检查全绿。

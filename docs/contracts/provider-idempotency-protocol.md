# Provider Idempotency Protocol — business-service 外部写契约（冻结）

> Phase 3 STOP C（2026-09-24）｜状态：**契约冻结，生产写未激活**（activation gate）
> 契约登记：`backend/shared/provider_idempotency.py::PROVIDER_CONTRACTS["business_service_http"]`

## 1. 背景

CS 域 `CS_WRITE_SOURCE=java`（默认 local）时，Python 侧对 business-service 的
`POST /internal/messages`、`POST /internal/state-transitions` 是真实外部写
（customer_service/conversation_store.py、state_transition.py）。
现契约（`backend/infra/http/business_client.py`）**无幂等键字段**，Java 侧
幂等支持未证实 → 能力登记为 `UNKNOWN`（fail-closed 语义）。

当前行为不受本契约影响的部分：两个 POST 均**不自动重试**
（business_client 显式设计，失败方向 = 丢一致性而非重复副作用），故不存在
blind replay 风险；风险面是「请求已生效而 Python 判失败」的状态漂移。

## 2. Provider Operation Contract（Java 侧激活前必须实现）

任何 business-service 写端点在接入前**必须**支持调用方传递：

| 字段 | 载体 | 语义 |
|---|---|---|
| `provider_idempotency_key` | HTTP Header `Idempotency-Key` | opaque 确定性值（sha256），由 Python 侧 `derive_provider_key()` 派生；同一 logical effect 跨 retry/recovery/delivery 恒定 |
| `logical_effect_id` | Header `X-Logical-Effect-Id` | 内部 ledger 四元组派生的 logical key 摘要（对账用） |
| `request_fingerprint` | Header `X-Request-Fingerprint` | canonical payload SHA-256；Java 侧必须校验 same key + different fingerprint = 409 CONFLICT |
| trace context | 既有 X-Trace-Id 链 | 观测关联 |

## 3. 激活门槛（Provider Contract Gate，任务书 §27）

Java 侧必须提供以下证据，缺一不可：

1. capability declaration：`Idempotency-Key` 支持的 endpoint 清单与 scope
2. native idempotency proof：same key same payload 重放返回同一结果且效果一次
3. retention window：key 保存时长
4. key scope：account/endpoint/tenant
5. payload replay semantics：same key different payload 的响应码与行为
6. status lookup semantics：按 logical_effect_id 查询真实状态（如支持）
7. timeout semantics：read-timeout-after-write 时 Java 侧行为
8. duplicate semantics：重复请求的响应可识别性
9. failure injection result：沙箱故障注入报告

证据齐备后：Python 侧将 `PROVIDER_CONTRACTS["business_service_http"]` 升级为
`SUPPORTED` 并移除 `activation_gate`；此前该 provider 保持 UNKNOWN fail-closed。

## 4. 冻结语义

- 内部 logical effect identity 权威 = `ai.idempotency_records` 四元组；
  provider key 只是它的确定性派生（方向不可逆）。
- 能力 UNKNOWN = 生产写不得启用（Gate C8）。
- 禁止声称 external exactly-once；正确口径 =
  `effectively-once where provider/transactional semantics permit;
   unknown external effects fail closed and require reconciliation`。

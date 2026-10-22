# business-service 客服业务契约（v1.0）

> `backend/mock/business_service/main.py` 是可运行的状态化业务替身。它为每个用户生成确定订单，内存保存售后申请，进程重启后会清空 Mock 申请；不执行资金退款。真实业务服务应实现相同 HTTP 契约，并在服务端完成订单归属、资格、幂等和售后状态校验。

## 通用约定

- Base URL：`BUSINESS_SERVICE_URL`（容器内默认 `http://business-mock:8081`）。
- 鉴权：`X-Internal-Token`。服务配置令牌后，请求必须精确匹配；失败返回 HTTP 401。
- 成功响应：`{"code": 0, "message": "ok", "data": {...}}`。
- 业务失败：非 2xx，封套为 `{"code": <业务码>, "message": "...", "data": null}`。
- 查询与写入都必须带服务端验证后的 `user_id`；售后请求还必须带可信 `tenant_id`。订单或申请不属于该用户/租户时返回 404，不泄漏资源是否存在。
- 写接口不自动重试。创建申请的 `request_id` 和修改原因的 `operation_id` 是 UUID 幂等键；重复相同请求返回原结果，键绑定不同内容返回 409。
- 请求应携带 `X-Trace-Id`，服务端须记录 Trace 关联。

## 订单与物流

### GET `/business/orders?user_id={id}`

最多返回 20 笔近期订单，按创建时间倒序。`data.orders[]` 包含 `id`、`order_no`、`status`、`payment_status`、`total_amount`、`item_count`、`item_summary`、`product_names`、`items`、`created_at` 和 `estimated_delivery`。

### GET `/business/orders/{order_no}?user_id={id}`

返回 `data.order`，字段与列表一致。订单不存在或不属于该用户时返回 404。

### GET `/business/orders/{order_no}/logistics?user_id={id}`

返回 `data.logistics`：`order_no`、`shipping_status`（`not_shipped` / `shipped` / `delivered` / `cancelled`）、`status_display`、`carrier`、`tracking_no`、`tracking_info`、`estimated_delivery` 和按时间升序排列的 `events[]`（`time`、`location`、`description`）。

## 退款申请

### POST `/business/refund-requests`

请求体：

```json
{
  "request_id": "<UUID 幂等键>",
  "user_id": "<服务端认证用户>",
  "tenant_id": "<服务端可信租户>",
  "conversation_id": "<客服会话>",
  "order_no": "MO-...",
  "expected_order_status": "paid",
  "expected_amount": 1299.0,
  "policy_version": "refund-policy-v1",
  "reason": "商品破损"
}
```

服务端按订单实时核对用户归属、状态、金额、政策窗口和活跃申请；禁止相信调用方金额。成功返回 `data.request`，含 `request_id`、用户可见 `request_no`、`order_no`、`amount`、`reason`、`status`、`status_display`、`created_at`、`updated_at` 和政策版本。初始状态为 `pending_review`，表示进入人工审核；不表示资金已退。

同一用户、租户、订单存在 `pending_review`、`reviewing` 或 `approved` 申请时返回 409。订单资格/事实变化也返回 409。Mock 读取 `CS_REFUND_WINDOW_DAYS` 与 `CS_REFUNDABLE_STATUSES`（默认与 Python 配置一致）；正式服务应以其唯一退款政策为准，并与客服资格查询保持一致。

### GET `/business/refund-requests?user_id={id}&tenant_id={id}&order_no={optional}&limit={1..100}`

按创建时间倒序返回当前用户和租户可见的 `data.requests[]` 及 `total`。客服用它读取最近申请的审核状态；没有匹配申请时返回空列表。

### GET `/business/refund-requests/{request_id}?user_id={id}&tenant_id={id}`

按申请 ID 查询 `data.request`。所有权或租户不匹配时返回 404。

### PATCH `/business/refund-requests/{request_id}/reason`

修改前请求体：

```json
{
  "operation_id": "<UUID 幂等键>",
  "user_id": "<服务端认证用户>",
  "tenant_id": "<服务端可信租户>",
  "reason": "商品破损"
}
```

仅 `pending_review` / `reviewing` 申请可改原因；修改须经客服提案与用户确认。已审核、撤销或不存在的申请返回 409/404。`operation_id` 重放返回原结果。

## 错误语义

| HTTP | 含义 | Python 客服行为 |
|---|---|---|
| 401 | 内部服务身份无效 | 拒绝业务操作 |
| 404 | 订单/申请不存在或越权 | 不区分原因，不暴露其他用户资源 |
| 409 | 资格变化、重复活跃申请或内容冲突 | 不假成功；提示刷新或转人工 |
| 422 | 参数格式错误 | 拒绝执行 |
| 网络错误 / 5xx | 写入最终结果可能未知 | 不重试、不报成功，进入 `verifying` 并提示人工核实 |

所有 GET 失败都不得回退到 sandbox。写操作只在 `CS_BUSINESS_GATEWAY_MODE=http` 且 `CS_REFUND_REQUEST_MODE=http` 时启用；本地案件库的 `sandbox` 写入仅在两个配置均为 `sandbox` 时启用。

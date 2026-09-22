# business-service 只读订单查询契约（v0.1，2026-09-22）

> 本契约由批次 B（客服业务 API 编排 MVP）定义。Java business-service 落地真实实现时**必须与本契约保持一致**；当前由 mock 容器（`backend/mock/business_service/main.py`，compose 服务 `business-mock`）提供同契约实现供联调。

## 通用约定

- Base URL：`BUSINESS_SERVICE_URL`（Python 侧 `backend/config/messaging.py`，默认 `http://127.0.0.1:8081`；容器网络内为 `http://business-mock:8081`）
- 鉴权：请求头 `X-Internal-Token`。服务端配置了 `INTERNAL_API_TOKEN` 时必须匹配，不匹配返回 HTTP 401（fail-closed）
- 响应封套（HTTP 200 时）：

```json
{"code": 0, "message": "ok", "data": {...}}
```

- 业务失败：HTTP 非 2xx + `{"code": <业务码>, "message": "...", "data": null}`
- 追溯：客户端会带 `X-Trace-Id`，服务端应记入日志

## 端点

### GET /business/orders?user_id={id}

订单列表（按创建时间倒序，最多 20 笔）。

`data` 结构：

```json
{
  "orders": [
    {
      "order_no": "MO-1A2B3C4D",
      "status": "shipped",
      "payment_status": "paid",
      "total_amount": 199.0,
      "item_count": 2,
      "item_summary": "演示商品-A",
      "created_at": "2026-08-01T12:00:00+00:00",
      "estimated_delivery": "2026-08-04T12:00:00+00:00"
    }
  ],
  "total": 1
}
```

错误：缺 `user_id` → 422；网关/服务故障 → 5xx。

### GET /business/orders/{order_no}?user_id={id}

订单详情。`data.order` 为单对象，结构与列表元素一致。

错误：订单不存在或不属于该用户 → **404（不区分，防探测）**。

### GET /health

存活探针：`{"status": "ok", ...}`。

## Python 侧对接状态

| 能力 | 状态 |
|---|---|
| 订单列表 `OrderService.query_orders(query_type="list")` | ✅ 已接入（`CS_BUSINESS_GATEWAY_MODE=http` 时生效） |
| 订单详情 `query_orders(query_type="detail")` | ✅ 已接入 |
| 订单明细 `query_order_items` | ⏸ 契约占位（`/business/orders/{no}` 响应预留 `items` 字段；未提供前显式报错） |
| 写操作（退货/退款） | 未开放——等只读链路验收后按此契约格式扩展 |

## 失败语义（Python 侧映射）

| 网关行为 | OrderService 异常 | 用户话术 |
|---|---|---|
| 404 | `OrderNotFoundError` | 查无此单 |
| 401 / 5xx / 网络错误 | `DatabaseError` | 「暂时查不到订单信息，请稍后再试」 |
| 封套 code≠0 / 缺 data | `DatabaseError` | 同上 |

**红线：网关失败绝不降级回 sandbox 演示库**——真环境返回假数据比查不到更危险。

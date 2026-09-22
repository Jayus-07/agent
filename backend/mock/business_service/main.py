"""business-service HTTP mock（只读订单查询，批次B 联调）。

契约与真实 Java business-service 一致（见同目录 contract.md）：
  GET /business/orders?user_id={id}            订单列表
  GET /business/orders/{order_no}?user_id={id} 订单详情
  GET /health                                  存活探针

统一响应封套：
  {"code": 0, "message": "ok", "data": {...}}          成功
  {"code": 40401, "message": "...", "data": null}      业务失败（HTTP 404）
  {"code": 40101, "message": "unauthorized", ...}      鉴权失败（HTTP 401）

设计约束：
  - 零外部依赖（不连 DB/Redis），内存确定性数据（hash 播种），
    同一 user_id 每次返回相同订单，联调可断言
  - 配置 INTERNAL_API_TOKEN 时强制校验 X-Internal-Token（fail-closed）
  - 独立进程运行：uvicorn backend.mock.business_service.main:app
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException, Query

app = FastAPI(title="business-service mock", docs_url=None, redoc_url=None)

_INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

_STATUSES = ["pending_payment", "paid", "shipped", "delivered", "completed", "refunded"]
_PAYMENTS = ["unpaid", "paid", "refunded"]


def _check_token(x_internal_token: str | None) -> None:
    """内部令牌校验：mock 配置了令牌时必须匹配（fail-closed）。"""
    if _INTERNAL_TOKEN and x_internal_token != _INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


def _seed_orders(user_id: str) -> list[dict]:
    """由 user_id 确定性生成 3 笔订单（同一用户恒定不变）。"""
    digest = hashlib.sha256(f"orders:{user_id}".encode()).hexdigest()
    orders: list[dict] = []
    base_ts = int(time.time()) - 90 * 86400  # 从 90 天前开始排
    for i in range(3):
        seg = digest[i * 8:(i + 1) * 8]
        n = int(seg, 16)
        status = _STATUSES[n % len(_STATUSES)]
        payment = _PAYMENTS[n % len(_PAYMENTS)]
        amount = round(20 + (n % 8800) / 100.0, 2)
        created = datetime.fromtimestamp(
            base_ts + (n % 60) * 86400 + i * 20 * 86400, tz=timezone.utc,
        )
        orders.append({
            "order_no": f"MO-{seg[:8].upper()}",
            "status": status,
            "payment_status": payment,
            "total_amount": amount,
            "item_count": 1 + n % 3,
            "item_summary": f"演示商品-{chr(65 + n % 26)}",
            "created_at": created.isoformat(),
            "estimated_delivery": (
                created + timedelta(days=3 + n % 5)
            ).isoformat() if status in ("paid", "shipped") else None,
        })
    orders.sort(key=lambda o: o["created_at"], reverse=True)
    return orders


def _ok(data) -> dict:
    return {"code": 0, "message": "ok", "data": data}


@app.get("/health")
def health():
    return {"status": "ok", "service": "business-mock", "time": time.time()}


@app.get("/business/orders")
def list_orders(
    user_id: str = Query("", description="用户 ID"),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    if not user_id.strip():
        raise HTTPException(status_code=422, detail="user_id is required")
    orders = _seed_orders(user_id)
    return _ok({"orders": orders, "total": len(orders)})


@app.get("/business/orders/{order_no}")
def get_order(
    order_no: str,
    user_id: str = Query("", description="用户 ID"),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    if not user_id.strip():
        raise HTTPException(status_code=422, detail="user_id is required")
    for order in _seed_orders(user_id):
        if order["order_no"].lower() == order_no.lower():
            return _ok({"order": order})
    raise HTTPException(status_code=404, detail="order not found")

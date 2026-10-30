"""customer_service/service/mall_service.py — 商城最小域服务（P2，Mock 优先）

双模式收口与 order_service 同构（docs/tasks/cs-workstation-v2/TASK.md §2.3）：
  - sandbox（默认）：直查/直写 agent_business —— 商品 product.products 只读
    映射、钱包 finance.wallet_*（迁移 125）、订单 "order".orders 状态机
    pending→paid→shipped→completed / cancelled / refunded
  - http：经 business_client 调 business-service（契约见
    backend/mock/business_service/contract.md v1.1 商城钱包账本节）

红线：
  - 金额一律 Decimal（DB NUMERIC → Decimal，禁止浮点累加）；
  - 服务端计价/对账（不信任调用方金额）；
  - 网关失败不降级回 sandbox；写接口幂等键由调用方提供（operation_key）。
"""
from __future__ import annotations

import threading
import uuid
from decimal import Decimal
from typing import Any

_ZERO = Decimal("0.00")
_MAX_TOPUP = Decimal("50000.00")
_MAX_TOPUP_F = 50000.0
_PAYABLE = ("pending", "unpaid")
_CANCEL_UNPAID = ("pending", "unpaid")
_CANCEL_PAID = ("paid", "paid")


class MallError(Exception):
    """商城域业务错误：code 供 API 层映射 HTTP，user_message 面向用户。"""

    def __init__(self, code: str, message: str, user_message: str | None = None):
        super().__init__(message)
        self.code = code
        self.user_message = user_message or message


def _use_http_gateway() -> bool:
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE

    return CS_BUSINESS_GATEWAY_MODE == "http"


def _unwrap(resp: dict) -> dict:
    code = resp.get("code", 0)
    if code != 0:
        raise MallError("gateway_error", f"业务网关返回失败 code={code}")
    data = resp.get("data")
    if not isinstance(data, dict):
        raise MallError("gateway_error", "业务网关响应缺少 data 字段")
    return data


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _mock_error(exc: Exception) -> MallError:
    """business-mock 409/404 错误 → 商城业务语义。"""
    from backend.infra.http.business_client import BusinessServiceError

    if isinstance(exc, BusinessServiceError):
        text = str(exc)
        if "insufficient wallet balance" in text:
            return MallError(
                "insufficient_balance", text, "账户余额不足，请先充值后再支付。",
            )
        if "-> 404" in text:
            return MallError("not_found", text, "订单不存在或无权操作。")
        if "-> 409" in text:
            return MallError("conflict", text, "订单状态已变化，请刷新后重试。")
    return MallError("gateway_error", str(exc))


class MallService:
    """商城最小域：商品只读 + 钱包账本 + 订单状态机。"""

    # ── 商品（两模式都直读本地 agent_business，只读） ─────────────

    def list_products(
        self, *, category: str | None = None, q: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        conditions = ["p.status = 'active'"]
        params: dict[str, Any] = {"limit": min(int(limit), 56)}
        if category:
            conditions.append("c.name = :category")
            params["category"] = category
        if q:
            conditions.append(
                "(p.product_name ILIKE :q OR p.brand ILIKE :q"
                " OR p.sku ILIKE :q)"
            )
            params["q"] = f"%{q}%"
        # DISTINCT ON 去重：inventory 可能按仓/批次多行，联表会放大商品行
        sql = """
            SELECT DISTINCT ON (p.id)
                   p.id::text AS product_id, p.sku, p.product_name, p.brand,
                   p.sale_price, c.name AS category,
                   COALESCE(i.stock_quantity, 0) AS stock
            FROM product.products p
            LEFT JOIN product.categories c ON c.id = p.category_id
            LEFT JOIN inventory.inventory i ON i.product_id = p.id
            WHERE {where}
            ORDER BY p.id, i.id DESC
            LIMIT :limit
        """.format(where=" AND ".join(conditions))
        with get_business_engine().connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        sql = """
            SELECT DISTINCT ON (p.id)
                   p.id::text AS product_id, p.sku, p.product_name, p.brand,
                   p.sale_price, c.name AS category,
                   COALESCE(i.stock_quantity, 0) AS stock
            FROM product.products p
            LEFT JOIN product.categories c ON c.id = p.category_id
            LEFT JOIN inventory.inventory i ON i.product_id = p.id
            WHERE p.id::text = :pid AND p.status = 'active'
            ORDER BY p.id, i.id DESC
            LIMIT 1
        """
        with get_business_engine().connect() as conn:
            row = conn.execute(text(sql), {"pid": product_id}).mappings().first()
        return dict(row) if row else None

    # ── 钱包 ─────────────────────────────────────────────────────

    def get_wallet(self, user_id: str, tenant_id: str) -> dict[str, Any]:
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import get_json_sync

                data = _unwrap(get_json_sync(
                    "/business/wallet",
                    params={"user_id": str(user_id), "tenant_id": str(tenant_id)},
                ))
                return data["wallet"]
            except MallError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._wallet_local(user_id)

    def _wallet_local(self, user_id: str) -> dict[str, Any]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        sql = """
            SELECT balance, currency, updated_at
            FROM finance.wallet_balances WHERE customer_id::text = :cid
        """
        ledger_sql = """
            SELECT direction, amount, balance_after, kind, ref_no, memo, created_at
            FROM finance.wallet_ledger
            WHERE customer_id::text = :cid
            ORDER BY id DESC LIMIT 5
        """
        with get_business_engine().connect() as conn:
            row = conn.execute(text(sql), {"cid": str(user_id)}).mappings().first()
            ledger = conn.execute(text(ledger_sql), {"cid": str(user_id)}).mappings().all()
        balance = _dec(row["balance"]) if row else _ZERO
        return {
            "balance": float(balance),
            "currency": (row or {}).get("currency", "CNY"),
            "updated_at": (row or {}).get("updated_at"),
            "ledger": [
                {
                    "direction": r["direction"],
                    "amount": float(_dec(r["amount"])),
                    "balance_after": float(_dec(r["balance_after"])),
                    "kind": r["kind"],
                    "ref_no": r["ref_no"],
                    "memo": r["memo"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in ledger
            ],
        }

    def top_up(
        self, user_id: str, tenant_id: str, amount: float,
        operation_key: str,
    ) -> dict[str, Any]:
        value = _dec(amount)
        if value <= _ZERO or value > _MAX_TOPUP:
            raise MallError("invalid_amount", "充值金额需在 0.01 - 50000 之间。")
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import post_json_sync

                data = _unwrap(post_json_sync("/business/wallet/topup", {
                    "user_id": str(user_id), "tenant_id": str(tenant_id),
                    "amount": float(value), "operation_key": operation_key,
                }))
                return data["wallet"]
            except MallError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._top_up_local(user_id, value)

    def _top_up_local(self, user_id: str, value: Decimal) -> dict[str, Any]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        with get_business_engine().begin() as conn:
            row = conn.execute(text(
                "SELECT balance FROM finance.wallet_balances"
                " WHERE customer_id::text = :cid FOR UPDATE",
            ), {"cid": str(user_id)}).first()
            if row is None:
                conn.execute(text(
                    "INSERT INTO finance.wallet_balances (customer_id, balance)"
                    " VALUES (:cid, :bal)",
                ), {"cid": int(user_id), "bal": value})
                balance_after = value
            else:
                balance_after = _dec(row[0]) + value
                conn.execute(text(
                    "UPDATE finance.wallet_balances SET balance = :bal,"
                    " updated_at = now() WHERE customer_id::text = :cid",
                ), {"bal": balance_after, "cid": str(user_id)})
            conn.execute(text(
                "INSERT INTO finance.wallet_ledger"
                " (customer_id, direction, amount, balance_after, kind, memo)"
                " VALUES (:cid, 'in', :amt, :after, 'topup', '演示充值')",
            ), {"cid": int(user_id), "amt": value, "after": balance_after})
        return {"balance": float(balance_after), "currency": "CNY"}

    # ── 订单 ─────────────────────────────────────────────────────

    def create_order(
        self, user_id: str, tenant_id: str,
        items: list[dict[str, Any]], operation_key: str,
    ) -> dict[str, Any]:
        if not items:
            raise MallError("invalid_items", "购物车为空，无法结算。")
        # 服务端计价：价格一律取库内 sale_price，不信任调用方
        priced: list[dict[str, Any]] = []
        total = _ZERO
        for item in items:
            product = self.get_product(str(item.get("product_id", "")))
            if product is None:
                raise MallError("invalid_items", "包含不可购买的商品，请刷新购物车。")
            quantity = int(item.get("quantity", 0))
            if not 1 <= quantity <= 99:
                raise MallError("invalid_items", "商品数量需在 1-99 之间。")
            price = _dec(product["sale_price"])
            total += price * quantity
            priced.append({**product, "quantity": quantity, "price": price})
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import post_json_sync

                data = _unwrap(post_json_sync("/business/orders", {
                    "user_id": str(user_id), "tenant_id": str(tenant_id),
                    "items": [
                        {"product_id": p["product_id"],
                         "product_name": p["product_name"],
                         "quantity": p["quantity"], "price": float(p["price"])}
                        for p in priced
                    ],
                    "operation_key": operation_key,
                }))
                return data["order"]
            except MallError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._create_order_local(user_id, priced, total)

    def _create_order_local(
        self, user_id: str, priced: list[dict[str, Any]], total: Decimal,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        order_no = f"WEB-{uuid.uuid4().hex[:10].upper()}"
        with get_business_engine().begin() as conn:
            order_row = conn.execute(text(
                'INSERT INTO "order".orders'
                " (order_no, customer_id, total_amount, status, payment_status)"
                " VALUES (:no, :cid, :amt, 'pending', 'unpaid') RETURNING id",
            ), {"no": order_no, "cid": int(user_id), "amt": total}).first()
            for p in priced:
                conn.execute(text(
                    'INSERT INTO "order".order_items'
                    " (order_id, product_id, quantity, price)"
                    " VALUES (:oid, :pid, :qty, :price)",
                ), {"oid": order_row[0], "pid": int(p["product_id"]),
                    "qty": p["quantity"], "price": p["price"]})
        return {
            "order_no": order_no, "status": "pending",
            "payment_status": "unpaid", "total_amount": float(total),
            "product_names": "；".join(p["product_name"] for p in priced),
        }

    def _fetch_order(
        self, conn: Any, user_id: str, order_no: str, *,
        for_update: bool = False,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        lock = " FOR UPDATE" if for_update else ""
        row = conn.execute(text(
            'SELECT id::text, order_no, customer_id::text AS customer_id,'
            " total_amount, status, payment_status"
            f' FROM "order".orders WHERE order_no = :no{lock}',
        ), {"no": order_no}).mappings().first()
        if row is None or row["customer_id"] != str(user_id):
            raise MallError("not_found", "订单不存在或无权操作。")
        return dict(row)

    def pay_order(
        self, user_id: str, tenant_id: str, order_no: str,
        expected_amount: float, operation_key: str,
    ) -> dict[str, Any]:
        expected = _dec(expected_amount)
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import post_json_sync

                data = _unwrap(post_json_sync(f"/business/orders/{order_no}/pay", {
                    "user_id": str(user_id), "tenant_id": str(tenant_id),
                    "expected_amount": float(expected),
                    "operation_key": operation_key,
                }))
                return {"order": data["order"], "wallet": data["wallet"]}
            except MallError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._pay_order_local(user_id, order_no, expected)

    def _pay_order_local(
        self, user_id: str, order_no: str, expected: Decimal,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        with get_business_engine().begin() as conn:
            order = self._fetch_order(conn, user_id, order_no, for_update=True)
            if (order["status"], order["payment_status"]) != _PAYABLE:
                raise MallError("conflict", "订单当前状态不可支付。")
            if _dec(order["total_amount"]) != expected:
                raise MallError("conflict", "订单金额已变化，请刷新后重试。")
            wallet = conn.execute(text(
                "SELECT balance FROM finance.wallet_balances"
                " WHERE customer_id::text = :cid FOR UPDATE",
            ), {"cid": str(user_id)}).first()
            balance = _dec(wallet[0]) if wallet else _ZERO
            if balance < expected:
                raise MallError(
                    "insufficient_balance", "余额不足",
                    "账户余额不足，请先充值后再支付。",
                )
            balance_after = balance - expected
            if wallet is None:
                conn.execute(text(
                    "INSERT INTO finance.wallet_balances (customer_id, balance)"
                    " VALUES (:cid, :bal)",
                ), {"cid": int(user_id), "bal": balance_after})
            else:
                conn.execute(text(
                    "UPDATE finance.wallet_balances SET balance = :bal,"
                    " updated_at = now() WHERE customer_id::text = :cid",
                ), {"bal": balance_after, "cid": str(user_id)})
            conn.execute(text(
                "INSERT INTO finance.wallet_ledger"
                " (customer_id, direction, amount, balance_after, kind, ref_no, memo)"
                " VALUES (:cid, 'out', :amt, :after, 'pay', :no, :memo)",
            ), {"cid": int(user_id), "amt": expected, "after": balance_after,
                "no": order_no, "memo": f"订单支付 {order_no}"})
            conn.execute(text(
                'UPDATE "order".orders SET status = \'paid\','
                " payment_status = 'paid' WHERE order_no = :no",
            ), {"no": order_no})
        return {
            "order": {**order, "status": "paid", "payment_status": "paid"},
            "wallet": {"balance": float(balance_after), "currency": "CNY"},
        }

    def get_logistics(
        self, user_id: str, tenant_id: str, order_no: str,
    ) -> dict[str, Any]:
        """物流轨迹：http 走业务契约；sandbox 按订单状态派生演示轨迹。"""
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import get_json_sync

                data = _unwrap(get_json_sync(
                    f"/business/orders/{order_no}/logistics",
                    params={"user_id": str(user_id)},
                ))
                return data["logistics"]
            except MallError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc

        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        with get_business_engine().connect() as conn:
            row = conn.execute(text(
                'SELECT order_no, status, created_at FROM "order".orders'
                " WHERE order_no = :no AND customer_id::text = :cid",
            ), {"no": order_no, "cid": str(user_id)}).mappings().first()
        if row is None:
            raise MallError("not_found", "订单不存在或无权操作。")
        status = str(row["status"])
        created = row["created_at"]
        base = {
            "order_no": row["order_no"],
            "carrier": None,
            "tracking_no": None,
            "estimated_delivery": None,
        }
        if status == "pending":
            return {
                **base,
                "shipping_status": "not_shipped",
                "status_display": "待付款，尚未发货",
                "tracking_info": "订单待付款，付款后仓库开始拣货发货。",
                "events": [],
            }
        if status in ("cancelled", "refunded"):
            return {
                **base,
                "shipping_status": "cancelled" if status == "cancelled" else "delivered",
                "status_display": "已取消" if status == "cancelled" else "已退款",
                "tracking_info": (
                    "订单已取消，不涉及物流配送。"
                    if status == "cancelled"
                    else "订单已退款，退款金额已退回账户余额。"
                ),
                "events": [],
            }
        events = [
            {"time": str(created), "location": "商家仓库", "description": "包裹已揽收"},
        ]
        if status in ("shipped", "completed"):
            events.append({
                "time": str(created),
                "location": "上海转运中心",
                "description": "包裹运输中",
            })
        if status == "completed":
            events.append({
                "time": str(created),
                "location": "收货地址",
                "description": "包裹已签收",
            })
        return {
            **base,
            "shipping_status": "delivered" if status == "completed" else "shipped",
            "status_display": "已签收" if status == "completed" else "已发货，运输中",
            "carrier": "演示速运",
            "tracking_no": f"TRK{order_no[-8:]}",
            "tracking_info": (
                "您的包裹已签收。" if status == "completed" else "您的包裹正在运输中。"
            ),
            "events": events,
        }

    def cancel_order(
        self, user_id: str, tenant_id: str, order_no: str, *,
        reason: str = "", operation_key: str, via_confirmation: bool = False,
        expected_status: str | None = None,
    ) -> dict[str, Any]:
        """取消订单。

        - pending（未付款）：直接取消，无资金变动（via_confirmation 亦可，
          但用户端对未付款单走直接取消，不占确认流）；
        - paid（已支付待发货）：必须经客服提案-确认（via_confirmation=True），
          取消并全额退款回钱包。

        expected_status 由调用方声明预期状态：直取消通道只接受 'pending'，
        确认执行通道只接受 'paid'——防止直取消端点越权取消已支付订单。
        """
        if via_confirmation:
            expected_status = expected_status or "paid"
        else:
            expected_status = expected_status or "pending"
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import (
                    get_json_sync,
                    post_json_sync,
                )

                order = _unwrap(get_json_sync(
                    "/business/orders/{order_no}".format(order_no=order_no),
                    params={"user_id": str(user_id)},
                ))["order"]
                if order["status"] != expected_status:
                    raise MallError("conflict", "订单当前状态不可取消。")
                data = _unwrap(post_json_sync(
                    f"/business/orders/{order_no}/cancel",
                    {
                        "user_id": str(user_id), "tenant_id": str(tenant_id),
                        "expected_status": expected_status,
                        "expected_amount": float(_dec(order["total_amount"])),
                        "reason": reason, "operation_key": operation_key,
                    },
                ))
                return {"order": data["order"], "wallet": data["wallet"]}
            except MallError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._cancel_order_local(
            user_id, order_no, via_confirmation=via_confirmation,
            expected_status=expected_status,
        )

    def _cancel_order_local(
        self, user_id: str, order_no: str, *, via_confirmation: bool,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        with get_business_engine().begin() as conn:
            order = self._fetch_order(conn, user_id, order_no, for_update=True)
            status = order["status"]
            payment = order["payment_status"]
            amount = _dec(order["total_amount"])
            if status != expected_status:
                raise MallError("conflict", "订单当前状态不可取消。")
            refund = False
            if (status, payment) == _CANCEL_UNPAID:
                refund = False
            elif via_confirmation and (status, payment) == _CANCEL_PAID:
                refund = True
            else:
                raise MallError("conflict", "订单当前状态不可取消。")
            wallet = conn.execute(text(
                "SELECT balance FROM finance.wallet_balances"
                " WHERE customer_id::text = :cid FOR UPDATE",
            ), {"cid": str(user_id)}).first()
            balance = _dec(wallet[0]) if wallet else _ZERO
            balance_after = balance
            if refund:
                balance_after = balance + amount
                if wallet is None:
                    conn.execute(text(
                        "INSERT INTO finance.wallet_balances (customer_id, balance)"
                        " VALUES (:cid, :bal)",
                    ), {"cid": int(user_id), "bal": balance_after})
                else:
                    conn.execute(text(
                        "UPDATE finance.wallet_balances SET balance = :bal,"
                        " updated_at = now() WHERE customer_id::text = :cid",
                    ), {"bal": balance_after, "cid": str(user_id)})
                conn.execute(text(
                    "INSERT INTO finance.wallet_ledger"
                    " (customer_id, direction, amount, balance_after, kind, ref_no, memo)"
                    " VALUES (:cid, 'in', :amt, :after, 'cancel_refund', :no, :memo)",
                ), {"cid": int(user_id), "amt": amount, "after": balance_after,
                    "no": order_no, "memo": f"订单取消退款 {order_no}"})
            new_payment = "refunded" if refund else "unpaid"
            conn.execute(text(
                'UPDATE "order".orders SET status = \'cancelled\','
                " payment_status = :payment WHERE order_no = :no",
            ), {"payment": new_payment, "no": order_no})
        return {
            "order": {
                **order, "status": "cancelled",
                "payment_status": "refunded" if refund else "unpaid",
            },
            "wallet": {"balance": float(balance_after), "currency": "CNY"},
        }

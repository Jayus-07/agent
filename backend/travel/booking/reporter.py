"""travel/booking/reporter.py — Booking 结果渲染（STOP L7/L8，G37 口径）

真实状态话术（§三十七/§三十八）：
  允许：正在提交预订 / 预订成功 / 预订失败 / 供应商结果尚无法确认 /
        价格发生变化，需要重新确认 / 报价已过期 / 库存已变化
  IN_DOUBT 禁止写成失败或成功——推荐表达原样落地。
  Fake 模式必须显式「模拟交易」。
"""
from __future__ import annotations

from backend.travel.booking.executor import (
    ALREADY_BOOKED,
    BOOKED,
    CONFIRMATION_EXPIRED,
    FAILED,
    IN_DOUBT,
    IN_PROGRESS,
    NOT_CONFIRMABLE,
    OFFER_GONE,
    PRICE_CHANGED,
    REVALIDATION_UNAVAILABLE,
    SOLD_OUT,
)
from backend.travel.booking.executor import ExecutionOutcome

FORBIDDEN_PHRASES = (
    "出票成功", "房间已锁定", "扣款成功", "已锁价", "价格保证",
    "确认购买成功",
)


def render_quote_confirmation(quote: dict, offer_summary: dict,
                              order: dict) -> str:
    """Quote + 待确认卡片（§十：系统 TTL ≠ 供应商锁价）。"""
    expires = str(quote["internal_expires_at"])
    provider_exp = quote.get("provider_expires_at")
    provider_note = (
        f"供应商标注价格有效期至：{provider_exp}" if provider_exp
        else "当前查询价格，提交预订前会再次核验（供应商未承诺锁价）。")
    facts = quote["booking_facts"]
    if quote["commerce_type"] == "hotel":
        head = (f"### 预订确认（{facts.get('city', '')}，"
                f"{facts.get('check_in', '')} 入住 {facts.get('check_out', '')}，"
                f"{quote['nights'] if 'nights' in quote else ''}"
                f"{offer_summary.get('name', '')}）\n\n")
    else:
        head = (f"### 预订确认（{facts.get('origin', '')} → "
                f"{facts.get('destination', '')}，"
                f"{facts.get('departure_date', '')}）\n\n")
    return (
        head
        + f"- 金额：**{order['amount']} {order['currency']}**"
          f"（税费口径：{_tax_wording(quote)}）\n"
        + f"- 价格观测时间：{offer_summary.get('observed_at', '')}\n"
        + f"- {provider_note}\n"
        + f"- 确认有效期至：{expires}（系统确认时限，非供应商锁价）\n\n"
        + "请回复「**确认预订**」以提交；提交前系统会再次核验价格与库存，"
          "若价格变化会要求重新确认。"
    )


def _tax_wording(quote: dict) -> str:
    if quote.get("taxes") is None:
        return "税费未知，以供应商页面为准"
    return f"税费 {quote['taxes']} {quote['currency']}"


def render_execution_outcome(outcome: ExecutionOutcome,
                             *, provider_mode: str = "off") -> str:
    result = outcome.result
    order = outcome.order or {}
    fake_note = ("（当前为**模拟交易**测试数据源，非真实预订。）\n\n"
                 if provider_mode.startswith("fake_booking") else "")
    if result == BOOKED:
        return (fake_note
                + f"### 预订成功\n\n- 订单号：`{order.get('merchant_order_id', '')}`\n"
                  f"- 供应商订单：`{order.get('provider_order_id') or '待回执'}`\n"
                  f"- 金额：{order.get('amount')} {order.get('currency')}\n\n"
                  "以上为供应商确认结果。")
    if result == ALREADY_BOOKED:
        return (fake_note + "该订单已预订成功，无需重复提交。")
    if result == IN_DOUBT:
        # §三十七推荐表达：既不是失败也不是成功
        return (fake_note
                + "### 供应商端结果暂时无法确认\n\n"
                  "供应商端结果暂时无法确认，系统不会自动重复提交该预订，"
                  "正在等待状态核实。\n\n"
                  f"- 订单号：`{order.get('merchant_order_id', '')}`\n"
                  "- 我们会通过对账核实结果后更新状态；在此期间请勿重新下单。")
    if result == PRICE_CHANGED:
        new_q = outcome.new_quote
        tail = ""
        if new_q:
            tail = (f"\n\n已生成新报价（{new_q['price_amount']} "
                    f"{new_q['currency']}），请核验后重新确认。")
        return ("### 价格发生变化，需要重新确认\n\n"
                f"提交预订时供应商价格已变化：{outcome.detail}。"
                "原确认已失效，未执行预订。"
                + tail)
    if result == SOLD_OUT:
        return "### 库存已变化\n\n供应商返回该 offer 已订满，未执行预订，未产生扣款。"
    if result == OFFER_GONE:
        return "### 库存已变化\n\n该 offer 已不在供应商结果中，未执行预订。"
    if result == REVALIDATION_UNAVAILABLE:
        return ("### 暂时无法核验库存\n\n提交前核验时供应商服务不可用，"
                "为避免误订已停止执行。请稍后重试。")
    if result == CONFIRMATION_EXPIRED:
        return "### 报价已过期\n\n确认时限已到，该订单已关闭。请重新查询并下单。"
    if result == FAILED:
        code = order.get("failure_code") or ""
        reason = {
            "REJECTED": "供应商拒绝了该预订请求。",
            "NOT_SENT": "请求未能送达供应商（未产生预订），可重新发起。",
        }.get(code, f"预订未能完成（{code or 'UNKNOWN'}）。")
        return (f"### 预订失败\n\n{reason}\n\n"
                f"- 订单号：`{order.get('merchant_order_id', '')}`\n"
                "未产生成功预订。")
    if result == IN_PROGRESS:
        return "正在提交预订，请稍后查询状态。"
    if result == NOT_CONFIRMABLE:
        return f"当前没有可提交的预订。{outcome.detail or ''}"
    return f"预订状态：{result}。"


def render_status(report: dict | None) -> str:
    if report is None:
        return "目前没有预订记录。"
    order = report["order"]
    status = order["status"]
    wording = {
        "awaiting_confirmation": "待确认（请回复「确认预订」提交）",
        "confirmed": "已确认，正在提交",
        "submitting": "正在提交预订",
        "booked": "预订成功",
        "failed": f"预订失败（{order.get('failure_code') or '未知原因'}）",
        "in_doubt": ("供应商端结果暂时无法确认，系统不会自动重复提交该预订，"
                     "正在等待状态核实"),
        "expired": "报价已过期",
    }.get(status, status)
    return (f"### 预订状态\n\n- 订单号：`{order['merchant_order_id']}`\n"
            f"- 状态：{wording}\n"
            f"- 金额：{order['amount']} {order['currency']}\n")

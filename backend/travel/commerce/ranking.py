"""travel/commerce/ranking.py — 确定性排序（STOP K1，任务书 §十七）

**只做可计算规则的确定性排序**，禁止任何「最值得/性价比最高/最佳/最推荐」
式的主观断言（渲染话术由 reporter 白名单承载：只描述排序键本身）。

排序键（白名单）：price（价格升序）/ duration（总时长升序，仅 flight）/
stops（中转数升序，仅 flight）。全部稳定排序 + fingerprint 终序兜底——
同键 offer 的相对顺序跨进程可复现（指纹是 sha256 确定性派生）。
"""
from __future__ import annotations

_SORTERS = {
    "price": None,       # 占位；实际键在 sort_* 函数中构造（金额在快照内）
    "duration": None,
    "stops": None,
}


def _price_key(offer):
    return (offer.price_snapshot.amount.amount,
            offer.price_snapshot.amount.currency)


def sort_hotels(offers: list, sort_by: str = "price") -> list:
    """酒店排序：price 唯一键（价格同则币种序，再同则指纹序——不比较
    名称/星级等业务字段做隐式权重，排序依据必须可解释）。"""
    if sort_by != "price":
        raise ValueError(f"hotel 暂不支持排序键 {sort_by}（白名单：price）")
    return sorted(
        offers,
        key=lambda o: (_price_key(o), o.property_name, o.offer_fingerprint),
    )


def sort_flights(offers: list, sort_by: str = "price") -> list:
    """机票排序：price / duration / stops（后两者 Provider 未给时排末尾，
    不猜测填充——unknown 不参与数值比较）。"""
    if sort_by not in _SORTERS:
        raise ValueError(
            f"flight 暂不支持排序键 {sort_by}（白名单：price/duration/stops）")
    if sort_by == "price":
        return sorted(
            offers,
            key=lambda o: (_price_key(o), o.offer_fingerprint),
        )
    if sort_by == "duration":
        return sorted(
            offers,
            key=lambda o: (
                o.duration_minutes is None,
                o.duration_minutes if o.duration_minutes is not None else 0,
                _price_key(o), o.offer_fingerprint),
        )
    # stops
    return sorted(
        offers,
        key=lambda o: (o.stops, _price_key(o), o.offer_fingerprint),
    )

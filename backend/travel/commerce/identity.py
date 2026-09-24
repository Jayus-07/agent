"""travel/commerce/identity.py — Offer 指纹与快照 id（STOP K1，任务书 §十五）

跨进程稳定标识：**sha256，禁止 Python ``hash()``**（进程间 PYTHONHASHSEED
随机化，跨进程必不稳定）。字段拼接用 ``|`` 分隔 + 全字段空串归一，防止
字段值本身含 ``|`` 造成的歧义（值内 ``|`` 替换为 ``/``）。

Hotel 至少结合：provider/property_id/**rate 标识**/checkin/checkout/occupancy
——rate 标识缺失时退回 room_type，并在指纹域里显式带 ``room_type:`` 前缀，
两种来源不共享同一命名空间，避免把不同 rate plan 错误合并（§十五）。

Flight 至少结合：provider/segments(carrier+flight_number+departure_at)/
cabin/fare 标识。
"""
from __future__ import annotations

import hashlib


def _canon(value: object) -> str:
    """指纹字段归一：去空白、小写、分隔符转义。"""
    return str(value if value is not None else "").strip().lower().replace("|", "/")


def _sha(prefix: str, parts: list[str]) -> str:
    blob = "|".join(parts)
    return f"{prefix}_{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:32]}"


def hotel_fingerprint(
    *, provider: str, property_id: str, rate_identifier: str,
    check_in: str, check_out: str, adults: int, children: int, rooms: int,
) -> str:
    """酒店报价指纹（同 property 同 rate 同入住条件 = 同一 offer）。

    rate_identifier 由调用方组装：有 provider_offer_id 用 ``offer:<id>``，
    否则用 ``room_type:<room_type>``——两种来源前缀不同，绝不混同。
    """
    return _sha("ho", [
        _canon(provider), _canon(property_id), _canon(rate_identifier),
        _canon(check_in), _canon(check_out),
        str(int(adults)), str(int(children)), str(int(rooms)),
    ])


def flight_fingerprint(
    *, provider: str, provider_offer_id: str | None,
    segment_keys: list[str], cabin: str | None,
) -> str:
    """机票报价指纹（同航班组合同舱位同 fare = 同一 offer）。

    segment_keys 每段为 ``carrier:flight_number:departure_at``（由服务层
    从已校验 segments 组装）；provider_offer_id 是 fare 标识，缺失时指纹
    只含航班+舱位（同舱位不同 fare 的区分退化为 Provider id 缺失的已知
    局限，如实记录不伪造区分）。
    """
    return _sha("fl", [
        _canon(provider),
        _canon(provider_offer_id if provider_offer_id else ""),
        *(_canon(k) for k in segment_keys),
        _canon(cabin if cabin else ""),
    ])


def snapshot_id(offer_fingerprint: str, observed_at: str) -> str:
    """价格快照 id：offer 指纹 + 观测时间派生（确定性、可追溯、可复现）。"""
    return _sha("ps", [_canon(offer_fingerprint), _canon(observed_at)])

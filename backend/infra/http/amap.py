"""infra.http.amap — 高德开放平台 Web服务 API 统一客户端。

设计对齐 ``infra/http/tencent_lbs.py``（Key 服务端持有 / 超时 / 重试 /
缓存 / 节流 / 状态码语义），差异点均来自实测响应（2026-10-02，勿凭文档改）：

1. **status 是字符串不是整数**：成功 ``"status": "1"``，失败 ``"0"``；
   错误详情在 ``info``（英文标识，如 INVALID_USER_KEY）与 ``infocode``。
2. **HTTP 恒为 200**，不做 HTTP 状态码映射，全部按业务 infocode 走。
3. **坐标串是「经度,纬度」（lng,lat）**，与腾讯相反；本层不做换向，
   由 ``infra/lbs/amap.py`` 归一为平台口径 (lat, lng)。
4. 高德错误码的中文释义只挑了会改变调用方决策的几条；其余原样透传
   ``info``+``infocode``，不硬造翻译。

支持数字签名（``AMAP_SECRET`` 非空时自动启用；当前 Key 未开启该功能，
该路径未实测，启用时先跑 scripts 实网自检再信任）。
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from urllib.parse import quote

import httpx

from backend.config import map as MAP
from backend.infra.circuit_breaker import CircuitBreakerOpenError
from backend.shared.logger import logger

# ── 端点常量（实测可用路径）──────────────────────────────────
# 搜索POI 2.0：商家级字段（评分/人均/营业时间）靠 show_fields=business
EP_PLACE_TEXT = "/v5/place/text"

# ── 高德 infocode 语义表。只收录会改变调用方决策的码；
# message 会与高德原样返回的 info 拼接，翻译错了也不会误导排查。
_INFOCODE_MESSAGES: dict[str, str] = {
    "10001": "Key 无效或已过期（核对 AMAP_KEY，且须为「Web服务」类型）",
    "10002": "高德服务暂不可用",
    "10003": "触发 QPS/并发限制，请稍后重试",
    "10004": "当日调用量已超限",
    "10007": "Key 与服务类型不匹配（本接口要求「Web服务」类型 Key）",
    "10008": "IP 访问超限",
    "10009": "当日调用量已超限",
    "10020": "请求来源未被授权（Referer/IP 白名单）",
    "10021": "数字签名校验失败（核对 AMAP_SECRET）",
}

# 「再试也没用」的码：鉴权类。重试只会浪费配额。
_FATAL_INFOCODES = {"10001", "10007", "10020", "10021"}
# 配额/限流类：调用方可选择稍后重试或降级。
_QUOTA_INFOCODES = {"10003", "10004", "10008", "10009"}


class AmapError(Exception):
    """高德开放平台调用失败。

    Attributes:
        infocode: 高德业务错误码；"" 表示网络层失败（超时/连接被拒）。
        info: 高德原样返回的英文错误标识（如 INVALID_USER_KEY）。
    """

    def __init__(self, message: str, infocode: str = "", info: str = ""):
        super().__init__(message)
        self.infocode = str(infocode)
        self.info = info or ""

    @property
    def is_quota(self) -> bool:
        return self.infocode in _QUOTA_INFOCODES

    @property
    def is_fatal(self) -> bool:
        return self.infocode in _FATAL_INFOCODES


# ── 缓存：TTL + LRU（与 tencent_lbs 同款实现，键含全部请求参数）──
_cache: "OrderedDict[str, tuple[float, object]]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key: str) -> object | None:
    if not MAP.AMAP_CACHE_ENABLED:
        return None
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            return None
        expire_at, value = item
        if expire_at < time.time():
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return value


def _cache_put(key: str, value: object, ttl: float) -> None:
    if not MAP.AMAP_CACHE_ENABLED or ttl <= 0:
        return
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
        _cache.move_to_end(key)
        while len(_cache) > MAP.AMAP_CACHE_MAXSIZE:
            _cache.popitem(last=False)


def clear_cache() -> int:
    """清空缓存，返回被清除的条目数（供测试与运维使用）。"""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    return n


# ── 节流：同一 Key 的最小请求间隔，防止突发触发 10003 ──
_throttle_lock = threading.Lock()
_last_call_at = 0.0


def _throttle() -> None:
    global _last_call_at
    if MAP.AMAP_MIN_INTERVAL <= 0:
        return
    with _throttle_lock:
        wait = MAP.AMAP_MIN_INTERVAL - (time.time() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.time()


def _as_pairs(params: dict | None) -> list[tuple[str, str]]:
    """dict → 键值对列表，剔除 None 与空串（高德对空串参数会报 INVALID_PARAMS）。"""
    if not params:
        return []
    return [(str(k), str(v)) for k, v in params.items()
            if v is not None and str(v) != ""]


def _sign(path: str, pairs: list[tuple[str, str]]) -> str:
    """按高德数字签名规则计算 sig。

    规则：参数按 key 字典序升序 → 拼 ``k=v&k=v``（值**不做 URL 编码**，
    这是与腾讯 SN 的差异）→ 前置 path? → 追加私钥 → md5 小写。
    """
    query = "&".join(f"{k}={v}" for k, v in sorted(pairs, key=lambda p: p[0]))
    raw = f"{path}?{query}{MAP.AMAP_SECRET}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _prepare(params: dict | None) -> list[tuple[str, str]]:
    """注入鉴权参数并做前置校验，返回可直接交给 httpx 的键值对列表。"""
    if not MAP.is_amap_configured():
        raise AmapError(
            "高德开放平台未配置：请在 .env 设置 AMAP_KEY", infocode="10001"
        )
    pairs = _as_pairs(params)
    pairs.append(("key", MAP.AMAP_KEY))
    if MAP.AMAP_SECRET:
        # sig 在 key 之后计算，sig 自身不参与签名
        pairs.append(("sig", _sign(EP_PLACE_TEXT, pairs)))
    return pairs


def _cache_key(pairs: list[tuple[str, str]]) -> str:
    """缓存键：路径 + 除 key/sig 外的全部参数（排序后）。"""
    items = sorted((k, v) for k, v in pairs if k not in ("key", "sig"))
    return EP_PLACE_TEXT + "?" + "&".join(f"{k}={v}" for k, v in items)


def _parse(resp: httpx.Response) -> dict:
    """解析响应体并做业务状态映射，成功时返回解析后的 payload。"""
    try:
        payload = resp.json()
    except ValueError as e:
        # 哨兵 infocode "-1"：响应体不是合法 JSON（schema 失效）。
        # 重试无意义，直接抛，避免把第三方 schema 漂移放大成双倍无效请求。
        raise AmapError(
            f"响应不是合法 JSON（HTTP {resp.status_code}）", infocode="-1"
        ) from e

    status = str(payload.get("status", "0"))
    if status != "1":
        infocode = str(payload.get("infocode", ""))
        info = str(payload.get("info", ""))
        message = _INFOCODE_MESSAGES.get(infocode)
        merged = f"高德 API 错误 {infocode or '?'}: {info}"
        if message:
            merged += f"（{message}）"
        raise AmapError(merged, infocode=infocode, info=info)
    return payload


# ── 同步客户端（单例连接池）──
_sync_client: httpx.Client | None = None
_sync_lock = threading.Lock()

# ── 熔断器：与腾讯同理由 —— 网络持续失败时快速失败，交调用方降级 ──
_breaker = None
_breaker_lock = threading.Lock()


def _get_breaker():
    global _breaker
    if _breaker is None:
        with _breaker_lock:
            if _breaker is None:
                from backend.infra.circuit_breaker import CircuitBreaker

                _breaker = CircuitBreaker(
                    "amap",
                    fail_threshold=MAP.AMAP_BREAKER_THRESHOLD,
                    timeout=MAP.AMAP_BREAKER_COOLDOWN,
                )
    return _breaker


def _get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None:
        with _sync_lock:
            if _sync_client is None:
                _sync_client = httpx.Client(
                    timeout=httpx.Timeout(
                        connect=MAP.AMAP_CONNECT_TIMEOUT,
                        read=MAP.AMAP_READ_TIMEOUT,
                        write=MAP.AMAP_READ_TIMEOUT,
                        pool=MAP.AMAP_CONNECT_TIMEOUT,
                    ),
                    limits=httpx.Limits(
                        max_connections=10, max_keepalive_connections=5
                    ),
                )
    return _sync_client


def call_sync(path: str, params: dict | None = None,
              *, ttl: float | None = None) -> dict:
    """发起 Web服务 GET 请求（同步）并返回解析后的 payload。

    Args:
        path: 端点路径，如 ``EP_PLACE_TEXT``（签名按此路径计算）
        params: 查询参数；key/sig 由本函数注入，勿手动传
        ttl: 结果缓存秒数；None 用默认 TTL，0 不缓存

    Raises:
        AmapError: 未配置 Key、网络失败、或业务状态非 "1"。
    """
    merged = _prepare(params)
    ck = _cache_key(merged)
    cached = _cache_get(ck)
    if cached is not None:
        logger.debug("[AMap] 命中缓存 %s", ck)
        return cached  # type: ignore[return-value]

    url = f"{MAP.AMAP_HOST}{path}"
    client = _get_sync_client()
    attempts = (MAP.AMAP_RETRIES + 1) if MAP.AMAP_RETRIES >= 0 else 1
    effective_ttl = MAP.AMAP_CACHE_TTL if ttl is None else ttl
    breaker = _get_breaker()

    last_error: AmapError | None = None
    for attempt in range(1, attempts + 1):
        _throttle()
        try:
            resp = breaker.call(lambda: client.get(url, params=merged))
            payload = _parse(resp)
        except CircuitBreakerOpenError as e:
            # 熔断开路：不再等超时，把「已降级」事实交给调用方兜底
            raise AmapError(f"AMap 熔断开路（{e}）——调用方应走本地降级") from e
        except httpx.HTTPError as e:
            last_error = AmapError(f"网络错误: {e}")
            logger.warning("[AMap] %s %s (第 %d/%d 次)",
                           last_error, ck, attempt, attempts)
        except AmapError as e:
            last_error = e
            # 鉴权类错误与 schema 失效（infocode=-1）重试无意义，直接抛
            if e.is_fatal or e.infocode == "-1":
                raise
            logger.warning("[AMap] %s %s (第 %d/%d 次)", e, ck, attempt, attempts)

        if last_error is None:
            _cache_put(ck, payload, effective_ttl)
            return payload

        if attempt < attempts:
            time.sleep(MAP.AMAP_RETRY_BACKOFF)

    raise last_error  # type: ignore[misc]


def capability_report() -> dict:
    """高德接入自检信息（供 /api/map/health 与排障使用）。"""
    return {
        "configured": MAP.is_amap_configured(),
        "enabled": MAP.AMAP_ENABLED,
        "host": MAP.AMAP_HOST,
        "signature": bool(MAP.AMAP_SECRET),
        "cache_enabled": MAP.AMAP_CACHE_ENABLED,
        "min_interval_s": MAP.AMAP_MIN_INTERVAL,
        "default_region": MAP.AMAP_DEFAULT_REGION,
        "endpoints": [EP_PLACE_TEXT],
    }

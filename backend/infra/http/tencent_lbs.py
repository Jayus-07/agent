"""infra.http.tencent_lbs — 腾讯位置服务 WebService API 统一客户端。

设计对齐 ``infra/http/business_client.py``（单例连接池 + 异常统一映射），
并针对 LBS 的业务特性补三件事：

1. **Key 服务端持有。** 所有请求在此处注入 ``key``，上层与前端都不接触它。
2. **统一的状态码语义。** 腾讯 HTTP 恒为 200，错误藏在响应体的 ``status`` 里。
   若不做映射，``resp.json()["result"]`` 会在限流/无权限时抛 KeyError，
   把「配额用尽」误报成「代码 bug」。此处映射为 ``TencentLbsError``。
3. **节流 + 缓存。** 个人 Key 并发上限约 5 QPS、日调用量有限，
   客户端侧限速配 TTL 缓存，避免把配额烧在重复查询上。

支持 SN 签名校验（``TENCENT_LBS_SK`` 非空时自动启用）。
"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import OrderedDict
from urllib.parse import quote

import httpx

from backend.config import map as MAP
from backend.infra.circuit_breaker import CircuitBreakerOpenError
from backend.shared.logger import logger

# ── 端点常量（均为已实测可用的路径，改动请同步 scripts/verify_tencent_lbs.py）──
EP_IP_LOCATION = "/ws/location/v1/ip"
EP_GEOCODER = "/ws/geocoder/v1/"
EP_PLACE_SEARCH = "/ws/place/v1/search"
EP_PLACE_SUGGESTION = "/ws/place/v1/suggestion"
EP_DISTRICT_LIST = "/ws/district/v1/list"
EP_DISTRICT_SEARCH = "/ws/district/v1/search"
EP_DISTRICT_CHILDREN = "/ws/district/v1/getchildren"
EP_DIRECTION = "/ws/direction/v1/{mode}/"      # mode: driving|walking|bicycling|transit
EP_DISTANCE_MATRIX = "/ws/distance/v1/matrix"
EP_COORD_TRANSLATE = "/ws/coord/v1/translate"
EP_STATIC_MAP = "/ws/staticmap/v2/"            # 注意：v2 且必须带尾斜杠
# 天气（实测本 Key 可直接用，adcode 与 location 两种入参均可）
EP_WEATHER = "/ws/weather/v1/"
# 街景：路径已用 113（功能未授权）验证存在，非 404 —— 服务需单独申请配额
EP_STREETVIEW_PANO = "/ws/streetview/v1/getpano"
EP_STREETVIEW_IMAGE = "/ws/streetview/v1/image"
# URI API 地图调起：HTTP 302 跳转，不是 JSON 接口，走 build_uri_url 拼接
EP_URI_ROUTEPLAN = "/uri/v1/routeplan"
EP_URI_MARKER = "/uri/v1/marker"

# 需要单独申请开通的服务 → 给调用方的可执行指引。
# 与「控制台勾选即可」的 113 场景区分开：街景是邮件申请制，
# 只说「去控制台勾选」会把用户带进死胡同。
_SERVICE_APPLY_HINTS: dict[str, str] = {
    EP_STREETVIEW_IMAGE: (
        "街景服务需**单独申请**：腾讯仅对企业开发者开放，"
        "需按官方配额申请模板（邮件正文格式）发至 mapapi@vip.qq.com 并抄送 "
        "mapbd@tencent.com，约 3 个工作日审批。"
    ),
    EP_STREETVIEW_PANO: (
        "街景服务需**单独申请**：腾讯仅对企业开发者开放，"
        "需按官方配额申请模板（邮件正文格式）发至 mapapi@vip.qq.com 并抄送 "
        "mapbd@tencent.com，约 3 个工作日审批。"
    ),
}


def apply_hint_for(path: str) -> str:
    """该端点在「功能未授权」时的针对性指引（无则返回空串）。"""
    return _SERVICE_APPLY_HINTS.get(path, "")

# ── 状态码语义表。只收录会改变调用方决策的码，其余归入「其他错误」。
_STATUS_MESSAGES: dict[int, str] = {
    0: "成功",
    110: "请求来源未被授权（请检查 Key 的 Referer/IP 白名单）",
    111: "签名验证失败（请核对 TENCENT_LBS_SK）",
    112: "IP 未被授权",
    113: "该功能未被授权（请在控制台为 Key 勾选对应服务）",
    120: "该 Key 当日调用量已达上限",
    121: "该 Key 每秒请求量已达上限（限流）",
    122: "该 Key 使用过于频繁",
    123: "该 Key 已被暂时禁用",
    190: "无效的 Key",
    199: "该 Key 未开启 WebService API 功能",
    300: "请求参数错误",
    301: "Key 格式错误",
    302: "请求参数信息有误",
    303: "无结果（检索条件过窄）",
    306: "请求参数错误",
    310: "请求参数信息有误",
    311: "Key 格式错误",
    347: "无结果",
    348: "请求参数错误",
}

# 这些码属于「再试也没用」，调用方应直接降级而不是重试
_FATAL_STATUS = {110, 111, 112, 113, 190, 199, 301, 311}


class TencentLbsError(Exception):
    """腾讯位置服务调用失败。

    Attributes:
        status: 腾讯业务状态码；0 表示网络层失败（超时/连接被拒）。
        request_id: 腾讯返回的 request_id，提工单时必带，便于定位。
    """

    def __init__(self, message: str, status: int = 0, request_id: str = ""):
        super().__init__(message)
        self.status = status
        self.request_id = request_id

    @property
    def is_quota(self) -> bool:
        return self.status in (120, 121, 122, 123)

    @property
    def is_fatal(self) -> bool:
        return self.status in _FATAL_STATUS


# ── 缓存：TTL + LRU。用 OrderedDict 实现，避免为这点需求引入依赖。
# async 与 sync 共用同一份缓存：键里已含全部请求参数，二者结果同源。
_cache: "OrderedDict[str, tuple[float, object]]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key: str) -> object | None:
    if not MAP.TENCENT_LBS_CACHE_ENABLED:
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
    if not MAP.TENCENT_LBS_CACHE_ENABLED or ttl <= 0:
        return
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
        _cache.move_to_end(key)
        while len(_cache) > MAP.TENCENT_LBS_CACHE_MAXSIZE:
            _cache.popitem(last=False)


def clear_cache() -> int:
    """清空缓存，返回被清除的条目数（供测试与运维使用）。"""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    return n


# ── 节流：保证同一 Key 的最小请求间隔，防止突发把配额打成 121
_throttle_lock = threading.Lock()
_last_call_at = 0.0


def _throttle() -> None:
    global _last_call_at
    if MAP.TENCENT_LBS_MIN_INTERVAL <= 0:
        return
    with _throttle_lock:
        wait = MAP.TENCENT_LBS_MIN_INTERVAL - (time.time() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.time()


# ── 签名（SN 校验）
def _as_pairs(params: "dict | list[tuple[str, str]] | None") -> list[tuple[str, str]]:
    """归一为「键值对列表」。

    必须支持重复键：静态图接口的 ``markers`` 要求同键多值
    （每个 markers 参数定义一种图标样式），dict 无法表达。
    """
    if not params:
        return []
    items = params.items() if isinstance(params, dict) else params
    return [(str(k), str(v)) for k, v in items if v is not None and str(v) != ""]


def _sign(path: str, pairs: list[tuple[str, str]]) -> str:
    """按腾讯 SN 规则计算 sig。

    规则：参数按 key 字典序升序 → 拼成 ``k=v&k=v``（v 需 RFC3986 编码，
    字母数字与 ``-_.~`` 不编码，空格转 ``%20``）→ 前置 ``path?`` →
    追加 SK → 取 md5 小写。
    """
    query = "&".join(
        f"{k}={quote(str(v), safe='')}" for k, v in sorted(pairs, key=lambda p: p[0])
    )
    raw = f"{path}?{query}{MAP.TENCENT_LBS_SK}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _prepare(path: str, params: "dict | list[tuple[str, str]] | None") -> list[tuple[str, str]]:
    """注入鉴权参数并做前置校验，返回可直接交给 httpx 的键值对列表。"""
    if not MAP.is_configured():
        raise TencentLbsError(
            "腾讯位置服务未配置：请在 .env 设置 TENCENT_LBS_KEY", status=190
        )
    pairs = _as_pairs(params)
    pairs.append(("key", MAP.TENCENT_LBS_KEY))
    if MAP.TENCENT_LBS_SK:
        # sig 必须在 key 入参之后计算，且 sig 自身不参与签名
        pairs.append(("sig", _sign(path, pairs)))
    return pairs


def _cache_key(path: str, pairs: list[tuple[str, str]]) -> str:
    """缓存键：路径 + 除 key/sig 外的全部参数（排序后，保留重复键）。"""
    items = sorted(
        (k, v) for k, v in pairs if k not in ("key", "sig")
    )
    return path + "?" + "&".join(f"{k}={v}" for k, v in items)


def _parse(resp: httpx.Response, path: str = "") -> dict:
    """解析响应体并做业务状态码映射，成功时返回解析后的 payload。"""
    try:
        payload = resp.json()
    except ValueError as e:
        raise TencentLbsError(
            f"响应不是合法 JSON（HTTP {resp.status_code}）", status=0
        ) from e

    status = int(payload.get("status", 0))
    if status != 0:
        message = payload.get("message") or _STATUS_MESSAGES.get(status, "未知错误")
        merged = f"腾讯 LBS 错误 {status}: {message}"
        # 113 有两种成因：控制台没勾服务（勾一下即可），或该服务是申请制。
        # 申请制的不给出申请路径，用户会一直在控制台里找那个不存在的开关 ——
        # 所以此时用申请指引**替代**泛化的「去控制台勾选」建议，避免两句话打架。
        hint = apply_hint_for(path) if status == 113 else ""
        if hint:
            merged += f"。{hint}"
        elif _STATUS_MESSAGES.get(status):
            merged += f"（{_STATUS_MESSAGES[status]}）"
        raise TencentLbsError(
            merged, status=status, request_id=payload.get("request_id", "")
        )
    return payload


# ── 同步客户端
_sync_client: httpx.Client | None = None


# ── 熔断器（2026-09-15）：网络持续失败时快速失败，交调用方本地兜底 ──
_lbs_breaker = None
_lbs_breaker_lock = threading.Lock()


def _get_lbs_breaker():
    """LBS 专用熔断器（懒加载单例，参数走 config/map）。

    fail_threshold 次连续失败 → 开路 cooldown 秒；期间调用立即抛
    CircuitBreakerOpenError，由 call_sync 转成 TencentLbsError，
    调用方（travel POI 解析 / 路线估算）随即走本地估算兜底。
    """
    global _lbs_breaker
    if _lbs_breaker is None:
        with _lbs_breaker_lock:
            if _lbs_breaker is None:
                from backend.infra.circuit_breaker import CircuitBreaker

                _lbs_breaker = CircuitBreaker(
                    "tencent-lbs",
                    fail_threshold=MAP.TENCENT_LBS_BREAKER_THRESHOLD,
                    timeout=MAP.TENCENT_LBS_BREAKER_COOLDOWN,
                )
    return _lbs_breaker
_sync_lock = threading.Lock()


def _get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None:
        with _sync_lock:
            if _sync_client is None:
                _sync_client = httpx.Client(
                    timeout=httpx.Timeout(
                        connect=MAP.TENCENT_LBS_CONNECT_TIMEOUT,
                        read=MAP.TENCENT_LBS_READ_TIMEOUT,
                        write=MAP.TENCENT_LBS_READ_TIMEOUT,
                        pool=MAP.TENCENT_LBS_CONNECT_TIMEOUT,
                    ),
                    limits=httpx.Limits(
                        max_connections=10, max_keepalive_connections=5
                    ),
                )
    return _sync_client


def call_sync(path: str, params: "dict | list[tuple[str, str]] | None" = None,
              *, ttl: float | None = None) -> dict:
    """发起 WebService GET 请求（同步）并返回解析后的 payload。

    Args:
        path: 端点路径，如 ``EP_GEOCODER``
        params: 查询参数，dict 或 (键, 值) 列表（后者用于 markers 这类重复键）；
            key/sig 由本函数注入，勿手动传
        ttl: 结果缓存秒数；None 表示用默认 TTL，0 表示不缓存

    Raises:
        TencentLbsError: 未配置 Key、网络失败、或业务状态码非 0。
    """
    merged = _prepare(path, params)
    ck = _cache_key(path, merged)
    cached = _cache_get(ck)
    if cached is not None:
        logger.debug("[TencentLBS] 命中缓存 %s", ck)
        return cached  # type: ignore[return-value]

    url = f"{MAP.TENCENT_LBS_HOST}{path}"
    client = _get_sync_client()
    attempts = (MAP.TENCENT_LBS_RETRIES + 1) if MAP.TENCENT_LBS_RETRIES >= 0 else 1
    effective_ttl = MAP.TENCENT_LBS_CACHE_TTL if ttl is None else ttl

    # ── 熔断快速失败（2026-09-15）───────────────────────────────
    # 背景：网络抖动/LBS 侧不可达时，每次调用都要等满 connect+read 超时
    # （3s+8s）× 重试；而一次行程规划会发起多次调用（POI 解析/逐段路线），
    # 实测把首次请求拖到 6 分钟。熔断开路后立即失败 → 调用方既有的本地
    # 估算兜底接管，延迟与正确性都不再被网络拖累。
    breaker = _get_lbs_breaker()

    last_error: TencentLbsError | None = None
    for attempt in range(1, attempts + 1):
        _throttle()
        try:
            resp = breaker.call(lambda: client.get(url, params=merged))
            payload = _parse(resp, path)
        except CircuitBreakerOpenError as e:
            # 熔断开路：不再等超时，直接把"已降级"事实交给调用方兜底
            raise TencentLbsError(
                f"TencentLBS 熔断开路（{e}）——本次降级为本地估算", status=0
            ) from e
        except httpx.HTTPError as e:
            last_error = TencentLbsError(f"网络错误: {e}", status=0)
            logger.warning("[TencentLBS] %s %s (第 %d/%d 次)",
                           last_error, ck, attempt, attempts)
        except TencentLbsError as e:
            last_error = e
            # 鉴权类错误重试无意义，直接抛出，避免浪费配额
            if e.is_fatal:
                raise
            logger.warning("[TencentLBS] %s %s (第 %d/%d 次)", e, ck, attempt, attempts)

        if last_error is None:
            _cache_put(ck, payload, effective_ttl)
            return payload

        if attempt < attempts:
            time.sleep(MAP.TENCENT_LBS_RETRY_BACKOFF)

    raise last_error  # type: ignore[misc]


def call_bytes_sync(path: str, params: "dict | list[tuple[str, str]] | None" = None) -> bytes:
    """请求返回二进制体的端点（目前仅静态图）。

    静态图失败时同样返回 JSON 错误体，因此需要检查 content-type。
    """
    merged = _prepare(path, params)
    url = f"{MAP.TENCENT_LBS_HOST}{path}"
    _throttle()
    try:
        resp = _get_sync_client().get(url, params=merged)
    except httpx.HTTPError as e:
        raise TencentLbsError(f"网络错误: {e}", status=0) from e

    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("image/"):
        return resp.content
    # 出错时腾讯以 JSON + HTTP 200 返回
    try:
        payload = resp.json()
        raise TencentLbsError(
            f"腾讯 LBS 错误 {payload.get('status')}: {payload.get('message')}",
            status=int(payload.get("status", 0)),
            request_id=payload.get("request_id", ""),
        )
    except ValueError:
        raise TencentLbsError(
            f"静态图返回异常内容（HTTP {resp.status_code}, {ctype}）", status=0
        ) from None


# ── URI API（地图调起）：不是 JSON 接口，是「拼一个链接让浏览器打开」。
def build_uri_url(path: str, params: "dict | list[tuple[str, str]] | None" = None,
                  *, key: str) -> str:
    """拼接 URI API 调起链接。

    **返回的 URL 里含 Key**，这是 URI API 的固有形态（腾讯实测会把传入的 Key
    原样带到 302 跳转目标上，后端代理也藏不住）。因此：

      · 服务端自用时，传后端 Key（``MAP.TENCENT_LBS_KEY``）没问题；
      · 要交给浏览器时必须传**前端专用 Key**（``TENCENT_LBS_FRONTEND_KEY``），
        且该 Key 已在腾讯控制台配好 Referer 域名白名单。
    """
    pairs = _as_pairs(params)
    pairs.append(("referer", MAP.TENCENT_LBS_REFERER))
    pairs.append(("key", key))
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in pairs)
    return f"{MAP.TENCENT_LBS_HOST}{path}?{query}"


# ── 异步客户端
def call_async(path: str, params: dict | None = None, *, ttl: float | None = None) -> dict:
    """``call_sync`` 的异步封装。

    直接复用同步实现并通过 ``asyncio.to_thread`` 卸载：
    LangChain Tool 与 FastAPI 路由都可能调用，同步路径已是主路径，
    再维护一套 AsyncClient 单例会引入跨事件循环复用连接的风险
    （见 business_client.py 对该问题的注释），收益不抵复杂度。
    """
    return asyncio.to_thread(call_sync, path, params, ttl=ttl)


def safe_call(path: str, params: dict | None = None, *, ttl: float | None = None) -> dict | None:
    """容错版调用：任何 ``TencentLbsError`` 都吞掉并返回 None。

    供「有更好、没有也能跑」的增强路径使用（如旅行域通勤估算），
    调用方拿不到结果时自行降级，不必到处写 try/except。
    """
    try:
        return call_sync(path, params, ttl=ttl)
    except TencentLbsError as e:
        logger.info("[TencentLBS] 降级（%s）: %s", path, e)
        return None


# ── 便捷取字段：绝大多数调用方只关心 payload["result"]
def result_of(payload: dict | None) -> dict | None:
    """从 payload 中取出 result 段；payload 为 None 时返回 None。

    腾讯部分端点把数据放在 ``data``（检索类）而非 ``result``，
    调用方需要什么就取什么 —— 这里只做 None 安全，不做字段搬运。
    """
    if payload is None:
        return None
    if "result" in payload:
        return payload["result"]
    return payload

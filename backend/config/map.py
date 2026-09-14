"""config/map.py — 腾讯位置服务（LBS）配置

三条纪律：

1. **Key 只从环境变量读取，代码内不落任何默认值。** 缺失时
   ``TENCENT_LBS_KEY == ""``，由服务层整体降级（返回空结果 / 走本地
   估算），而不是抛异常打断主链路 —— 地图是增强能力，不是硬依赖。

2. **前端永不接触 Key。** 浏览器侧一律调用 ``/api/map/*`` 后端代理，
   WebService 的鉴权发生在服务端。前端若要渲染交互式地图，需在腾讯
   控制台给该 Key 配置 Referer 域名白名单；商用场景应走密钥代理模式。

3. **坐标系口径为 GCJ-02（火星坐标）。** 腾讯位置服务全线使用 GCJ-02。
   上游若为 WGS-84（GPS 原始值）或 BD-09（百度），必须先经
   ``/ws/coord/v1/translate`` 转换，否则会出现数百米级偏移。
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# =============================================
# 鉴权
# =============================================
# WebService API 开发者密钥（6 段式，见 .env 的 TENCENT_LBS_KEY）
TENCENT_LBS_KEY = os.getenv("TENCENT_LBS_KEY", "").strip()
# 签名密钥（SK）。仅当控制台开启了「签名校验(SN)」时才需要；
# 开启后请求必须附带 sig=md5(path?排序后的查询串+SK)。
TENCENT_LBS_SK = os.getenv("TENCENT_LBS_SK", "").strip()

# ── 前端专用 Key（可选，但强烈建议配）────────────────────────
# 只用于「Key 必须出现在浏览器 URL 里」的两类能力：
#   · URI API 地图调起（/uri/v1/*）—— 腾讯的设计就是让浏览器打开带 Key 的链接，
#     实测其 302 跳转地址里带的就是你传进去的 Key，无法用后端代理藏住
#   · JS API GL JS 渲染交互式地图
# 因此应当**与后端 Key 分开申请**，并在腾讯控制台给它配 Referer 域名白名单：
# 这样即使被嗅探，泄露面也被限制为「只能在你的域名下调用」，
# 后端 WebService 的日配额不会被盗用。
# 留空时调起功能回退用后端 Key，并在响应里带显著告警。
TENCENT_LBS_FRONTEND_KEY = os.getenv("TENCENT_LBS_FRONTEND_KEY", "").strip()
# URI API 的 referer 标识（腾讯要求必填，用于统计与防滥用）
TENCENT_LBS_REFERER = os.getenv("TENCENT_LBS_REFERER", "agent-platform").strip()

# 总开关。默认 true，但实际生效条件为 enabled and key 非空，
# 因此「没配 Key」等价于「未启用」，无需额外改开关。
TENCENT_LBS_ENABLED = _bool("TENCENT_LBS_ENABLED", True)

# =============================================
# 传输层
# =============================================
TENCENT_LBS_HOST = os.getenv("TENCENT_LBS_HOST", "https://apis.map.qq.com").rstrip("/")
TENCENT_LBS_CONNECT_TIMEOUT = float(os.getenv("TENCENT_LBS_CONNECT_TIMEOUT", "3"))
TENCENT_LBS_READ_TIMEOUT = float(os.getenv("TENCENT_LBS_READ_TIMEOUT", "8"))
# 幂等 GET 的网络层重试次数（不含首次）。腾讯个人 Key 并发上限约 5 QPS，
# 重试间隔设得比 429 的恢复窗口略长，避免把限流打成雪崩。
TENCENT_LBS_RETRIES = int(os.getenv("TENCENT_LBS_RETRIES", "1"))
TENCENT_LBS_RETRY_BACKOFF = float(os.getenv("TENCENT_LBS_RETRY_BACKOFF", "0.4"))
# 客户端节流：同一 Key 的最小请求间隔（秒），0 表示不限速。
# 默认 0.2s ≈ 5 QPS，与个人 Key 的并发限制对齐。
TENCENT_LBS_MIN_INTERVAL = float(os.getenv("TENCENT_LBS_MIN_INTERVAL", "0.2"))

# =============================================
# 结果缓存
# =============================================
# 地理编码 / 行政区划这类结果短期内不会变，缓存能显著省配额。
# 路线规划带实时路况，TTL 刻意给得短。
TENCENT_LBS_CACHE_ENABLED = _bool("TENCENT_LBS_CACHE_ENABLED", True)
TENCENT_LBS_CACHE_MAXSIZE = int(os.getenv("TENCENT_LBS_CACHE_MAXSIZE", "1024"))
TENCENT_LBS_CACHE_TTL = float(os.getenv("TENCENT_LBS_CACHE_TTL", "600"))
TENCENT_LBS_ROUTE_CACHE_TTL = float(os.getenv("TENCENT_LBS_ROUTE_CACHE_TTL", "120"))
# 天气：实时/逐小时变化快，TTL 取 5 分钟；未来预报变化慢，可长一些
TENCENT_LBS_WEATHER_TTL = float(os.getenv("TENCENT_LBS_WEATHER_TTL", "300"))
TENCENT_LBS_FORECAST_TTL = float(os.getenv("TENCENT_LBS_FORECAST_TTL", "1800"))

# =============================================
# 业务默认值
# =============================================
# 检索/提示的默认城市限定（省份级检索很容易跨城返回噪声结果）
TENCENT_LBS_DEFAULT_REGION = os.getenv("TENCENT_LBS_DEFAULT_REGION", "福州")
# 周边检索默认半径（米），腾讯上限 50000
TENCENT_LBS_DEFAULT_RADIUS = int(os.getenv("TENCENT_LBS_DEFAULT_RADIUS", "3000"))
# 单次检索返回条数上限（腾讯 page_size 上限 20）
TENCENT_LBS_MAX_PAGE_SIZE = int(os.getenv("TENCENT_LBS_MAX_PAGE_SIZE", "20"))

# =============================================
# 旅行域接入开关
# =============================================
# 开启后 travel 域的通勤估算改走真实路径规划（失败自动回落直线估算），
# 默认关闭：保证既有单测与排程口径不被网络依赖污染。
TRAVEL_USE_LIVE_MAP = _bool("TRAVEL_USE_LIVE_MAP", False)


def is_configured() -> bool:
    """Key 是否可用。调用方据此决定走真实数据还是本地降级。"""
    return bool(TENCENT_LBS_ENABLED and TENCENT_LBS_KEY)


def is_live_map_enabled() -> bool:
    """旅行域是否启用真实地图数据（需同时满足总开关与 Key 非空）。"""
    return bool(TRAVEL_USE_LIVE_MAP and is_configured())

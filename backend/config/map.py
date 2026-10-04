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
# 熔断（2026-09-15）：连续失败 N 次开路 cooldown 秒，期间调用立即失败 →
# 调用方（行程规划的 POI 解析/路线估算）随即走本地估算兜底。避免网络
# 故障时每次调用都等满 connect+read 超时（实测把一次请求拖到 6 分钟）。
TENCENT_LBS_BREAKER_THRESHOLD = int(os.getenv("TENCENT_LBS_BREAKER_THRESHOLD", "3"))
TENCENT_LBS_BREAKER_COOLDOWN = float(os.getenv("TENCENT_LBS_BREAKER_COOLDOWN", "60"))

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


# =============================================
# 和风天气（腾讯天气的备用源，2026-09-28）
# =============================================
# 配置 Key 即自动作为天气备用源：主源（腾讯 LBS）失败（超时/限流/鉴权/
# 城市未收录）时降级到和风；不配置则行为与旧版完全一致（零行为变化）。
# 注意：2024-10 后注册的和风账号须用控制台分配的专属 API Host
# （本机实测 key 走 api.qweather.com）；老账号默认 devapi.qweather.com。
QWEATHER_API_KEY = os.getenv("QWEATHER_API_KEY", "").strip()
QWEATHER_API_HOST = (os.getenv("QWEATHER_API_HOST", "").strip()
                     or "devapi.qweather.com")


def is_qweather_configured() -> bool:
    """和风备用源是否参与 fallback（Key 非空即启用，开关随天气总闸）。"""
    return bool(QWEATHER_API_KEY)


# =============================================
# 高德开放平台（AMap Web服务 API，2026-10-02）—— 商家级 POI 检索
# =============================================
# 与腾讯并存的补充 provider：腾讯检索只给名称/类别/地址，商家维度字段
# （评分 / 人均消费 / 营业时间）只有高德 v5 检索（show_fields=business）
# 提供。Key 须在高德控制台绑定「Web服务」类型，类型不符会返回 10007。
#
# 同腾讯三条纪律：
#   1. Key 只从环境变量读取，代码内不落默认值；缺失时整体降级，
#      地图是增强能力，不是硬依赖。
#   2. Key 只在服务端持有，前端不接触。
#   3. 坐标系口径为 GCJ-02（高德与腾讯同系，互相换算无需转坐标，
#      但坐标串顺序是「经度,纬度」，归一在 infra/lbs/amap.py 做）。
AMAP_KEY = os.getenv("AMAP_KEY", "").strip()
# 数字签名私钥：仅当控制台为该 Key 开启「数字签名」校验时才需要。
# 留空即不签名（当前 Key 未开启，实测裸 Key 可用）。
AMAP_SECRET = os.getenv("AMAP_SECRET", "").strip()
# 总开关。默认 true，但实际生效条件为 enabled and key 非空，
# 「没配 Key」等价于「未启用」，与腾讯同口径。
AMAP_ENABLED = _bool("AMAP_ENABLED", True)

AMAP_HOST = os.getenv("AMAP_HOST", "https://restapi.amap.com").rstrip("/")
AMAP_CONNECT_TIMEOUT = float(os.getenv("AMAP_CONNECT_TIMEOUT", "3"))
AMAP_READ_TIMEOUT = float(os.getenv("AMAP_READ_TIMEOUT", "8"))
# 客户端节流：个人认证 Key 的搜索 QPS 上限低（超限返 10003），保底限速。
AMAP_MIN_INTERVAL = float(os.getenv("AMAP_MIN_INTERVAL", "0.35"))
AMAP_RETRIES = int(os.getenv("AMAP_RETRIES", "1"))
AMAP_RETRY_BACKOFF = float(os.getenv("AMAP_RETRY_BACKOFF", "0.5"))
# 熔断：连续失败 N 次开路 cooldown 秒（口径与腾讯一致），期间调用立即失败
AMAP_BREAKER_THRESHOLD = int(os.getenv("AMAP_BREAKER_THRESHOLD", "3"))
AMAP_BREAKER_COOLDOWN = float(os.getenv("AMAP_BREAKER_COOLDOWN", "60"))

# 商家信息（评分/人均/营业时间）变化不快，TTL 缓存省配额
AMAP_CACHE_ENABLED = _bool("AMAP_CACHE_ENABLED", True)
AMAP_CACHE_MAXSIZE = int(os.getenv("AMAP_CACHE_MAXSIZE", "512"))
AMAP_CACHE_TTL = float(os.getenv("AMAP_CACHE_TTL", "600"))
# 单次检索返回条数上限（高德 v5 page_size 上限 25）
AMAP_MAX_PAGE_SIZE = int(os.getenv("AMAP_MAX_PAGE_SIZE", "25"))
# 默认检索城市（与 TENCENT_LBS_DEFAULT_REGION 同一策略：无地域限定
# 会返回全国噪声结果）
AMAP_DEFAULT_REGION = os.getenv("AMAP_DEFAULT_REGION", "福州")
# 高德分类码：餐饮服务 / 住宿服务 / 风景名胜+科教文化服务（A1 评分源）。
# 允许通过环境变量调整，避免把业务类目固化在前端；商户 Tool 仍会校验
# 调用方传入的 types 格式。
AMAP_FOOD_TYPES = os.getenv("AMAP_FOOD_TYPES", "050000").strip()
AMAP_HOTEL_TYPES = os.getenv("AMAP_HOTEL_TYPES", "100000").strip()
# A1 候选池评分源：风景名胜(110000) + 科教文化服务(140000，含博物馆/展览馆)
AMAP_ATTRACTION_TYPES = os.getenv("AMAP_ATTRACTION_TYPES", "110000|140000").strip()


def is_amap_configured() -> bool:
    """高德 Key 是否可用。调用方据此决定走真实数据还是降级。"""
    return bool(AMAP_ENABLED and AMAP_KEY)

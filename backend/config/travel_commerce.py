"""config/travel_commerce.py — Travel Commerce & Inventory 配置（STOP K1）

与 config/travel.py 同策略：全部开关代码默认关（off），由根 .env 决定实际
取值——未验收的能力不得被线上流量命中。

三态 Provider 模式（STOP K0 §7 决策 3）：
  off   默认。Commerce prefilter 不命中，链路不可达。
  fake  显式测试模式。FakeHotel/FakeFlight 适配器供测试/评测/链路验收，
        输出强制 source=fake:commerce 且渲染层披露「测试数据」——
        **严禁作为生产验收**（任务书 §28）。
  live  真实供应商。当前无任何 Hotel/Flight Provider 凭据与适配器实现
        （K0 审计 §2/§3）——选 live 一律 DISABLED（reason=未接入），
        **绝不回退 fake 充数**（§29 严禁伪造）。
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


# 域总开关（prefilter 与域图执行双闸）
TRAVEL_COMMERCE_ENABLED = _env_flag("TRAVEL_COMMERCE_ENABLED")

# Provider 模式：off | fake | live（live 当前恒 DISABLED，见模块 docstring）
TRAVEL_COMMERCE_PROVIDER_MODE = os.getenv(
    "TRAVEL_COMMERCE_PROVIDER_MODE", "off").strip().lower()

# Deep Link host 白名单（逗号分隔，小写）。空 = 拒绝一切 Provider 提供的
# 链接（fail-closed）：未配置白名单时不该有任何链接可穿透到用户。
TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS = tuple(
    h.strip().lower()
    for h in os.getenv("TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS", "").split(",")
    if h.strip()
)

# Deep Link 长度上限（URL 过长即拒绝；防解析负担与诡异构造）
TRAVEL_COMMERCE_DEEPLINK_MAX_LEN = int(
    os.getenv("TRAVEL_COMMERCE_DEEPLINK_MAX_LEN", "2048"))

# 单次搜索返回 offer 数上限（渲染与 DoS 防护；超出截断并披露）
TRAVEL_COMMERCE_MAX_OFFERS = int(os.getenv("TRAVEL_COMMERCE_MAX_OFFERS", "10"))

# 日期窗校验：入住日期不得早于今天；check_out 不得晚于 today + horizon
TRAVEL_COMMERCE_DATE_HORIZON_DAYS = int(
    os.getenv("TRAVEL_COMMERCE_DATE_HORIZON_DAYS", "365"))

# 搜索结果为空/无结果的 fresh 缓存 TTL（秒）——区别于 negative cache 60s：
# 「确认无结果」也允许短缓存防打爆，但比 NOT_FOUND 稍长（结果随供给变化）
TRAVEL_COMMERCE_EMPTY_TTL = int(os.getenv("TRAVEL_COMMERCE_EMPTY_TTL", "120"))


def is_commerce_enabled() -> bool:
    return TRAVEL_COMMERCE_ENABLED


def provider_mode() -> str:
    return TRAVEL_COMMERCE_PROVIDER_MODE

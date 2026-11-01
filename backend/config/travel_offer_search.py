"""config/travel_offer_search.py — Hotel/Flight 数据源搜索配置
（收编自原商务域配置模块；原域图壳已于 2026-11-01 收编删除，本配置只剩
数据源实现层在用）

与 config/travel.py 同策略：Provider 模式默认关（off），由根 .env 决定
实际取值——未验收的能力不得被线上流量命中。

三态 Provider 模式（原 STOP K0 §7 决策 3）：
  off   默认。搜索不可达，调用方如实披露「未启用」。
  fake  显式测试模式。FakeHotel/FakeFlight 适配器供测试/评测/链路验收，
        输出强制 source=fake:commerce 且渲染层披露「测试数据」——
        **严禁作为生产验收**（任务书 §28）。
  live  真实供应商。酒店侧 2026-11-01 起接 rollinggo MCP 通道（能力中心
        动态 capability，经 mcp_registry 快照；运行时不在场 = disabled，
        **绝不回退 fake 充数**，§29）；机票侧仍无适配器，恒 DISABLED。

环境变量沿用 TRAVEL_COMMERCE_* 历史名（.env 兼容，不做迁移）。
"""
import os

from dotenv import load_dotenv

load_dotenv()


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

# live 酒店数据源的 MCP capability（能力中心连接名 rollinggo × 工具
# searchHotels 的标准 slug；连接改名需同步管理端与本值）
TRAVEL_ROLLINGGO_HOTEL_CAPABILITY = os.getenv(
    "TRAVEL_ROLLINGGO_HOTEL_CAPABILITY", "mcp.rollinggo__searchhotels").strip()


def provider_mode() -> str:
    return TRAVEL_COMMERCE_PROVIDER_MODE

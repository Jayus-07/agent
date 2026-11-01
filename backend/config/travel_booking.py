"""config/travel_booking.py — Booking 事务配置（收编重构后仅剩事务机制开关）

2026-11-01 收编重构：原域总开关 TRAVEL_BOOKING_ENABLED 随域图壳退役，
TRAVEL_BOOKING_PROVIDER 成为唯一开关（off | fake_booking_* | <real>），
控制 BookingExecutor 的 provider 选型——off 时 REST 端点如实拒绝。

Provider 选型对应 shared/provider_idempotency.py 集中 registry 的三种
fake profile（Model A/B/C）；真实供应商当前不存在——配置为真实供应商名
时 registry 未登记/UNKNOWN → ensure_execution_allowed fail-closed 拒绝。
"""
import os

from dotenv import load_dotenv

load_dotenv()


# Booking Provider：off | fake_booking_native | fake_booking_clientref |
#                   fake_booking_bare | <real>（未登记即 fail-closed）
TRAVEL_BOOKING_PROVIDER = os.getenv(
    "TRAVEL_BOOKING_PROVIDER", "off").strip().lower()

# Quote/确认 TTL（秒）——系统自己的 confirmation TTL（§十：绝不表述为
# 供应商锁价；provider_expires_at 仅 Provider 明示时存在）
TRAVEL_BOOKING_QUOTE_TTL_SECONDS = int(
    os.getenv("TRAVEL_BOOKING_QUOTE_TTL_SECONDS", "900"))

# 恢复扫描：SUBMITTING/IN_DOUBT 订单 stale 阈值与批大小
TRAVEL_BOOKING_RECOVERY_STALE_SECONDS = int(
    os.getenv("TRAVEL_BOOKING_RECOVERY_STALE_SECONDS", "120"))
TRAVEL_BOOKING_RECOVERY_BATCH = int(
    os.getenv("TRAVEL_BOOKING_RECOVERY_BATCH", "20"))

# 单次搜索返回 offer 数上限（复用 commerce 同名语义）
TRAVEL_BOOKING_MAX_ORDERS_REPORT = int(
    os.getenv("TRAVEL_BOOKING_MAX_ORDERS_REPORT", "5"))


def provider_name() -> str:
    return TRAVEL_BOOKING_PROVIDER

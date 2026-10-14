"""评测报告时间格式化。"""
from datetime import datetime, timedelta, timezone


_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def format_shanghai_timestamp(timestamp: str) -> str:
    """将 ISO 时间转成上海时区的中文可读格式。

    兼容旧报告中没有时区后缀的时间；旧版评测默认运行在 UTC 环境。
    """
    normalized = timestamp.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"

    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    shanghai_time = parsed.astimezone(_SHANGHAI_TIMEZONE)
    return (
        f"{shanghai_time.year} 年 {shanghai_time.month} 月 "
        f"{shanghai_time.day} 日 {shanghai_time:%H:%M:%S}（上海时间）"
    )

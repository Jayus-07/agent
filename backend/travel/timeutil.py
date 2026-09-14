"""travel/timeutil.py — 行程时间算术

行程时间统一用 "HH:MM" 字符串承载（可读、可序列化、跨天不溢出），
但所有比较与加减必须走本模块 —— 否则各处重复实现解析与边界处理，
迟早出现 "9:00" 与 "09:00" 混用、或 24:10 这种越界读数。

约定：
  - 入参容忍 "9:00" / "09:00" 两种写法
  - 一天以内用 0..1439 的分钟数表示，不跨天；跨天意味着排程有 bug，
    由 from_min 的 clamp 兜底并在调用方可见（不静默吃掉）
"""
from __future__ import annotations

MINUTES_PER_DAY = 24 * 60


def to_min(hhmm: str, default: int | None = None) -> int:
    """ "HH:MM" → 当日分钟数。

    Args:
        hhmm: 时刻字符串
        default: 解析失败时的回退值；为 None 则抛 ValueError（不静默兜底，
                 让数据问题在测试阶段就暴露）
    """
    try:
        parts = str(hhmm).strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"时刻格式应为 HH:MM，实际 {hhmm!r}")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"时刻越界: {hhmm!r}")
        return h * 60 + m
    except (ValueError, AttributeError):
        if default is None:
            raise
        return default


def from_min(minutes: int) -> str:
    """当日分钟数 → "HH:MM"；越界值 clamp 到 [0, 23:59]。"""
    clamped = max(0, min(MINUTES_PER_DAY - 1, int(minutes)))
    return f"{clamped // 60:02d}:{clamped % 60:02d}"


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """两个半开区间 [start, end) 是否重叠。首尾相接不算重叠。"""
    return a_start < b_end and b_start < a_end


def format_range(start: str, end: str) -> str:
    """统一展示格式，供行程单复用。"""
    return f"{start}-{end}"

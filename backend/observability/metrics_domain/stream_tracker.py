"""流式响应延迟度量与追踪器。"""
from __future__ import annotations

from prometheus_client import Histogram

# ── TTFT / TPOT 可观测性（P1 真 token 级流式 + 2026-09-13 补 TPOT）──
# TTFT：首 delta 事件距请求开始的秒数：真流式下 ≈ 首个生成 chunk 到达时间，
# 假打字机回退时 ≈ 全链路耗时。对比两条曲线即可验证流式改造收益。
chat_ttft_seconds = Histogram(
    "chat_ttft_seconds",
    "Time to first delta event (TTFT) in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0, 60.0),
)


# 首次明确状态反馈与首token分开统计，不能用“已收到”冒充答案已生成。
chat_first_feedback_seconds = Histogram(
    "chat_first_feedback_seconds",
    "Time to first meaningful SSE feedback in seconds (not TTFT)",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 30.0, 60.0),
)


# TPOT：首 delta 到末 delta 之间平均每 token 生成耗时（受 LLM 服务端 decode 速度
# 与网络吞吐影响，与 TTFT 的 prefill 阶段正交）。deltas < 2 时不采样。
chat_tpot_seconds = Histogram(
    "chat_tpot_seconds",
    "Time per output token (TPOT) in seconds between first and last delta",
    buckets=(0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
)


class StreamLatencyTracker:
    """SSE 流式延迟追踪器：记录 TTFT / delta 数 / TPOT，收尾时统一上报。

    TPOT 定义：(末 delta 时间 - 首 delta 时间) / (delta 数 - 1)，
    即 decode 阶段平均每 token 耗时（不含 prefill 的 TTFT 部分）。

    用法:
        tracker = StreamLatencyTracker(start=time.monotonic())
        for evt in events:
            if evt["event"] == "delta":
                tracker.on_delta(time.monotonic())
        tracker.finish()   # finally 中调用，幂等
    """

    def __init__(self, start: float) -> None:
        self._start = start
        self._first_delta: float | None = None
        self._first_feedback: float | None = None
        self._last_delta: float | None = None
        self._delta_count = 0
        self._finished = False

    @property
    def delta_count(self) -> int:
        return self._delta_count

    @property
    def ttft_ms(self) -> int | None:
        """首 token 延迟毫秒（M13 尾项：落 trace_summary.ttft_ms 用）。

        None = 未收到任何 delta（流式前失败/纯 status 流），调用方跳过落库。
        """
        if self._first_delta is None:
            return None
        return max(1, round((self._first_delta - self._start) * 1000))

    def on_delta(self, now: float) -> None:
        """每收到一个 delta 事件调用一次。首个 delta 同时记 TTFT。"""
        self._delta_count += 1
        if self._first_delta is None:
            self._first_delta = now
            try:
                chat_ttft_seconds.observe(now - self._start)
            except Exception:
                pass
        self._last_delta = now

    @property
    def first_feedback_ms(self) -> int | None:
        if self._first_feedback is None:
            return None
        return max(1, round((self._first_feedback - self._start) * 1000))

    def on_feedback(self, now: float) -> None:
        """消费方实际投递首个可见状态时采样，不计meta和心跳。"""
        if self._first_feedback is not None:
            return
        self._first_feedback = now
        try:
            chat_first_feedback_seconds.observe(max(0, now - self._start))
        except Exception:
            pass

    def finish(self) -> None:
        """流结束时计算并上报 TPOT；delta 不足 2 个（无 decode 过程）跳过。幂等。"""
        if self._finished:
            return
        self._finished = True
        if self._first_delta is None or self._delta_count < 2:
            return
        try:
            decode_elapsed = self._last_delta - self._first_delta
            chat_tpot_seconds.observe(decode_elapsed / (self._delta_count - 1))
        except Exception:
            pass



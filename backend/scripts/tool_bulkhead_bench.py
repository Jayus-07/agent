"""scripts/tool_bulkhead_bench.py — Tool 隔离舱容量实测（bulkhead 压测）

目的：把「槽位 ÷ 真实耗时」从估算换成实测。对指定 tool_key 按目标并发
施压，测量：
  - 实际成功率 / TOOL_BUSY 拒绝率
  - 延迟分位数 p50 / p90 / p95 / p99
  - 有效槽位数（实测在途峰值）
  - 可持续 QPS（用实测延迟反算）

与真实链路的差异（刻意为之，便于可重复对比）：
  - 用注入的假 call（可控耗时），不真实访问 RAG/SQL/网络；
    本脚本量的是**执行层与隔离舱的行为**，不是下游真实性能。
  - 下游真实延迟请用 --latency-ms 传实测值（如 rag p50=5s 传 5000）。

用法（Windows 本机实测，注意两个环境变量）：
  # LOG_FILE 必须重定向：默认 rag_system.log 被在跑的 backend 进程独占，导入即 PermissionError
  # PYTHONIOENCODING 必须设 utf8：否则 GBK 控制台中文乱码
  set LOG_FILE=bench.log
  set PYTHONPATH=D:\path\to\agent
  set PYTHONIOENCODING=utf8
  .venv\Scripts\python.exe backend\scripts\tool_bulkhead_bench.py --tool rag.search --sweep

  # Linux/容器：
  LOG_FILE=bench.log PYTHONPATH=. PYTHONIOENCODING=utf8 \
      python backend/scripts/tool_bulkhead_bench.py --tool rag.search --latency-ms 5000 --sweep

退出码：0 正常；1 参数/环境错误。压测本身不设 PASS/FAIL（结果供人判断）。
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field

from backend.core.tool_runtime.bulkhead import bulkhead_registry
from backend.core.tool_runtime.executor import safe_tool_executor
from backend.core.tool_runtime.models import ToolStatus
from backend.core.tool_runtime.policy import get_policy


@dataclass
class Sample:
    """单次调用的观测结果。"""
    ok: bool
    latency_ms: float
    error_code: str = ""


@dataclass
class BenchResult:
    tool_key: str
    concurrency: int
    requests: int
    latency_injected_ms: float
    limit: int
    samples: list[Sample] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.samples if s.ok)

    @property
    def busy_count(self) -> int:
        return sum(1 for s in self.samples if s.error_code == "TOOL_BUSY")

    @property
    def other_errors(self) -> int:
        return len(self.samples) - self.ok_count - self.busy_count

    def _pct(self, values: list[float], ratio: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
        return ordered[idx]

    def report(self, wall_s: float, peak_inflight: int) -> dict:
        ok_lat = [s.latency_ms for s in self.samples if s.ok]
        total = max(1, len(self.samples))
        qps = self.ok_count / wall_s if wall_s > 0 else 0.0
        # 用实测成功延迟反算「可持续 QPS」= 槽位 / 平均占用
        avg_ok = statistics.fmean(ok_lat) if ok_lat else 0.0
        sustainable = (self.limit / (avg_ok / 1000)) if avg_ok > 0 else 0.0
        return {
            "tool_key": self.tool_key,
            "limit": self.limit,
            "concurrency": self.concurrency,
            "requests": self.requests,
            "injected_latency_ms": self.latency_injected_ms,
            "ok": self.ok_count,
            "busy": self.busy_count,
            "other_errors": self.other_errors,
            "reject_rate_pct": round(self.busy_count / total * 100, 2),
            "wall_s": round(wall_s, 3),
            "achieved_qps": round(qps, 2),
            "sustainable_qps_est": round(sustainable, 2),
            "peak_inflight": peak_inflight,
            "lat_p50": round(self._pct(ok_lat, 0.50), 1),
            "lat_p90": round(self._pct(ok_lat, 0.90), 1),
            "lat_p95": round(self._pct(ok_lat, 0.95), 1),
            "lat_p99": round(self._pct(ok_lat, 0.99), 1),
        }


async def _run_one(
    tool_key: str, latency_ms: float, result: BenchResult,
    inflight: dict, stop: asyncio.Event,
) -> None:
    """单次调用：走真实 SafeToolExecutor，call 为可控耗时的假实现。"""

    async def _fake_call():
        await asyncio.sleep(latency_ms / 1000)
        return {"ok": True}

    t0 = time.monotonic()
    res = await safe_tool_executor.run(tool_key=tool_key, call=_fake_call)
    lat = (time.monotonic() - t0) * 1000
    ok = res.status is ToolStatus.SUCCESS
    result.samples.append(Sample(ok=ok, latency_ms=lat,
                                 error_code=res.error_code or ""))
    if not stop.is_set() and not ok and res.error_code == "TOOL_BUSY":
        pass


async def _worker(
    tool_key: str, latency_ms: float, result: BenchResult, n: int,
    inflight: dict, peak: list, stop: asyncio.Event,
) -> None:
    for _ in range(n):
        if stop.is_set():
            return
        async with inflight["sem"]:
            cur = inflight["cur"] + 1
            inflight["cur"] = cur
            peak[0] = max(peak[0], cur)
            try:
                await _run_one(tool_key, latency_ms, result, inflight, stop)
            finally:
                inflight["cur"] -= 1


async def bench(
    tool_key: str, concurrency: int, requests: int, latency_ms: float,
) -> tuple[BenchResult, float, int]:
    policy = get_policy(tool_key)
    bulkhead = bulkhead_registry.get(tool_key, policy.bulkhead_limit)
    limit = bulkhead.limit

    result = BenchResult(
        tool_key=tool_key, concurrency=concurrency, requests=requests,
        latency_injected_ms=latency_ms, limit=limit,
    )
    # 预热：首次调用要付一次性成本（Redis 连接/指标注册/trace span 初始化），
    # 不预热会把冷启动算进 p95/p99（实测 300ms 注入会测出 2125ms 的假尾延迟）。
    for _ in range(min(3, max(1, requests))):
        await safe_tool_executor.run(
            tool_key=tool_key,
            call=lambda: asyncio.sleep(latency_ms / 1000),
        )
    result.samples.clear()

    inflight = {"cur": 0, "sem": asyncio.Semaphore(concurrency)}
    peak = [0]
    stop = asyncio.Event()

    per = requests // concurrency
    extra = requests % concurrency
    counts = [per + (1 if i < extra else 0) for i in range(concurrency)]

    t0 = time.monotonic()
    await asyncio.gather(*[
        _worker(tool_key, latency_ms, result, counts[i], inflight, peak, stop)
        for i in range(concurrency)
    ])
    wall = time.monotonic() - t0
    return result, wall, peak[0]


def _fmt(row: dict) -> str:
    return (
        f"  limit={row['limit']:<3} 并发={row['concurrency']:<4} "
        f"注入延迟={row['injected_latency_ms']:>6.0f}ms  "
        f"成功={row['ok']:<4} BUSY={row['busy']:<4} "
        f"拒绝率={row['reject_rate_pct']:>6.2f}%  "
        f"实测QPS={row['achieved_qps']:>7.2f}  "
        f"可持续QPS≈{row['sustainable_qps_est']:>7.2f}  "
        f"p50={row['lat_p50']:>7.1f} p95={row['lat_p95']:>7.1f} p99={row['lat_p99']:>7.1f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tool", default="rag.search", help="tool_key（capability 名）")
    parser.add_argument("--concurrency", type=int, default=24, help="同时发起的调用数")
    parser.add_argument("--requests", type=int, default=120, help="总调用数")
    parser.add_argument("--latency-ms", type=float, default=500.0,
                        help="注入的单次耗时（ms）；下游真实延迟请传实测 p50")
    parser.add_argument("--sweep", action="store_true",
                        help="对多个并发档位扫描，画出拒绝率拐点")
    args = parser.parse_args()

    if args.requests < 1 or args.concurrency < 1:
        print("[bench] 参数非法：concurrency/requests 必须 ≥1")
        return 1
    if args.latency_ms < 0:
        print("[bench] 参数非法：latency-ms 不能为负")
        return 1

    policy = get_policy(args.tool)
    print("[bench] Tool 隔离舱容量实测")
    print(f"[bench] tool={args.tool} policy.bulkhead_limit={policy.bulkhead_limit} "
          f"timeout={policy.timeout_ms}ms retries={policy.retries} "
          f"wait={policy.bulkhead_wait_ms}ms")
    print()

    if args.sweep:
        limit = bulkhead_registry.get(args.tool, policy.bulkhead_limit).limit
        print(f"[bench] 扫描模式（limit={limit}，注入延迟={args.latency_ms:.0f}ms）")
        saturation_qps = limit / (args.latency_ms / 1000)
        print(f"[bench] 理论崩点 QPS = limit / 延迟 = {saturation_qps:.2f}/s")
        rows = []
        for c in sorted({2, max(1, limit // 4), max(1, limit // 2), limit,
                         limit + max(1, limit // 4), limit * 2}):
            n = max(c * 3, 30)
            res, wall, peak = asyncio.run(
                bench(args.tool, c, n, args.latency_ms))
            row = res.report(wall, peak)
            rows.append(row)
            print(_fmt(row))
        print()
        worst = max(rows, key=lambda r: r["reject_rate_pct"])
        print(f"[bench] 最差档位：并发={worst['concurrency']} "
              f"拒绝率={worst['reject_rate_pct']}%")
        return 0

    res, wall, peak = asyncio.run(
        bench(args.tool, args.concurrency, args.requests, args.latency_ms))
    row = res.report(wall, peak)
    print("[bench] 结果")
    for k, v in row.items():
        print(f"  {k:<22} {v}")
    print()
    print(f"[bench] 提示：可持续 QPS ≈ limit / 平均成功延迟 "
          f"= {row['limit']} / {row['lat_p50']:.1f}ms ≈ {row['sustainable_qps_est']}/s")
    if row["reject_rate_pct"] > 0:
        print(f"[bench] 已出现 TOOL_BUSY（{row['busy']} 次，"
              f"{row['reject_rate_pct']}%）→ 并发超过槽位，多出的请求 300ms 内拿不到即拒")
    else:
        print("[bench] 未出现 TOOL_BUSY → 当前并发未打满槽位")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""cs_dispatch_loadtest.py — P9 派单链路压测（APISIX 网关）。

两组负载（方案 §六 P9 完成标准）：

1. 突发：200 并发同时打 GET（默认 /cs/conversations/stats，只读）。
2. 持续：默认 20 RPS × 10 分钟稳定压测（``--duration`` 可调）。

只读端点压测不产生业务副作用；写路径压测（转人工入池）需要
``--include-write`` 显式开启并自行承担测试数据清理。

用法：
    python scripts/cs_dispatch_loadtest.py --gateway http://gateway:9080
    python scripts/cs_dispatch_loadtest.py --gateway http://gateway:9080 --burst-only

结果 JSON 写 docs/reports/cs-dispatch-loadtest-<ts>.json。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO_ROOT / "docs" / "reports"

BURST_CONCURRENCY = 200
SUSTAINED_RPS = 20
SUSTAINED_DURATION_S = 600
TIMEOUT_S = 15.0


async def _burst(client: httpx.AsyncClient, url: str) -> list[float]:
    async def one() -> float:
        start = time.perf_counter()
        resp = await client.get(url)
        resp.raise_for_status()
        return time.perf_counter() - start

    latencies = await asyncio.gather(*(one() for _ in range(BURST_CONCURRENCY)))
    return list(latencies)


async def _sustained(client: httpx.AsyncClient, url: str, *, rps: int, duration_s: int) -> list[float]:
    interval = 1.0 / rps
    latencies: list[float] = []
    deadline = time.perf_counter() + duration_s
    while time.perf_counter() < deadline:
        batch_start = time.perf_counter()
        try:
            resp = await client.get(url)
            latencies.append(time.perf_counter() - batch_start)
            resp.raise_for_status()
        except httpx.HTTPError:
            latencies.append(time.perf_counter() - batch_start)
        elapsed = time.perf_counter() - batch_start
        await asyncio.sleep(max(0.0, interval - elapsed))
    return latencies


def _summary(latencies: list[float]) -> dict:
    if not latencies:
        return {"count": 0}
    ordered = sorted(latencies)
    return {
        "count": len(ordered),
        "p50_s": round(statistics.median(ordered), 4),
        "p95_s": round(ordered[int(len(ordered) * 0.95) - 1], 4),
        "p99_s": round(ordered[int(len(ordered) * 0.99) - 1], 4),
        "max_s": round(ordered[-1], 4),
        "mean_s": round(statistics.fmean(ordered), 4),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", required=True, help="如 http://gateway:9080")
    parser.add_argument("--path", default="/cs/conversations/stats")
    parser.add_argument("--duration", type=int, default=SUSTAINED_DURATION_S)
    parser.add_argument("--burst-only", action="store_true")
    args = parser.parse_args()

    url = args.gateway.rstrip("/") + args.path
    report: dict = {
        "script": "cs_dispatch_loadtest.py",
        "gateway": args.gateway,
        "path": args.path,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            probe = await client.get(url)
        except httpx.HTTPError as exc:
            report["error"] = f"目标不可达（{type(exc).__name__}），未开始压测"
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            out = REPORT_DIR / "cs-dispatch-loadtest-unreachable.json"
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        report["probe_status"] = probe.status_code
        if probe.status_code >= 500:
            report["error"] = "目标不可用（probe 5xx），未开始压测"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        burst = await _burst(client, url)
        report["burst"] = {"concurrency": BURST_CONCURRENCY, **_summary(burst)}
        print("burst:", report["burst"])

        if not args.burst_only:
            sustained = await _sustained(
                client, url, rps=SUSTAINED_RPS, duration_s=args.duration
            )
            report["sustained"] = {
                "rps_target": SUSTAINED_RPS,
                "duration_s": args.duration,
                **_summary(sustained),
            }
            print("sustained:", report["sustained"])

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report["started_at"][:19].replace(":", "").replace("-", "")
    out = REPORT_DIR / f"cs-dispatch-loadtest-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

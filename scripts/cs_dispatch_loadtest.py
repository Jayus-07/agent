"""cs_dispatch_loadtest.py — P9 派单链路压测（APISIX 网关）。

两组负载（方案 §六 P9 完成标准；Q4 2026-11-03 修正）：

1. 突发：``--concurrency`` 并发同时打 GET（默认 200；打共享栈时建议降档）。
2. 持续：``--rps`` 目标速率 × ``--duration`` 秒，open-loop 定时发射
   （修正点：旧实现串行发请求，实际速率 = 1/延迟，永远达不到目标 RPS）。

计量口径（修正点：旧实现错误耗时混入延迟分位数、无错误统计）：
  - 延迟分位数（p50/p95/p99/max/mean）只统计 2xx 成功请求；
  - 非 2xx 与异常独立计数，按 status/异常类型分布；
  - 持续负载记录实际发射速率与完成速率，供与目标 RPS 对照；
  - 网关限流（limit-count 600/min/user，≈10 rps）触发的 429 属预期行为，
    会出现在状态分布里——按容量结论解读，不是脚本故障。

认证：经 ``--header`` 注入（可重复），通常为 Authorization: Bearer <JWT>
（网关 gateway-auth 校验签名 + 会话闸）。预检非 2xx 直接中止并给出归因。

用法：
    python scripts/cs_dispatch_loadtest.py --gateway http://127.0.0.1:9080 \
        --path /api/cs/customer-context --header "Authorization: Bearer <JWT>"
    python scripts/cs_dispatch_loadtest.py --gateway ... --burst-only

结果 JSON 写 docs/reports/cs-dispatch-loadtest-<ts>.json。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
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

# 单条请求的计量记录：(latency_s, status_code 或 异常类型名)
RequestRecord = tuple[float, int | str]


def summarize(records: list[RequestRecord]) -> dict:
    """成功请求出延迟分位数；错误独立计数与分布。

    p95/p99 用 ceil 索引（小样本不越界），口径注释写入报告键名。
    """
    ok = [lat for lat, status in records if status == 200 or (isinstance(status, int) and 200 <= status < 300)]
    errors: dict[str, int] = {}
    for _lat, status in records:
        key = str(status)
        if key == "200" or (status != 200 and isinstance(status, int) and 200 <= status < 300):
            continue
        errors[key] = errors.get(key, 0) + 1
    result: dict = {
        "total": len(records),
        "success": len(ok),
        "count": len(ok),
        "success_ratio": round(len(ok) / len(records), 4) if records else None,
        "errors": dict(sorted(errors.items(), key=lambda kv: -kv[1])),
    }
    if ok:
        ordered = sorted(ok)
        result.update({
            "latency_basis": "2xx 成功请求",
            "count": len(ordered),
            "p50_s": round(statistics.median(ordered), 4),
            "p95_s": round(ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))], 4),
            "p99_s": round(ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.99) - 1))], 4),
            "max_s": round(ordered[-1], 4),
            "mean_s": round(statistics.fmean(ordered), 4),
        })
    return result


async def _one_get(client: httpx.AsyncClient, url: str, headers: dict) -> RequestRecord:
    start = time.perf_counter()
    try:
        resp = await client.get(url, headers=headers)
        return time.perf_counter() - start, resp.status_code
    except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
        return time.perf_counter() - start, type(exc).__name__


async def run_burst(client: httpx.AsyncClient, url: str, headers: dict,
                    *, concurrency: int) -> list[RequestRecord]:
    """``concurrency`` 个请求同时发射（asyncio.gather）。"""
    return list(await asyncio.gather(*(_one_get(client, url, headers) for _ in range(concurrency))))


async def run_sustained(client: httpx.AsyncClient, url: str, headers: dict,
                        *, rps: int, duration_s: int) -> tuple[list[RequestRecord], float, float]:
    """open-loop 定时发射：按 1/rps 节拍创建任务，不等待前一请求完成。

    返回 (records, 实际发射速率, 实际完成速率)。发射速率显著低于目标
    说明调度本身受限；完成速率低于发射说明请求积压（超时排队）。
    """
    interval = 1.0 / rps
    started = time.perf_counter()
    deadline = started + duration_s
    tasks: list[asyncio.Task] = []
    while True:
        now = time.perf_counter()
        if now >= deadline:
            break
        tasks.append(asyncio.create_task(_one_get(client, url, headers)))
        next_fire = started + len(tasks) * interval
        await asyncio.sleep(max(0.0, next_fire - time.perf_counter()))
    emitted_at = time.perf_counter()
    records = list(await asyncio.gather(*tasks))
    finished_at = time.perf_counter()
    emit_span = max(emitted_at - started, 1e-9)
    finish_span = max(finished_at - started, 1e-9)
    return records, len(tasks) / emit_span, len(records) / finish_span


def _write_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report["started_at"][:19].replace(":", "").replace("-", "")
    out = REPORT_DIR / f"cs-dispatch-loadtest-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _pool_size(*, concurrency: int, rps: int) -> int:
    """客户端连接池上界：open-loop 在途请求 ≈ rps × 最坏延迟(TIMEOUT_S)。

    Review P2：httpx 默认池 100，在途超过池容量时任务在客户端排队——
    发射速率虚高（按任务数计）且排队时间混入延迟，会把客户端瓶颈误判
    为服务端容量。池取 max(突发并发, rps×超时, 512) 并写入报告。
    """
    return max(concurrency, min(rps * int(TIMEOUT_S), 2000), 512)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", required=True, help="如 http://127.0.0.1:9080")
    parser.add_argument("--path", default="/api/cs/customer-context",
                        help="网关路径（/api/cs/* 经兜底路由剥前缀转发；旧默认 /cs/conversations/stats 在网关不存在）")
    parser.add_argument("--header", action="append", default=[],
                        metavar="NAME: VALUE", help="附加请求头（可重复；认证必填）")
    parser.add_argument("--concurrency", type=int, default=BURST_CONCURRENCY)
    parser.add_argument("--rps", type=int, default=SUSTAINED_RPS)
    parser.add_argument("--duration", type=int, default=SUSTAINED_DURATION_S)
    parser.add_argument("--burst-only", action="store_true")
    args = parser.parse_args()

    headers: dict[str, str] = {}
    for raw in args.header:
        name, _, value = raw.partition(":")
        if not value:
            parser.error(f"--header 需要 'Name: Value' 形式: {raw!r}")
        headers[name.strip()] = value.strip()

    url = args.gateway.rstrip("/") + args.path
    pool = _pool_size(concurrency=args.concurrency, rps=args.rps)
    report: dict = {
        "script": "cs_dispatch_loadtest.py",
        "gateway": args.gateway,
        "path": args.path,
        "burst_concurrency": args.concurrency,
        "sustained_rps_target": args.rps,
        "sustained_duration_s": args.duration,
        "client_pool_max_connections": pool,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    limits = httpx.Limits(max_connections=pool, max_keepalive_connections=pool)
    async with httpx.AsyncClient(timeout=TIMEOUT_S, limits=limits) as client:
        probe_lat, probe_status = await _one_get(client, url, headers)
        report["probe"] = {"status": str(probe_status), "latency_s": round(probe_lat, 4)}
        ok_probe = isinstance(probe_status, int) and 200 <= probe_status < 300
        if not ok_probe:
            if probe_status == 401:
                report["error"] = "预检 401：认证缺失/无效（JWT 过期或会话闸无键），未开始压测"
            elif probe_status == 403:
                report["error"] = "预检 403：凭据无权访问该端点，未开始压测"
            elif probe_status in (429,):
                report["error"] = "预检即触发限流（429）：目标限流配置与压测参数冲突，未开始压测"
            elif isinstance(probe_status, int) and probe_status >= 500:
                report["error"] = f"预检 {probe_status}：目标不可用，未开始压测"
            else:
                report["error"] = f"预检未通过（{probe_status}），未开始压测"
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            out = _write_report(report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"report -> {out}")
            return 1

        burst = await run_burst(client, url, headers, concurrency=args.concurrency)
        report["burst"] = summarize(burst)
        print("burst:", json.dumps(report["burst"], ensure_ascii=False))

        if not args.burst_only:
            records, emit_rps, finish_rps = await run_sustained(
                client, url, headers, rps=args.rps, duration_s=args.duration,
            )
            report["sustained"] = {
                "rps_target": args.rps,
                "rps_emitted_actual": round(emit_rps, 2),
                "rps_finished_actual": round(finish_rps, 2),
                "duration_s": args.duration,
                **summarize(records),
            }
            print("sustained:", json.dumps(report["sustained"], ensure_ascii=False))

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    out = _write_report(report)
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

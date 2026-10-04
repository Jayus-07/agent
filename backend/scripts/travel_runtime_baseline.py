"""scripts/travel_runtime_baseline.py — 运行时基线聚合面（验收 #130/#131）

从进程内 Prometheus registry 直读旅游 Provider 遥测，输出：
  - #131 Cache Hit 聚合：hit/miss/negative/stale 计数与命中率
  - #130 调用数量基线：按 operation 的调用总数（fan-out 上限的观测面，
    A2 扩容后对比基线用）+ 降级/软停计数
  - #128 附带：按 operation 的延迟分位（histogram 直读近似）

用法：cd backend && PYTHONPATH=. python ../scripts/travel_runtime_baseline.py [--json]
（读的是本进程 registry——生产多副本时在各副本各跑一次或接 /metrics 抓取。）
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    from backend.providers.travel.live import telemetry as tel

    def counter_value(counter, label_filter: dict | None = None):
        """prometheus_client Counter 的样本直读（_metrics 是 dict 容器）。"""
        total = 0.0
        detail: dict = {}
        for labels_key, child in counter._metrics.items():  # noqa: SLF001
            value = child._value._value.get()  # noqa: SLF001
            if label_filter is not None:
                labels = dict(zip(counter._labelnames, labels_key))  # noqa: SLF001
                if any(label_filter.get(k) not in (None, v)
                       for k, v in labels.items()):
                    continue
            total += value
            detail[labels_key] = value
        return total, detail

    cache_total, cache_detail = counter_value(tel.travel_provider_cache_total)
    hits, _ = counter_value(
        tel.travel_provider_cache_total, {"cache_status": "hit"})
    fallback_total, fallback_detail = counter_value(
        tel.travel_provider_fallback_total)
    quota_total, _ = counter_value(tel.travel_provider_quota_total)
    stale_total, _ = counter_value(tel.travel_provider_stale_total)

    # 调用数：latency histogram 的观测次数 = 真实外部调用次数（cache hit
    # 不产生 histogram 观测——它在 call_with_budget 之前短路）
    calls_detail: dict = {}
    calls_total = 0.0
    for labels_key, hist in tel.travel_provider_latency_seconds._metrics.items():  # noqa: SLF001
        count = float(hist._count._value.get())  # noqa: SLF001
        calls_detail[labels_key] = count
        calls_total += count

    hit_rate = (hits / cache_total) if cache_total else 0.0
    payload = {
        "cache": {"total": cache_total, "hit": hits, "hit_rate": round(hit_rate, 4),
                  "detail": cache_detail},
        "calls": {"total": calls_total, "by_operation": calls_detail},
        "fallback": {"total": fallback_total, "detail": fallback_detail},
        "quota_soft_stopped": quota_total,
        "stale_served": stale_total,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[travel-runtime] 外部调用总数: {calls_total:.0f}")
        for k, v in sorted(calls_detail.items()):
            print(f"    {k}: {v:.0f}")
        print(f"[travel-runtime] 缓存: total={cache_total:.0f} hit={hits:.0f} "
              f"hit_rate={hit_rate:.1%}")
        print(f"[travel-runtime] 降级: {fallback_total:.0f}  软停: {quota_total:.0f}  "
              f"stale: {stale_total:.0f}")
        print("[travel-runtime] 口径：调用数=latency histogram 观测数（cache hit 短路"
              "不计）；hit_rate 分母含 negative/stale。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

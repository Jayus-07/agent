"""scripts/travel_p95_baseline.py — 旅游规划性能基线门禁（验收 #127/#144）

从 trace_summary 统计旅游链路（tags 含 travel_source）的 duration_ms /
ttft_ms 分位数，与冻结基线（travel_p95_baseline.json）对比：
  - 默认（校验模式）：超阈值 exit 1 + 告警输出（发布前跑，防性能退化）
  - --freeze（固化模式）：用当前数据计算基线并写入 JSON（P95 上浮 20%
    作为余量），基线变更须随代码评审提交

用法（本机库一律 5433）：
  cd backend && PYTHONPATH=. PGPORT=5433 python ../scripts/travel_p95_baseline.py
  cd backend && PYTHONPATH=. PGPORT=5433 python ../scripts/travel_p95_baseline.py --freeze
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "travel_p95_baseline.json"
# 样本少于该值时不判定退化（新环境/刚清库时基线无意义，只输出统计）
MIN_SAMPLES = 5


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return int(ordered[idx])


def fetch_travel_durations() -> dict[str, list[int]]:
    from sqlalchemy import text

    from backend.infra.db import get_memory_engine

    engine = get_memory_engine()
    sql = text(
        "SELECT duration_ms, COALESCE(ttft_ms, 0) AS ttft_ms "
        "FROM trace_summary "
        "WHERE tags LIKE '%travel_source%' AND status IN ('success','degraded') "
        "ORDER BY created_at DESC LIMIT 200"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return {
        "duration_ms": [int(r["duration_ms"]) for r in rows],
        "ttft_ms": [int(r["ttft_ms"]) for r in rows if r["ttft_ms"]],
    }


def compute_baseline(samples: dict[str, list[int]]) -> dict:
    return {
        "duration_ms": {
            "p50": _percentile(samples["duration_ms"], 0.50),
            "p95": _percentile(samples["duration_ms"], 0.95),
            # 冻结时上浮 20% 作为退化判定余量（波动来自 live 检索/网络）
            "p95_limit": int(_percentile(samples["duration_ms"], 0.95) * 1.2),
            "samples": len(samples["duration_ms"]),
        },
        "ttft_ms": {
            "p95": _percentile(samples["ttft_ms"], 0.95),
            "p95_limit": int(_percentile(samples["ttft_ms"], 0.95) * 1.2),
            "samples": len(samples["ttft_ms"]),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true",
                        help="用当前数据固化基线（覆盖 JSON）")
    args = parser.parse_args()

    samples = fetch_travel_durations()
    current = {
        "duration_ms": {
            "p50": _percentile(samples["duration_ms"], 0.50),
            "p95": _percentile(samples["duration_ms"], 0.95),
        },
        "ttft_ms": {"p95": _percentile(samples["ttft_ms"], 0.95)},
    }
    print(f"[travel-p95] 样本数 duration={len(samples['duration_ms'])} "
          f"ttft={len(samples['ttft_ms'])}")
    print(f"[travel-p95] 当前 duration p50={current['duration_ms']['p50']} "
          f"p95={current['duration_ms']['p95']} | ttft p95={current['ttft_ms']['p95']}")

    if args.freeze:
        baseline = compute_baseline(samples)
        baseline["frozen_at"] = __import__("datetime").datetime.now().isoformat(
            timespec="seconds")
        BASELINE_PATH.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[travel-p95] 基线已固化 → {BASELINE_PATH}")
        print(json.dumps(baseline, ensure_ascii=False, indent=2))
        return 0

    if not BASELINE_PATH.exists():
        print(f"[travel-p95] FAIL 基线文件不存在：{BASELINE_PATH}（先跑 --freeze 固化）")
        return 1
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    if len(samples["duration_ms"]) < MIN_SAMPLES:
        print(f"[travel-p95] 样本不足（<{MIN_SAMPLES}），跳过退化判定（只输出统计）")
        return 0

    exit_code = 0
    for metric in ("duration_ms", "ttft_ms"):
        limit = int(baseline.get(metric, {}).get("p95_limit", 0))
        now_p95 = int(current[metric]["p95"])
        if limit and now_p95 > limit:
            print(f"[travel-p95] ALERT {metric} p95={now_p95} 超过冻结基线上限 "
                  f"{limit}（基线 p95={baseline[metric]['p95']}）—— 性能退化，"
                  "请排查后重新 --freeze")
            exit_code = 1
        else:
            print(f"[travel-p95] PASS {metric} p95={now_p95} ≤ {limit}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

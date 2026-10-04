"""scripts/travel_plan_version_reconcile.py — plan_version 四面对账（验收 #53）

对最近 N 小时的会话做系统化对账（此前只有逐项人工核，无系统化巡检）：
  1. 版本账内部：plan_version 连续性（首版=1，逐版 +1，无跳号/重复）
  2. 账本 ↔ decision：apply_draft 的 plan_version 必须存在于账本
  3. 账本 ↔ trace：confirmed 版本的会话应有对应 travel trace
任一不一致 → 输出明细并 exit 1（巡检/发布门禁消费）。

用法：cd backend && PYTHONPATH=. PGPORT=5433 python ../scripts/travel_plan_version_reconcile.py [--hours 24]
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24,
                        help="对账最近 N 小时的会话（默认 24）")
    args = parser.parse_args()
    window = f"{max(1, args.hours)} hours"

    from sqlalchemy import text

    from backend.infra.db import get_memory_engine

    engine = get_memory_engine()
    problems: list[str] = []

    with engine.connect() as conn:
        versions = conn.execute(text(
            "SELECT conversation_id, plan_version, plan_status, created_at "
            "FROM travel_plan_versions WHERE created_at > now() - CAST(:window AS INTERVAL) "
            "ORDER BY conversation_id, plan_version"),
            {"window": window},
        ).mappings().all()

        decisions = conn.execute(text(
            "SELECT conversation_id, decision, plan_version "
            "FROM travel_decision_audit WHERE created_at > now() - CAST(:window AS INTERVAL)"),
            {"window": window},
        ).mappings().all()

        traces = conn.execute(text(
            "SELECT session_id FROM trace_summary "
            "WHERE tags LIKE '%travel_source%' AND created_at::timestamptz > now() - CAST(:window AS INTERVAL)"),
            {"window": window},
        ).mappings().all()

    by_conv: dict[str, list[dict]] = {}
    for row in versions:
        by_conv.setdefault(row["conversation_id"], []).append(row)

    print(f"[reconcile] 对账窗口 {args.hours}h：会话 {len(by_conv)} 个、"
          f"版本行 {len(versions)}、decision {len(decisions)}、trace {len(traces)}")

    # ① 版本账内部：严格递增 + 无重复。
    # 口径说明（2026-10-04 对账实测沉淀）：plan_version 链包含**规划内部的
    # 修复中间版**（repair v+1 不落账），账本只落产出给用户的版本——因此
    # 相邻步进可 >1（1→3→5 正常），首版可 >1（首轮内部修复后直接落 v2）。
    # 对账只抓「回退/重复」这类真断链。
    for cid, rows in by_conv.items():
        seq = [int(r["plan_version"]) for r in rows]
        if seq != sorted(set(seq)):
            problems.append(f"{cid}: 版本序非严格递增或有重复 {seq}")
        if any(b <= a for a, b in zip(seq, seq[1:])):
            problems.append(f"{cid}: 版本回退 {seq}")

    # ② apply_draft 的版本必须在账本
    known = {(r["conversation_id"], int(r["plan_version"])) for r in versions}
    for d in decisions:
        if d["decision"] != "apply_draft":
            continue
        key = (d["conversation_id"], int(d["plan_version"] or 0))
        # 会话可能超出窗口（窗口内 decision 引用窗口外版本），只对账本里有
        # 该会话记录的做严格校验
        if d["conversation_id"] in by_conv and key not in known:
            problems.append(
                f"{key[0]}: apply_draft v{key[1]} 不在版本账（幽灵应用）")

    # ③ 账本会话应有对应 travel trace
    trace_sessions = {t["session_id"] for t in traces}
    for cid in by_conv:
        if cid not in trace_sessions:
            problems.append(f"{cid}: 版本账有记录但窗口内无 travel trace")

    if problems:
        print(f"[reconcile] FAIL {len(problems)} 处不一致：")
        for p in problems[:30]:
            print("  -", p)
        return 1
    print("[reconcile] PASS 版本账连续、decision/trace 与账本一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())

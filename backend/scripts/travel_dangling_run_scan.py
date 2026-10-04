"""scripts/travel_dangling_run_scan.py — 悬空 run 扫描（验收 #142）

对账口径（request_runtime 是无状态执行器，进程内注册表重启即清，
跨进程悬空判定落在 trace_summary 权威账上）：
  1. 全表：status 不在终态枚举（success/error/degraded/rejected/cancelled/
     timeout）的记录，且写入时间早于宽限窗（默认 15 分钟）→ 悬空；
  2. 旅游专项：tags 含 travel_source 的记录必须终止于终态——出现任何
     非终态（含未来新增的 running）即 FAIL。

悬空>0 时 exit 1 + 输出 trace_id 清单（供巡检/发布门禁消费）。

用法：cd backend && PYTHONPATH=. PGPORT=5433 python ../scripts/travel_dangling_run_scan.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

TERMINAL_STATES = ("success", "error", "degraded", "rejected",
                   "cancelled", "timeout")
# 宽限窗：写入后仍在执行中的合法窗口（TRAVEL_REQUEST_TIMEOUT_S=50s 的
# 数倍余量；超过该窗仍非终态即视为悬空，不可能是活请求）
GRACE_MINUTES = 15


def main() -> int:
    from sqlalchemy import text

    from backend.infra.db import get_memory_engine

    engine = get_memory_engine()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=GRACE_MINUTES)
              ).isoformat()

    with engine.connect() as conn:
        # ① 全表非终态扫描
        rows = conn.execute(text(
            "SELECT trace_id, workflow_name, status, created_at "
            "FROM trace_summary "
            "WHERE status NOT IN :terminal AND created_at < :cutoff "
            "ORDER BY created_at DESC LIMIT 50"),
            {"terminal": tuple(TERMINAL_STATES), "cutoff": cutoff},
        ).mappings().all()

        # ② 旅游链路终态覆盖
        travel_rows = conn.execute(text(
            "SELECT trace_id, status FROM trace_summary "
            "WHERE tags LIKE '%travel_source%' AND status NOT IN :terminal "
            "ORDER BY created_at DESC LIMIT 50"),
            {"terminal": tuple(TERMINAL_STATES)},
        ).mappings().all()

    print(f"[dangling-scan] 全表非终态（宽限 {GRACE_MINUTES}min）: {len(rows)} 条")
    for r in rows:
        print(f"  DANGLING {r['trace_id']} workflow={r['workflow_name']} "
              f"status={r['status']} created_at={r['created_at']}")
    print(f"[dangling-scan] 旅游链路非终态: {len(travel_rows)} 条")
    for r in travel_rows:
        print(f"  TRAVEL_DANGLING {r['trace_id']} status={r['status']}")

    if rows or travel_rows:
        print("[dangling-scan] FAIL 存在悬空 run（未终止于终态）")
        return 1
    print("[dangling-scan] PASS 所有 run 均终止于终态")
    return 0


if __name__ == "__main__":
    sys.exit(main())

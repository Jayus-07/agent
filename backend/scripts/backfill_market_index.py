# backend/scripts/backfill_market_index.py
"""一次性回填：将 competitor_snapshots 全量历史索引到 competitor_market collection。

用法（项目根目录）:
    python -m backend.scripts.backfill_market_index [--dry-run]
"""
import argparse

from backend.competitor.store import get_store
from backend.selection.market_index import get_market_index


def main() -> None:
    parser = argparse.ArgumentParser(description="回填市场语义索引")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    store = get_store()
    rows = store.list_snapshots()

    index = get_market_index()
    written = 0
    for row in rows:
        if args.dry_run:
            written += 1
            continue
        if index.index_snapshot(row):
            written += 1
    mode = "dry-run" if args.dry_run else "写入"
    print(f"[backfill] {mode} {written}/{len(rows)} 条快照 → competitor_market")


if __name__ == "__main__":
    main()

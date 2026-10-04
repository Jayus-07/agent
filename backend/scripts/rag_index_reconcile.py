"""只读索引对账入口；不一致或数据源不可达返回非零退出码。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", help="正式 chunk 集合；默认派生自 CHROMA_PATH")
    parser.add_argument("--output", type=Path, help="保存验收 JSON")
    parser.add_argument("--fix", action="store_true", help="修正无正式向量的 active 登记；其他问题仍报错")
    parser.add_argument("--backup", type=Path, help="修复前完整行备份，必须指定且文件不得已存在")
    args = parser.parse_args(argv)
    from backend.rag.indexing.reconcile import repair_missing_indexes, run_reconcile

    if args.fix and not args.backup:
        parser.error("--fix 必须指定 --backup，先备份再修改")
    repair = repair_missing_indexes(args.backup, args.collection) if args.fix else None

    result = run_reconcile(args.collection)
    if repair is not None:
        result["repair"] = repair
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    sys.stdout.write(payload + "\n")
    return 0 if result["consistent"] else 1


if __name__ == "__main__":
    sys.exit(main())

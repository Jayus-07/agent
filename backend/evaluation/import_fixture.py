"""评估 fixture 的明确离线导入入口。"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """按 fixture_set 调用既有离线索引导入器。"""
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1 or not args[0].strip():
        print("用法: python -m backend.evaluation.import_fixture <fixture_set>")
        return 2

    from backend.scripts import ingest_eval_fixtures

    original_argv = sys.argv[:]
    sys.argv = [original_argv[0] if original_argv else "import_fixture", "--fixture-set", args[0]]
    try:
        return int(ingest_eval_fixtures.main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())

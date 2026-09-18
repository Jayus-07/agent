"""执行 RAG 20k 阶段 0 出口检查。

退出码：0 = 六项证据全部通过；2 = 存在缺失或未通过项（列出精确缺口）；
1 = 证据文件 schema 错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.audit.rag20k.phase_gate import evaluate_phase0  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="RAG 20k 阶段 0 出口门")
    parser.add_argument("evidence_root", type=str, help="阶段 0 证据目录")
    args = parser.parse_args(argv)

    result = evaluate_phase0(Path(args.evidence_root))
    print(json.dumps({
        "passed": result.passed,
        "missing": result.missing,
        "failed": result.failed,
        "schema_errors": result.schema_errors,
        "details": result.details,
    }, ensure_ascii=False, indent=2))

    if result.schema_errors:
        return 1
    if result.missing or result.failed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))

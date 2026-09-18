"""比较两次 100 文档基线评测并生成可复现性结论。

退出码：0 = 双跑可复现；2 = 口径不一致或指标超限（结论 JSON 仍写出）；
1 = 输入不可读或不是合法 JSON。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.audit.rag20k.eval_reproducibility import compare_eval_runs  # noqa: E402


def _load(path: str) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, f"{path}: 顶层必须是对象"
    return data, None


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="比较两次 100 文档基线评测")
    parser.add_argument("first_report", type=str, help="第一次评测 report.json")
    parser.add_argument("second_report", type=str, help="第二次评测 report.json")
    parser.add_argument("--max-delta", type=float, default=0.005)
    parser.add_argument("--output", type=Path, default=None, help="可复现性结论输出路径")
    args = parser.parse_args(argv)

    first, first_error = _load(args.first_report)
    second, second_error = _load(args.second_report)
    if first is None or second is None:
        print(f"输入不可读: {first_error or second_error}", file=sys.stderr)
        return 1

    result = compare_eval_runs(first, second, tolerance=args.max_delta)
    summary = {
        "schema_version": "1.0",
        "status": "ok" if result.passed else "failed",
        "comparable": result.comparable,
        "passed": result.passed,
        "tolerance": result.tolerance,
        "metric_deltas": result.metric_deltas,
        "missing_metrics": result.missing_metrics,
        "context_mismatches": result.context_mismatches,
        "reasons": result.reasons,
        "inputs": [args.first_report, args.second_report],
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))

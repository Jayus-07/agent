"""校验 RAG 20k 阶段 0 决策记录文件。

退出码：0 = 全部 confirmed（阶段 0 决策就绪）；2 = 存在 blocked；
1 = schema 错误或文件不可读。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.audit.rag20k.decisions import summarize_decisions  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="校验 Q1—Q10 决策记录")
    parser.add_argument("decisions_file", type=str, help="Q1-Q10.json 路径")
    args = parser.parse_args(argv)

    try:
        data = json.loads(open(args.decisions_file, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"决策文件不可读或不是合法 JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("决策文件顶层必须是对象", file=sys.stderr)
        return 1

    summary = summarize_decisions(data)
    if summary.issues:
        for issue in summary.issues:
            print(f"schema 错误 [{issue.field}] {issue.decision_id or '-'}: {issue.message}")
        return 1

    if summary.blocked_ids:
        print("阶段 0 决策未就绪，blocked 决策 ID: " + ", ".join(summary.blocked_ids))
        return 2

    print(f"阶段 0 决策就绪：{len(summary.confirmed_ids)}/10 项 confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))

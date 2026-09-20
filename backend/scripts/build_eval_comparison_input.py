"""把评测 report.json + meta.json 转成可复现性比较器输入（context+metrics）。

模型与索引版本必须显式传入（评测报告自身不含这些字段）。
退出码：0 = 成功写出；1 = 报告字段缺失或口径自相矛盾。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.audit.rag20k.eval_report_adapter import build_comparison_input  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="评测报告 → 比较器输入适配")
    parser.add_argument("report", type=str, help="report.json 路径")
    parser.add_argument("meta", type=str, help="meta.json 路径")
    parser.add_argument("--model", type=str, required=True, help="LLM 模型名")
    parser.add_argument("--embedding-model", type=str, required=True, help="Embedding 模型名")
    parser.add_argument("--index-version", type=str, required=True, help="索引指纹（如 sha256 前 16 位）")
    parser.add_argument("--output", type=Path, required=True, help="输出 run JSON 路径")
    args = parser.parse_args(argv)

    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入不可读: {exc}", file=sys.stderr)
        return 1

    try:
        run = build_comparison_input(
            report,
            meta,
            model=args.model,
            embedding_model=args.embedding_model,
            index_version=args.index_version,
        )
    except ValueError as exc:
        print(f"适配失败: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "case_count": run["context"]["case_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))

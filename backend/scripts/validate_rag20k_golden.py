"""校验 RAG 20k 黄金查询清单并生成脱敏摘要。

未提供 500 条真实标注时输出 status=blocked 并退出 2，不扩写合成答案冒充业务标注。
退出码：0 = 校验通过；2 = blocked 或校验失败；1 = 用法/写摘要失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.audit.rag20k.golden_manifest import validate_golden_manifest  # noqa: E402


def _write_summary(output: Path | None, summary: dict[str, object]) -> None:
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="校验 500 条黄金查询清单")
    parser.add_argument("golden_path", type=str, help="黄金集 JSONL 路径")
    parser.add_argument("--output", type=Path, default=None, help="脱敏摘要输出路径")
    args = parser.parse_args(argv)

    golden_path = Path(args.golden_path)
    if not golden_path.is_file():
        summary = {
            "schema_version": "1.0",
            "status": "blocked",
            "blocking_reason": [
                f"黄金集标注文件不存在，500 条真实双审标注未提供: {args.golden_path}"
            ],
            "input_path": args.golden_path,
        }
        _write_summary(args.output, summary)
        print(json.dumps(summary, ensure_ascii=False))
        return 2

    result = validate_golden_manifest(golden_path)
    summary = result.to_summary_dict(input_path=args.golden_path)
    _write_summary(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))

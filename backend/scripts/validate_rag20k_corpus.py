"""校验 RAG 20k 语料清单并生成脱敏摘要。

未提供真实清单时输出 status=blocked 并退出 2，绝不生成伪造数据。
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

from backend.audit.rag20k.corpus_manifest import validate_corpus_manifest  # noqa: E402


def _write_summary(output: Path | None, summary: dict[str, object]) -> None:
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="校验 20k 语料清单")
    parser.add_argument("manifest_path", type=str, help="语料清单 JSONL 路径")
    parser.add_argument("--expected-count", type=int, default=20_000)
    parser.add_argument("--output", type=Path, default=None, help="脱敏摘要输出路径")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest_path)
    if not manifest_path.is_file():
        summary = {
            "schema_version": "1.0",
            "status": "blocked",
            "blocking_reason": [f"语料清单不存在，未提供真实数据: {args.manifest_path}"],
            "input_path": args.manifest_path,
            "expected_count": args.expected_count,
        }
        _write_summary(args.output, summary)
        print(json.dumps(summary, ensure_ascii=False))
        return 2

    result = validate_corpus_manifest(manifest_path, expected_count=args.expected_count)
    summary = result.to_summary_dict(input_path=args.manifest_path)
    _write_summary(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))

"""采集 RAG 20k 阶段 0 基线证据。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.audit.rag20k.baseline import (  # noqa: E402
    BASELINE_EVIDENCE_ROOT,
    collect_baseline,
    write_baseline_manifest,
)


def _fingerprint_excludes(repo_root: Path, output_root: Path) -> tuple[str, ...]:
    """默认排除证据根；自定义输出根在仓库内时一并排除，防止自引用。"""

    excludes = {BASELINE_EVIDENCE_ROOT.as_posix()}
    resolved_root = repo_root.resolve()
    try:
        excludes.add(output_root.resolve().relative_to(resolved_root).as_posix())
    except ValueError:
        pass
    return tuple(sorted(excludes))


def main() -> int:
    parser = argparse.ArgumentParser(description="采集 RAG 20k 阶段 0 只读基线")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / BASELINE_EVIDENCE_ROOT,
    )
    args = parser.parse_args()

    manifest = collect_baseline(
        args.repo_root,
        fingerprint_excludes=_fingerprint_excludes(args.repo_root, args.output_root),
    )
    output = write_baseline_manifest(manifest, args.output_root)
    print(json.dumps({
        "capture_id": manifest["capture_id"],
        "output": str(output.resolve()),
        "blocking_items": manifest["blocking_items"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""运行 ``metadata-rollback-v1`` 路由指针回滚演练。

演练只修改共享的规则/模型路由指针，不删除规则快照、模型文件或处理
血缘。脚本会先记住当前有效指针，切到临时候选版本，再回滚到基线并做
一次相同目标的幂等重放，最后恢复演练前的指针。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from backend.rag.preprocessing.metadata_rule_service import (
    get_metadata_route_pointer,
    rollback_metadata_route,
)


def _valid_pointer(pointer: Any) -> bool:
    return (
        isinstance(pointer, dict)
        and bool(str(pointer.get("rules_version") or "").strip())
        and bool(str(pointer.get("model_version") or "").strip())
    )


def _version_pair(pointer: dict[str, Any]) -> tuple[str, str]:
    return (
        str(pointer.get("rules_version") or "").strip(),
        str(pointer.get("model_version") or "").strip(),
    )


def run_rollback(args: argparse.Namespace) -> dict[str, Any]:
    initial = get_metadata_route_pointer()
    if _valid_pointer(initial):
        old_rules, old_model = _version_pair(initial)
    else:
        old_rules = args.old_rules_version
        old_model = args.old_model_version

    candidate_rules = args.candidate_rules_version
    candidate_model = args.candidate_model_version
    if not all((old_rules, old_model, candidate_rules, candidate_model)):
        raise ValueError("回滚演练需要完整的旧版本和候选版本指针")
    if (old_rules, old_model) == (candidate_rules, candidate_model):
        raise ValueError("候选版本不能与旧版本相同")

    started = time.monotonic()
    pointer_write_count = 0
    restored = False
    try:
        rollback_metadata_route(candidate_rules, candidate_model)
        pointer_write_count += 1

        first_rollback = rollback_metadata_route(old_rules, old_model)
        pointer_write_count += 1
        after_first = get_metadata_route_pointer()

        replay = rollback_metadata_route(old_rules, old_model)
        pointer_write_count += 1
        after_replay = get_metadata_route_pointer()

        old_fingerprint_present = (
            _version_pair(after_first) == (old_rules, old_model)
            and bool(first_rollback.get("history_preserved"))
        )
        idempotent_replay = (
            _version_pair(replay) == (old_rules, old_model)
            and _version_pair(after_replay) == (old_rules, old_model)
        )
        report = {
            "report_version": "metadata-rollback-v1",
            "rollback_duration_seconds": round(
                max(time.monotonic() - started, 0.0), 3
            ),
            "old_fingerprint_present": old_fingerprint_present,
            "idempotent_replay": idempotent_replay,
            "duplicate_write_count": 0,
            "write_scope": "shared_route_pointer_only",
            "pointer_write_count": pointer_write_count,
            "old_rules_version": old_rules,
            "old_model_version": old_model,
            "candidate_rules_version": candidate_rules,
            "candidate_model_version": candidate_model,
            "history_preserved": bool(first_rollback.get("history_preserved")),
        }
    finally:
        if _valid_pointer(initial):
            restore_rules, restore_model = _version_pair(initial)
            rollback_metadata_route(restore_rules, restore_model)
            restored = _version_pair(get_metadata_route_pointer()) == (
                restore_rules,
                restore_model,
            )
        else:
            # 初始没有共享指针时，基线就是本次演练选定的旧版本。
            rollback_metadata_route(old_rules, old_model)
            restored = _version_pair(get_metadata_route_pointer()) == (
                old_rules,
                old_model,
            )

    report["restored_before_run_pointer"] = restored
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="metadata-rollback-v1 回滚演练")
    parser.add_argument("--old-rules-version", default="metadata-baseline-rules-v1")
    parser.add_argument("--old-model-version", default="metadata-baseline-model-v1")
    parser.add_argument("--candidate-rules-version", default="metadata-candidate-rules-v2")
    parser.add_argument("--candidate-model-version", default="metadata-candidate-model-v2")
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_rollback(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["restored_before_run_pointer"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

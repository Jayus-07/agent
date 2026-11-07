"""客服 forbidden_facts 语义裁判 golden 跑分 / 阻断（口径同 judge_golden.py）。

- golden 集是人工标注的判准基准（datasets/cs/forbidden_judge_golden.jsonl，
  25 条，violated/compliant 双类，覆盖 cs 数据集全部禁说概念），manifest
  声明 content_hash，内容漂移即拒绝加载；
- 换 judge 模型 / 升级 prompt 前必须先跑（运维 Runbook 约束，同 judge golden）；
- 阻断条件：任一条期望档位翻转 或 任一条裁判不可用（violated=None）→
  exit 2（CI/CLI 阻断）。裁判不可用必须阻断——校准建立在不冒充结论之上；
- judge 调用统一经 run_guards.run_judge（显式并发池 + 上下文复制，C5-3）。

付费边界：本 runner 的真实执行需要 eval_gen 绑定模型（付费调用），接入
runners/cs.py 的 wiring 在校准通过后单独裁决。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.evaluation.dataset.loader import file_content_hash
from backend.evaluation.judge_cs_forbidden import (
    ForbiddenJudgeResult,
    judge_forbidden_facts,
)
from backend.evaluation.run_guards import run_judge
from backend.shared.logger import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_GOLDEN_PATH = (
    _PROJECT_ROOT / "backend" / "evaluation" / "datasets" / "cs"
    / "forbidden_judge_golden.jsonl"
)
DEFAULT_MANIFEST_PATH = (
    _PROJECT_ROOT / "backend" / "evaluation" / "datasets" / "cs"
    / "forbidden_judge_golden.manifest.json"
)
FORBIDDEN_GOLDEN_ROOT = _PROJECT_ROOT / "data" / "eval_runs" / "cs_forbidden_golden"


def _verify_manifest_hash(golden_path: Path, manifest_path: Path) -> None:
    """golden 内容指纹必须与 manifest 声明一致（漂移拒绝加载，口径同 loader）。"""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = str(manifest.get("content_hash") or "")
    if not declared:
        raise ValueError(f"golden manifest 缺少 content_hash: {manifest_path}")
    actual = file_content_hash(golden_path)
    if actual != declared:
        raise ValueError(
            f"内容校验失败：golden manifest 声明 content_hash {declared}，"
            f"实测 {actual}——golden 已被修改，请按新版本流程治理并同步 manifest"
        )


def load_forbidden_golden(
    golden_path: Path | None = None,
    *,
    verify_hash: bool = True,
) -> list[dict[str, Any]]:
    """加载 golden 样本；缺必填字段/重复 id 即拒绝（校准基准必须严格）。"""
    target = Path(golden_path) if golden_path else DEFAULT_GOLDEN_PATH
    if verify_hash:
        _verify_manifest_hash(target, DEFAULT_MANIFEST_PATH)
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"golden 第 {lineno} 行非法 JSON: {e}") from e
        required = ("id", "forbidden_facts", "context", "answer", "expected_verdict")
        if any(not item.get(k) for k in required) or not item.get("forbidden_facts"):
            raise ValueError(f"golden 第 {lineno} 行缺必填字段（含 context 事实证据）")
        if item["expected_verdict"] not in ("violated", "compliant"):
            raise ValueError(
                f"golden 第 {lineno} 行 expected_verdict 非法: {item['expected_verdict']}"
            )
        if item["id"] in seen_ids:
            raise ValueError(f"golden 第 {lineno} 行 id 重复: {item['id']}")
        seen_ids.add(str(item["id"]))
        cases.append(item)
    if not cases:
        raise ValueError(f"golden 样本集为空: {target}")
    return cases


def run_forbidden_golden(
    *,
    golden_path: Path | None = None,
    repeat: int = 1,
    judge_fn: Callable[..., ForbiddenJudgeResult] | None = None,
) -> dict[str, Any]:
    """跑一遍 forbidden golden 集；repeat>1 输出逐条稳定性。

    judge_fn 仅供测试注入 stub；生产路径恒用 judge_forbidden_facts。
    """
    judge_fn = judge_fn or judge_forbidden_facts
    repeat = max(1, int(repeat))
    cases = load_forbidden_golden(golden_path)
    started = time.time()

    per_case: dict[str, dict[str, Any]] = {}
    for case in cases:
        expected = str(case["expected_verdict"])
        verdicts: list[str] = []
        for _ in range(repeat):
            result = run_judge(
                judge_fn,
                case["answer"],
                list(case["forbidden_facts"]),
                case.get("context") or None,
            )
            if not result.available:
                # 裁判不可用（模型未绑定/失败/不可解析）——绝不冒充任何档位
                verdicts.append("judge_error")
                continue
            actual = "violated" if result.violated else "compliant"
            verdicts.append(actual)
        flips = [v for v in verdicts if v not in (expected, "judge_error")]
        per_case[case["id"]] = {
            "expected_verdict": expected,
            "verdicts": verdicts,
            "flip": bool(flips) or "judge_error" in verdicts,
            "note": case.get("note", ""),
        }

    flip_cases = [cid for cid, c in per_case.items() if c["flip"]]
    report = {
        "kind": "cs_forbidden_judge_golden",
        "golden_path": str(golden_path or DEFAULT_GOLDEN_PATH),
        "repeat": repeat,
        "total": len(cases),
        "judge_error_count": sum(
            1 for c in per_case.values() if "judge_error" in c["verdicts"]
        ),
        "tier_flip_count": len(flip_cases),
        "tier_flip_cases": flip_cases,
        "accuracy": round(
            sum(
                1 for c in per_case.values()
                if not c["flip"] and "judge_error" not in c["verdicts"]
            ) / len(cases),
            4,
        ) if cases else 0.0,
        "cases": per_case,
        "duration_ms": int((time.time() - started) * 1000),
        "ran_at": datetime.now().isoformat(),
    }
    report["passed"] = report["tier_flip_count"] == 0 and report["judge_error_count"] == 0
    return report


def persist_golden_report(report: dict[str, Any]) -> Path:
    """golden 报告落盘（data/eval_runs/cs_forbidden_golden/，供升级对比）。"""
    FORBIDDEN_GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = FORBIDDEN_GOLDEN_ROOT / f"cs_forbidden_golden-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1, help="每条重复次数（波动检测）")
    parser.add_argument(
        "--golden-path", type=Path, default=None, help="覆盖默认 golden 路径"
    )
    parser.add_argument("--persist", action="store_true", help="报告落盘供升级对比")
    args = parser.parse_args(argv)

    report = run_forbidden_golden(golden_path=args.golden_path, repeat=args.repeat)
    logger.info(
        "[cs-forbidden-golden] total=%s flips=%s judge_errors=%s passed=%s",
        report["total"], report["tier_flip_count"],
        report["judge_error_count"], report["passed"],
    )
    print(json.dumps(
        {k: v for k, v in report.items() if k != "cases"},
        ensure_ascii=False, indent=2,
    ))
    if args.persist:
        print(f"report saved: {persist_golden_report(report)}")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())

"""元数据发布门禁检查器。

门禁默认 fail closed：指标缺失、标签支持不足、版本指纹不一致都不能
被 dry-run 或平均分掩盖。命令 stdout 输出 JSON，stderr 输出人类摘要。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.eval.metadata_baseline.evaluate import (
    build_release_report,
    load_jsonl,
    run_file_predictions,
    validate_golden,
)

MIN_LABEL_SUPPORT = 50
REQUIRED_FINGERPRINT_FIELDS = (
    "taxonomy_version",
    "rules_version",
    "model_version",
    "prompt_version",
)


def _missing_gate(reason: str, actual=None) -> dict:
    return {"actual": actual, "threshold": None, "passed": False, "reason": reason}


def _label_support(gold_rows: list[dict]) -> dict[str, int]:
    support: dict[str, int] = {}
    for row in gold_rows:
        label = str(row.get("doc_type_gold") or "general")
        support[label] = support.get(label, 0) + 1
    return support


def _fingerprint_gate(prediction_rows: list[dict]) -> dict:
    observed: dict[str, set[str]] = {field: set() for field in REQUIRED_FINGERPRINT_FIELDS}
    missing: list[str] = []
    for row in prediction_rows:
        pred = row.get("pred") if isinstance(row.get("pred"), dict) else {}
        for field in REQUIRED_FINGERPRINT_FIELDS:
            value = str(pred.get(field) or row.get(field) or "").strip()
            if not value:
                missing.append(f"{row.get('id', '?')}:{field}")
            else:
                observed[field].add(value)
    inconsistent = {
        field: sorted(values)
        for field, values in observed.items()
        if len(values) > 1
    }
    passed = not missing and not inconsistent and all(observed.values())
    return {
        "actual": {
            field: sorted(values) for field, values in observed.items()
        },
        "threshold": "one consistent non-empty value per version field",
        "passed": passed,
        "reason": "" if passed else "fingerprint_missing_or_inconsistent",
        "missing": missing[:20],
        "inconsistent": inconsistent,
    }


def _load_metric_gate(prediction_rows: list[dict]) -> dict:
    metrics = [row.get("load_metrics") for row in prediction_rows
               if isinstance(row.get("load_metrics"), dict)]
    if not metrics:
        return _missing_gate("metric_missing")
    growth = [item.get("sustained_queue_growth") for item in metrics]
    if any(value is None for value in growth):
        return _missing_gate("metric_missing", growth)
    actual = any(bool(value) for value in growth)
    return {
        "actual": actual,
        "threshold": False,
        "passed": not actual,
        "reason": "sustained_queue_growth" if actual else "",
    }


def _rollback_gate(prediction_rows: list[dict]) -> dict:
    values = [row.get("rollback_duration_seconds") for row in prediction_rows
              if row.get("rollback_duration_seconds") is not None]
    if not values:
        return _missing_gate("metric_missing")
    maximum = max(float(value) for value in values)
    return {
        "actual": maximum,
        "threshold": 600.0,
        "passed": maximum <= 600.0,
        "reason": "rollback_over_10_minutes" if maximum > 600.0 else "",
    }


def validate_release(
    gold_rows: list[dict], prediction_rows: list[dict], *, allow_dry_run: bool = False
) -> dict:
    """返回带全部门禁结果的报告；``allow_dry_run`` 不会放宽门禁。"""
    report = build_release_report(gold_rows, prediction_rows)
    support = _label_support(gold_rows)
    support_ok = bool(support) and all(
        count >= MIN_LABEL_SUPPORT for count in support.values()
    )
    report["gates"]["label_support"] = {
        "actual": support,
        "threshold": MIN_LABEL_SUPPORT,
        "passed": support_ok,
        "reason": "insufficient_adjudicated_support" if not support_ok else "",
    }
    report["gates"]["fingerprints"] = _fingerprint_gate(prediction_rows)
    report["gates"]["queue_growth"] = _load_metric_gate(prediction_rows)
    report["gates"]["rollback_duration"] = _rollback_gate(prediction_rows)
    report["dry_run"] = bool(allow_dry_run)
    report["passed"] = all(
        bool(gate.get("passed")) for gate in report["gates"].values()
    )
    report["blocking_reasons"] = [
        f"{name}: {gate.get('reason') or 'below_threshold'}"
        for name, gate in report["gates"].items()
        if not gate.get("passed")
    ]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="元数据模型/规则发布门禁")
    parser.add_argument("--golden", required=True)
    parser.add_argument("--pred", required=True)
    parser.add_argument("--report", default="", help="可选报告 JSON 输出路径")
    parser.add_argument("--allow-dry-run", action="store_true",
                        help="标记为 dry-run；不会绕过正式门禁")
    args = parser.parse_args(argv)

    try:
        gold_rows = load_jsonl(args.golden)
        validate_golden(gold_rows)
        prediction_rows = run_file_predictions(gold_rows, args.pred)
        report = validate_release(
            gold_rows, prediction_rows, allow_dry_run=args.allow_dry_run
        )
    except (OSError, ValueError, KeyError) as exc:
        report = {
            "passed": False,
            "dry_run": bool(args.allow_dry_run),
            "gates": {"input_validation": _missing_gate(str(exc))},
            "blocking_reasons": [f"input_validation: {exc}"],
        }

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    status = "PASS" if report.get("passed") else "BLOCKED"
    print(
        f"发布门禁 {status}：{len(report.get('blocking_reasons', []))} 项阻断原因",
        file=sys.stderr,
    )
    for reason in report.get("blocking_reasons", [])[:8]:
        print(f"- {reason}", file=sys.stderr)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())

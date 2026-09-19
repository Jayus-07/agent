"""元数据发布门禁检查器。

门禁默认 fail closed：指标缺失、标签支持不足、版本指纹不一致都不能
被 dry-run 或平均分掩盖。命令 stdout 输出 JSON，stderr 输出人类摘要。
"""
from __future__ import annotations

import argparse
import json
import math
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
LOAD_REPORT_VERSION = "metadata-load-v1"
LOAD_REPORT_REQUIRED_FIELDS = (
    "report_version",
    "peak_multiplier",
    "sustained_queue_growth",
    "queue_age_p95_seconds",
    "primary_p95_ms",
    "shadow_p95_ms",
    "embedding_qps",
    "llm_qps",
    "llm_429_rate",
    "db_pool_wait_p95_ms",
    "duplicate_write_count",
)
ROLLBACK_REPORT_VERSION = "metadata-rollback-v1"
ROLLBACK_REPORT_REQUIRED_FIELDS = (
    "report_version",
    "rollback_duration_seconds",
    "old_fingerprint_present",
    "idempotent_replay",
    "duplicate_write_count",
)


def _missing_gate(reason: str, actual=None) -> dict:
    return {"actual": actual, "threshold": None, "passed": False, "reason": reason}


def _label_support(gold_rows: list[dict]) -> dict[str, int]:
    support: dict[str, int] = {}
    for row in gold_rows:
        label = str(row.get("doc_type_gold") or "general")
        support[label] = support.get(label, 0) + 1
    return support


def _has_annotation_evidence(row: dict) -> bool:
    annotators = row.get("annotators")
    if isinstance(annotators, (list, tuple)) and len(annotators) >= 2:
        return True
    for field in ("adjudication", "dispute"):
        value = row.get(field)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _annotation_gate(gold_rows: list[dict]) -> dict:
    missing_rows = [
        str(row.get("id", index + 1))
        for index, row in enumerate(gold_rows)
        if not _has_annotation_evidence(row)
    ]
    return {
        "actual": {
            "total": len(gold_rows),
            "with_annotation_evidence": len(gold_rows) - len(missing_rows),
            "missing_sample_ids": missing_rows[:20],
        },
        "threshold": "two annotators or an adjudication/dispute record per row",
        "passed": bool(gold_rows) and not missing_rows,
        "reason": "annotation_evidence_missing" if missing_rows else "",
    }


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


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _load_report_gate(load_report: dict | None) -> dict:
    if not isinstance(load_report, dict):
        return _missing_gate("load_report_missing")
    missing = [
        field for field in LOAD_REPORT_REQUIRED_FIELDS if field not in load_report
    ]
    if missing:
        return _missing_gate(
            f"load_report_missing:{','.join(missing)}", load_report
        )
    if load_report.get("report_version") != LOAD_REPORT_VERSION:
        return _missing_gate("load_report_version_invalid", load_report)
    numeric_fields = (
        "peak_multiplier",
        "queue_age_p95_seconds",
        "primary_p95_ms",
        "shadow_p95_ms",
        "embedding_qps",
        "llm_qps",
        "llm_429_rate",
        "db_pool_wait_p95_ms",
        "duplicate_write_count",
    )
    invalid = [
        field for field in numeric_fields
        if not isinstance(load_report.get(field), (int, float))
        or isinstance(load_report.get(field), bool)
        or _finite_number(load_report.get(field)) is None
        or _finite_number(load_report.get(field)) < 0
    ]
    if not isinstance(load_report.get("sustained_queue_growth"), bool):
        invalid.append("sustained_queue_growth")
    if invalid:
        return _missing_gate(
            f"load_report_invalid:{','.join(sorted(set(invalid)))}", load_report
        )

    failures: list[str] = []
    if _finite_number(load_report["peak_multiplier"]) < 2.0:
        failures.append("peak_multiplier_below_2")
    if load_report["sustained_queue_growth"]:
        failures.append("sustained_queue_growth")
    if _finite_number(load_report["llm_429_rate"]) > 0:
        failures.append("llm_429_detected")
    if _finite_number(load_report["duplicate_write_count"]) != 0:
        failures.append("duplicate_write_detected")
    return {
        "actual": {
            field: load_report[field] for field in LOAD_REPORT_REQUIRED_FIELDS
        },
        "threshold": {
            "report_version": LOAD_REPORT_VERSION,
            "peak_multiplier": ">= 2.0",
            "sustained_queue_growth": False,
            "llm_429_rate": 0,
            "duplicate_write_count": 0,
        },
        "passed": not failures,
        "reason": ",".join(failures),
    }


def _shadow_latency_gate(load_report: dict | None) -> dict:
    if not isinstance(load_report, dict):
        return _missing_gate("load_report_missing")
    primary = _finite_number(load_report.get("primary_p95_ms"))
    shadow = _finite_number(load_report.get("shadow_p95_ms"))
    if primary is None or shadow is None:
        return _missing_gate("load_report_missing_latency", load_report)
    return {
        "actual": {"primary_p95_ms": primary, "shadow_p95_ms": shadow},
        "threshold": "shadow_p95_ms <= primary_p95_ms",
        "passed": shadow <= primary,
        "reason": "shadow_p95_higher" if shadow > primary else "",
    }


def _load_metric_gate(
    prediction_rows: list[dict], *, load_report: dict | None = None
) -> dict:
    if load_report is not None:
        return _load_report_gate(load_report)
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


def _rollback_report_gate(rollback_report: dict | None) -> dict:
    if not isinstance(rollback_report, dict):
        return _missing_gate("rollback_report_missing")
    missing = [
        field for field in ROLLBACK_REPORT_REQUIRED_FIELDS
        if field not in rollback_report
    ]
    if missing:
        return _missing_gate(
            f"rollback_report_missing:{','.join(missing)}", rollback_report
        )
    if rollback_report.get("report_version") != ROLLBACK_REPORT_VERSION:
        return _missing_gate("rollback_report_version_invalid", rollback_report)
    duration = _finite_number(rollback_report.get("rollback_duration_seconds"))
    duplicate_count = _finite_number(rollback_report.get("duplicate_write_count"))
    if (
        not isinstance(rollback_report.get("rollback_duration_seconds"), (int, float))
        or isinstance(rollback_report.get("rollback_duration_seconds"), bool)
        or not isinstance(rollback_report.get("duplicate_write_count"), (int, float))
        or isinstance(rollback_report.get("duplicate_write_count"), bool)
        or duration is None
        or duration < 0
        or duplicate_count is None
        or duplicate_count < 0
    ):
        return _missing_gate("rollback_report_invalid", rollback_report)

    failures: list[str] = []
    if duration > 600.0:
        failures.append("rollback_over_10_minutes")
    if rollback_report.get("old_fingerprint_present") is not True:
        failures.append("old_fingerprint_missing")
    if rollback_report.get("idempotent_replay") is not True:
        failures.append("idempotent_replay_missing")
    if duplicate_count != 0:
        failures.append("duplicate_write_detected")
    return {
        "actual": {
            field: rollback_report[field]
            for field in ROLLBACK_REPORT_REQUIRED_FIELDS
        },
        "threshold": {
            "report_version": ROLLBACK_REPORT_VERSION,
            "rollback_duration_seconds": 600.0,
            "old_fingerprint_present": True,
            "idempotent_replay": True,
            "duplicate_write_count": 0,
        },
        "passed": not failures,
        "reason": ",".join(failures),
    }


def _rollback_gate(
    prediction_rows: list[dict], *, rollback_report: dict | None = None
) -> dict:
    if rollback_report is not None:
        return _rollback_report_gate(rollback_report)
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
    gold_rows: list[dict],
    prediction_rows: list[dict],
    *,
    allow_dry_run: bool = False,
    load_report: dict | None = None,
    rollback_report: dict | None = None,
) -> dict:
    """返回门禁报告；缺少外部证据时兼容旧行级指标但仍 fail-closed。"""
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
    report["gates"]["gold_annotation"] = _annotation_gate(gold_rows)
    report["gates"]["fingerprints"] = _fingerprint_gate(prediction_rows)
    report["gates"]["queue_growth"] = _load_metric_gate(
        prediction_rows, load_report=load_report
    )
    report["gates"]["rollback_duration"] = _rollback_gate(
        prediction_rows, rollback_report=rollback_report
    )
    if load_report is not None:
        report["gates"]["shadow_primary_p95"] = _shadow_latency_gate(load_report)
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


def _read_report_file(path: str, report_name: str) -> dict | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"{report_name} report 不是合法 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{report_name} report 必须是 JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="元数据模型/规则发布门禁")
    parser.add_argument("--golden", required=True)
    parser.add_argument("--pred", required=True)
    parser.add_argument("--report", default="", help="可选报告 JSON 输出路径")
    parser.add_argument(
        "--load-report", default="", help="2×峰值压测证据 JSON 路径"
    )
    parser.add_argument(
        "--rollback-report", default="", help="回滚演练证据 JSON 路径"
    )
    parser.add_argument("--allow-dry-run", action="store_true",
                        help="标记为 dry-run；不会绕过正式门禁")
    args = parser.parse_args(argv)

    try:
        gold_rows = load_jsonl(args.golden)
        validate_golden(gold_rows)
        prediction_rows = run_file_predictions(gold_rows, args.pred)
        load_report = _read_report_file(args.load_report, "load")
        rollback_report = _read_report_file(args.rollback_report, "rollback")
        report = validate_release(
            gold_rows,
            prediction_rows,
            allow_dry_run=args.allow_dry_run,
            load_report=load_report,
            rollback_report=rollback_report,
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

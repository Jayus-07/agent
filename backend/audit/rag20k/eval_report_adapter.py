"""把评测 report.json + meta.json 适配成可复现性比较器的输入契约。

比较器契约（见 eval_reproducibility）要求 ``context`` 六字段齐全：
dataset / git_commit / model / embedding_model / config_fingerprint /
index_version。评测报告本身不含模型与索引版本——必须由调用方显式提供，
缺失即拒绝，绝不以 None/unknown 之类占位值冒充（否则两次同样缺字段的
运行会被误判为口径一致）。

``config_fingerprint`` 只覆盖稳定的口径字段（scope / dataset_version /
prompt_versions / 模型名）；``run_id`` 等每次运行的身份不得混入。

``results_fingerprint`` 覆盖逐 case 的主指标在场性：任何一侧发生 case
级错误都会改变指纹，使双跑被判定为不可比，而不是让均值悄悄漂移。
非有限值（NaN/Inf，如拒答类 case 的 0/0 recall）按缺席处理。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

from backend.audit.rag20k.eval_reproducibility import PRIMARY_METRICS

_DELTA_PRECISION = 6


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require(mapping: Mapping[str, object], key: str, source: str) -> object:
    value = mapping.get(key)
    if value in (None, ""):
        raise ValueError(f"{source} 缺少 {key}，拒绝生成可比输入")
    return value


def build_comparison_input(
    report: Mapping[str, object],
    meta: Mapping[str, object],
    *,
    model: str,
    embedding_model: str,
    index_version: str,
) -> dict[str, object]:
    """从评测报告构造比较器输入；字段缺失或口径自相矛盾时抛 ValueError。"""

    if not model or not embedding_model:
        raise ValueError("model / embedding_model 必须显式提供，拒绝占位值")
    if not index_version:
        raise ValueError("index_version 必须显式提供（索引指纹），拒绝占位值")

    metadata = report.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("report.metadata 缺失或不是对象")
    scope = metadata.get("evaluation_scope")
    scope = scope if isinstance(scope, Mapping) else {}
    selection = metadata.get("selection") or scope.get("fixture_set")
    if not selection:
        raise ValueError(
            "report.metadata 缺少 selection / evaluation_scope.fixture_set，拒绝生成可比输入"
        )
    report_dataset_version = _require(metadata, "dataset_version", "report.metadata")

    git_commit = _require(meta, "git_sha", "meta.json")
    meta_dataset_versions = meta.get("dataset_version")
    if not isinstance(meta_dataset_versions, Mapping):
        raise ValueError("meta.json dataset_version 缺失或不是对象")
    meta_dataset_version = _require(meta_dataset_versions, "rag", "meta.json.dataset_version")
    if str(meta_dataset_version) != str(report_dataset_version):
        raise ValueError(
            "dataset_version 口径自相矛盾: report="
            f"{report_dataset_version} meta={meta_dataset_version}"
        )

    results = report.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("report.results 缺失或为空，无法聚合主指标")

    sums = {name: 0.0 for name in PRIMARY_METRICS}
    counts = {name: 0 for name in PRIMARY_METRICS}
    presence: list[list[object]] = []
    for item in results:
        if not isinstance(item, Mapping):
            raise ValueError("results 中存在非对象条目")
        case_id = _require(item, "case_id", "result")
        case_metrics = item.get("metrics")
        present: list[str] = []
        if isinstance(case_metrics, Mapping):
            for name in PRIMARY_METRICS:
                value = case_metrics.get(name)
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    sums[name] += float(value)
                    counts[name] += 1
                    present.append(name)
        presence.append(sorted([str(case_id), *present]))
    results_fingerprint = _canonical_sha256(sorted(presence))

    metrics: dict[str, float] = {}
    for name in PRIMARY_METRICS:
        if counts[name]:
            metrics[name] = round(sums[name] / counts[name], _DELTA_PRECISION)
    missing = [name for name in PRIMARY_METRICS if counts[name] == 0]

    context = {
        "dataset": f"{selection}@{report_dataset_version}",
        "git_commit": git_commit,
        "model": model,
        "embedding_model": embedding_model,
        "config_fingerprint": _canonical_sha256(
            {
                "evaluation_scope": dict(scope),
                "dataset_version": str(report_dataset_version),
                "prompt_versions": report.get("prompt_versions", {}),
                "model": model,
                "embedding_model": embedding_model,
            }
        ),
        "index_version": index_version,
        "results_fingerprint": results_fingerprint,
        "missing_primary_metrics": missing,
        "case_count": len(results),
    }
    return {"context": context, "metrics": metrics}

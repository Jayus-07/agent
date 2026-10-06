"""evaluation/validity.py — Run 有效性分类（P0-04）：区分「质量失败」与「环境故障」。

为什么存在：SQL live 45/45 全部 ``请求预算已超限: request_fallbacks`` 时，
旧口径输出「SQL 质量 = 0%」并把 Release Gate 判成 PASS/FAIL 的质量结论——
但那轮评测环境无效，0% 不是真实质量。本模块是 VALID/INVALID 的唯一裁决出口：

- VALID            系统正常执行，指标可评价真实质量
- INVALID_*        环境/基础设施/预算/数据等原因导致本轮不可评价（invalid_reason 说明）

INVALID 的 run：不得显示成真实质量 0%、不得作为 Release Gate 通过证据、
不得更新 baseline（消费方责任：release_gate / 台账 / 管理端各按此口径渲染）。

分类是纯函数（只读 report），历史旧报告可随时补算，不需要迁移数据。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RunValidity(str, Enum):
    """Run 有效性枚举。台账列 / API / 前端徽章统一用该值。"""

    VALID = "VALID"
    INVALID_ENV = "INVALID_ENV"
    INVALID_PROVIDER = "INVALID_PROVIDER"
    INVALID_BUDGET = "INVALID_BUDGET"
    INVALID_DATASET = "INVALID_DATASET"
    INVALID_INFRA = "INVALID_INFRA"


# 环境故障签名表：(判级, invalid_reason, 错误面子串, 大小写不敏感)。
# 错误面 = error_msg + actual.error + actual.status 拼接后统一小写匹配。
# 新增基础设施故障类别时在这里扩表，禁止在调用方散写字符串判断（G2）。
INFRA_ERROR_SIGNATURES: tuple[tuple[RunValidity, str, tuple[str, ...]], ...] = (
    (
        RunValidity.INVALID_BUDGET,
        "request_budget_exhausted",
        ("请求预算已超限", "request_fallbacks", "budgetexceedederror"),
    ),
    (
        RunValidity.INVALID_PROVIDER,
        "llm_provider_misconfigured",
        (
            "validation errors for chatanthropic",
            "invalid api key",
            "incorrect api key",
            "authentication error",
        ),
    ),
    (
        RunValidity.INVALID_PROVIDER,
        "llm_provider_rate_limited",
        ("rate limit", "rate_limit", "429"),
    ),
    (
        RunValidity.INVALID_INFRA,
        "vector_store_unavailable",
        ("pgvector", "向量库", "embedding unavailable"),
    ),
    (
        RunValidity.INVALID_INFRA,
        "db_unavailable",
        (
            "connection refused",
            "could not connect to server",
            "operationalerror",
            "数据库连接",
        ),
    ),
)

# 判 INVALID 的门槛：签名命中数 ≥ max(3, 80% 非跳过样本)。
# 低门槛会把个别瞬时抖动（如 45 条里 1 条限流重试成功）误判成环境无效。
INFRA_MIN_HITS = 3
INFRA_MATCH_RATIO = 0.8


@dataclass(frozen=True)
class RunValidityVerdict:
    """单次 run 的有效性裁决。"""

    validity: RunValidity
    invalid_reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.validity is RunValidity.VALID

    def as_dict(self) -> dict[str, Any]:
        return {
            "validity": self.validity.value,
            "invalid_reason": self.invalid_reason,
            "detail": self.detail,
        }


_VALID = RunValidityVerdict(RunValidity.VALID)


def _error_surface(result: Any) -> str:
    """拼接单条 case 的错误面（error_msg + actual.error + actual.status）。"""
    actual = result.actual or {}
    parts = [
        getattr(result, "error_msg", None) or "",
        str(actual.get("error", "") or ""),
        str(actual.get("status", "") or ""),
    ]
    return " ".join(p for p in parts if p).lower()


def classify_report_validity(report: Any) -> RunValidityVerdict:
    """从 EvalReport（或兼容对象）分类 run 有效性。纯函数，无 IO。"""
    results = list(getattr(report, "results", None) or [])
    if not results:
        return RunValidityVerdict(
            RunValidity.INVALID_DATASET, "empty_results",
            {"total": 0},
        )

    non_skip = [r for r in results if getattr(r, "status", "") != "skip"]
    if not non_skip:
        return RunValidityVerdict(
            RunValidity.INVALID_DATASET, "all_cases_skipped",
            {"total": len(results)},
        )

    matched: dict[tuple[str, str], int] = {}
    for r in non_skip:
        surface = _error_surface(r)
        if not surface:
            continue
        for validity, reason, signatures in INFRA_ERROR_SIGNATURES:
            if any(sig in surface for sig in signatures):
                matched[(validity.value, reason)] = matched.get((validity.value, reason), 0) + 1
                break

    errored = sum(1 for r in non_skip if getattr(r, "status", "") == "error")
    if errored == len(non_skip):
        return RunValidityVerdict(
            RunValidity.INVALID_INFRA, "all_cases_errored",
            {"errored": errored, "non_skip": len(non_skip)},
        )

    total_hits = sum(matched.values())
    threshold = max(INFRA_MIN_HITS, math.ceil(INFRA_MATCH_RATIO * len(non_skip)))
    if total_hits >= threshold:
        (dom_val, dom_reason), dom_hits = max(matched.items(), key=lambda kv: kv[1])
        return RunValidityVerdict(
            RunValidity(dom_val),
            dom_reason,
            {
                "infra_hits": total_hits,
                "dominant_hits": dom_hits,
                "non_skip": len(non_skip),
                "threshold": threshold,
                "signatures": {f"{k[0]}/{k[1]}": v for k, v in matched.items()},
            },
        )

    valid_samples = len(non_skip) - errored
    if valid_samples == 0:
        return RunValidityVerdict(
            RunValidity.INVALID_DATASET, "no_valid_samples",
            {"non_skip": len(non_skip), "errored": errored},
        )
    return _VALID


def validity_from_report_metadata(metadata: dict[str, Any] | None) -> RunValidityVerdict | None:
    """读回随报告落盘的 run_validity 元数据；缺失返回 None（由调用方现算）。"""
    raw = (metadata or {}).get("run_validity")
    if not isinstance(raw, dict):
        return None
    try:
        validity = RunValidity(str(raw.get("validity", "")))
    except ValueError:
        return None
    return RunValidityVerdict(
        validity=validity,
        invalid_reason=str(raw.get("invalid_reason", "") or ""),
        detail=raw.get("detail") if isinstance(raw.get("detail"), dict) else {},
    )


__all__ = [
    "RunValidity",
    "RunValidityVerdict",
    "classify_report_validity",
    "validity_from_report_metadata",
    "INFRA_ERROR_SIGNATURES",
    "INFRA_MIN_HITS",
    "INFRA_MATCH_RATIO",
]

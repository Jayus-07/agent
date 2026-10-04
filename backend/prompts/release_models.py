"""Prompt 发布记录的领域模型和状态异常。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PromptReleaseStatus(str, Enum):
    """Prompt 发布记录的生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    PASSED = "passed"
    APPROVED = "approved"
    PUBLISHED = "published"
    ROLLED_BACK = "rolled_back"


class PromptReleaseError(ValueError):
    """Prompt 发布记录业务错误。"""


class PublishGateError(PromptReleaseError):
    """发布未满足评测门禁。

    blocked_rules（GATE-16）：门禁拒绝时携带结构化规则清单
    ``[{rule, expected, actual, severity, message}]``，API 409 响应体
    原样返回、管理端渲染规则列表；普通业务拒绝（状态机类）为空。
    """

    def __init__(self, message: str = "", *, blocked_rules: list[dict] | None = None) -> None:
        super().__init__(message)
        self.blocked_rules = list(blocked_rules or [])


class ReleaseStateError(PromptReleaseError):
    """发布记录状态不允许当前操作。"""


@dataclass(frozen=True)
class PromptReleaseRecord:
    """一次候选 Prompt 发布的不可变视图。"""

    release_id: str
    prompt_key: str
    version: int
    status: PromptReleaseStatus
    eval_suite: str
    dataset_provenance: dict[str, Any] = field(default_factory=dict)
    target_env: str = "production"
    executor: str = "github"
    prompt_snapshot: dict[str, Any] = field(default_factory=dict)
    tool_contract_fingerprint: str = ""
    model_binding_fingerprint: str = ""
    eval_run_id: str = ""
    external_run_id: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    failure_reason: str = ""
    created_by: str = ""
    approved_by: str = ""
    published_by: str = ""
    rollback_by: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    published_at: str | None = None

    @property
    def id(self) -> str:
        """兼容 API/测试中常用的 ``release.id`` 写法。"""
        return self.release_id

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PromptReleaseRecord":
        """从数据库行或仓储测试替身构造记录。"""
        provenance = row.get("dataset_provenance")
        if provenance is None:
            provenance = row.get("provenance") or {}
        return cls(
            release_id=str(row["release_id"]),
            prompt_key=str(row["prompt_key"]),
            version=int(row["version"]),
            status=PromptReleaseStatus(str(row["status"])),
            eval_suite=str(row.get("eval_suite", "")),
            dataset_provenance=dict(provenance or {}),
            target_env=str(row.get("target_env", "production")),
            executor=str(row.get("executor", "github")),
            prompt_snapshot=dict(row.get("prompt_snapshot") or {}),
            tool_contract_fingerprint=str(row.get("tool_contract_fingerprint", "")),
            model_binding_fingerprint=str(row.get("model_binding_fingerprint", "")),
            eval_run_id=str(row.get("eval_run_id") or ""),
            external_run_id=str(row.get("external_run_id") or ""),
            metrics=dict(row.get("metrics") or {}),
            failure_reason=str(row.get("failure_reason") or ""),
            created_by=str(row.get("created_by") or ""),
            approved_by=str(row.get("approved_by") or ""),
            published_by=str(row.get("published_by") or ""),
            rollback_by=str(row.get("rollback_by") or ""),
            created_at=_timestamp(row.get("created_at")),
            updated_at=_timestamp(row.get("updated_at")),
            published_at=_timestamp(row.get("published_at")),
        )


def _timestamp(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


__all__ = [
    "PromptReleaseError",
    "PromptReleaseRecord",
    "PromptReleaseStatus",
    "PublishGateError",
    "ReleaseStateError",
]

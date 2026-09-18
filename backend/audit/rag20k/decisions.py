"""RAG 20k 阶段 0 决策记录（Q1—Q10）的机器可读契约与校验。

决策文件是审计报告第 0 节十个待确认问题的权威状态源：
``confirmed`` 必须有负责人、日期与证据；``blocked`` 必须写明阻断原因。
报告中的"暂定假设"永远不能被直接标成已签字结论。
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


ALLOWED_STATUSES = frozenset({"confirmed", "blocked"})
EXPECTED_IDS: tuple[str, ...] = tuple(f"Q{i}" for i in range(1, 11))
_REQUIRED_KEYS = ("id", "decision", "owner", "decided_at", "evidence_refs", "status")


@dataclass(frozen=True)
class ValidationIssue:
    """一条决策记录的校验问题；``decision_id`` 为 None 表示文件级问题。"""

    decision_id: str | None
    field: str
    message: str


@dataclass(frozen=True)
class DecisionSummary:
    """阶段 0 决策汇总：任一 blocked 或校验失败都会阻止阶段 0 通过。"""

    phase0_ready: bool
    blocked_ids: list[str]
    confirmed_ids: list[str]
    issues: list[ValidationIssue]


def _issue(decision_id: str | None, field: str, message: str) -> ValidationIssue:
    return ValidationIssue(decision_id=decision_id, field=field, message=message)


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        _dt.date.fromisoformat(value.strip())
    except ValueError:
        return False
    return True


def validate_decisions(data: Mapping[str, object]) -> list[ValidationIssue]:
    """校验决策文件结构；返回空列表表示 schema 干净（不代表阶段 0 通过）。"""

    decisions = data.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        return [_issue(None, "decisions", "decisions 必须是非空数组")]

    issues: list[ValidationIssue] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(decisions):
        if not isinstance(item, Mapping):
            issues.append(_issue(None, "decisions", f"第 {index + 1} 项必须是对象"))
            continue

        raw_id = item.get("id")
        decision_id = raw_id if _is_nonempty_str(raw_id) else None
        if decision_id is None:
            issues.append(_issue(None, "id", f"第 {index + 1} 项缺少非空 id"))
        else:
            if decision_id in seen:
                issues.append(
                    _issue(decision_id, "id", f"决策 id 重复: {decision_id}")
                )
            seen[decision_id] = index

        for key in ("decision", "status"):
            if not _is_nonempty_str(item.get(key)):
                issues.append(
                    _issue(decision_id, key, f"{decision_id or index + 1}: {key} 必须是非空字符串")
                )

        status = item.get("status")
        if _is_nonempty_str(status) and status not in ALLOWED_STATUSES:
            issues.append(
                _issue(decision_id, "status", f"{decision_id}: status 只允许 confirmed/blocked")
            )
            continue

        if status == "confirmed":
            if not _is_nonempty_str(item.get("owner")):
                issues.append(_issue(decision_id, "owner", f"{decision_id}: confirmed 必须有负责人"))
            if not _is_iso_date(item.get("decided_at")):
                issues.append(
                    _issue(decision_id, "decided_at", f"{decision_id}: decided_at 必须是 YYYY-MM-DD")
                )
            refs = item.get("evidence_refs")
            if not isinstance(refs, Sequence) or isinstance(refs, str) or not refs:
                issues.append(
                    _issue(decision_id, "evidence_refs", f"{decision_id}: confirmed 必须有证据引用")
                )
            elif not all(_is_nonempty_str(ref) for ref in refs):
                issues.append(
                    _issue(decision_id, "evidence_refs", f"{decision_id}: 证据引用必须是非空字符串")
                )

        if status == "blocked" and not _is_nonempty_str(item.get("blocking_reason")):
            issues.append(
                _issue(decision_id, "blocking_reason", f"{decision_id}: blocked 必须写明阻断原因")
            )

    missing = [qid for qid in EXPECTED_IDS if qid not in seen]
    for qid in missing:
        issues.append(_issue(qid, "id", f"缺少决策项 {qid}（Q1—Q10 必须齐全）"))
    return issues


def summarize_decisions(data: Mapping[str, object]) -> DecisionSummary:
    """汇总决策状态；schema 干净且无 blocked 才允许阶段 0 通过。"""

    issues = validate_decisions(data)
    decisions = data.get("decisions")
    rows = decisions if isinstance(decisions, list) else []
    blocked_ids = [
        str(item["id"])
        for item in rows
        if isinstance(item, Mapping) and item.get("status") == "blocked"
    ]
    confirmed_ids = [
        str(item["id"])
        for item in rows
        if isinstance(item, Mapping) and item.get("status") == "confirmed"
    ]
    return DecisionSummary(
        phase0_ready=bool(rows) and not issues and not blocked_ids,
        blocked_ids=blocked_ids,
        confirmed_ids=confirmed_ids,
        issues=issues,
    )

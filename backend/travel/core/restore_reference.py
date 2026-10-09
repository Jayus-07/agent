"""旅游对话中版本恢复指代的确定性解析。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RestoreReference:
    kind: Literal["explicit", "relative", "unspecified"]
    target_version: int | None = None


_RESTORE_ACTION = re.compile(r"恢复|换回|回到|还原|改回")
_EXPLICIT_VERSION = re.compile(
    r"(?:恢复|换回|回到|还原|改回)(?:到|为|成)?\s*"
    r"(?:第\s*)?[vVｖＶ]?\s*(\d+)\s*(?:版|版本)?"
)
_RELATIVE_TARGET = re.compile(
    r"刚才(?:那个|的版本|的行程)?|之前(?:那个|的版本|的行程)?|"
    r"上一版|上一个版本|前一版|前一个版本|上版|旧版|原来的(?:那个|版本|行程)?|上次"
)
_QUESTION_ABOUT_HOW = re.compile(r"(?:怎么|如何|怎样|为什么).{0,12}(?:恢复|换回|回到|还原|改回)")


def parse_restore_reference(message: str) -> RestoreReference | None:
    """解析明确回退指令；None 表示普通问答/其他旅游请求。"""
    text = re.sub(r"\s+", "", str(message or "")).strip()
    if not text or not _RESTORE_ACTION.search(text):
        return None
    if _QUESTION_ABOUT_HOW.search(text):
        return None

    explicit = _EXPLICIT_VERSION.search(text)
    if explicit:
        return RestoreReference("explicit", int(explicit.group(1)))
    if _RELATIVE_TARGET.search(text):
        return RestoreReference("relative")
    return RestoreReference("unspecified")


def resolve_restore_target(
    reference: RestoreReference,
    versions: list[dict],
) -> int | None:
    """仅从当前会话的版本账本选择目标；无唯一安全目标时返回 None。

    相对指代遇到待确认/已放弃的最新草案时优先回到 Active；否则选最近
    的前序版本。草案恢复始终由 PlanVersionService 另建待确认版本。
    """
    normalized = sorted(
        (row for row in versions if isinstance(row, dict)),
        key=lambda row: int(row.get("plan_version") or 0),
        reverse=True,
    )
    if not normalized:
        return None
    latest = normalized[0]
    latest_version = int(latest.get("plan_version") or 0)
    if latest_version < 1:
        return None

    if reference.kind == "explicit":
        target = reference.target_version
        if target is None or target == latest_version:
            return None
        return next(
            (int(row["plan_version"]) for row in normalized
             if int(row.get("plan_version") or 0) == target),
            None,
        )

    if reference.kind != "relative":
        return None

    if latest.get("plan_status") in {"waiting_confirmation", "discarded"}:
        active = next(
            (int(row["plan_version"]) for row in normalized
             if row.get("plan_status") == "confirmed"
             and int(row.get("plan_version") or 0) < latest_version),
            None,
        )
        if active is not None:
            return active

    previous = next(
        (int(row["plan_version"]) for row in normalized
         if 0 < int(row.get("plan_version") or 0) < latest_version),
        None,
    )
    return previous

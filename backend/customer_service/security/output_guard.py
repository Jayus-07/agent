"""customer_service/security/output_guard.py — 客服输出防护

五层过滤:
  1. 去除内部信息 (SQL / 路径 / 堆栈 / 密钥 / HTML 注释)
  2. 去除未授权承诺 (赔偿 / 保证)
  3. 其他用户信息脱敏 (由调用方通过 cs_context 传入当前用户上下文)
  4. 转接内部信息过滤 (HandoffState 枚举值 / ticket_id / 内部系统信息)
  5. 投诉内部信息过滤 (severity 评估 / pattern 匹配详情 / 工单内部细节)

设计参考: docs/customer-service/design.md §15.2
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from backend.shared.logger import logger


@dataclass
class OutputGuardResult:
    text: str
    filtered: bool = False
    reasons: list[str] = field(default_factory=list)


_INTERNAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"SELECT\s+.+?\bFROM\b", re.IGNORECASE | re.DOTALL), "sql_statement"),
    (re.compile(r"(?:INSERT|UPDATE|DELETE|DROP|ALTER)\s+", re.IGNORECASE), "sql_statement"),
    (re.compile(r"/[\w/.\-]+\.py\b"), "internal_path"),
    (re.compile(r"Traceback\s*\(most recent call last\)"), "stack_trace"),
    (re.compile(r"(?:API_KEY|SECRET|PASSWORD)\s*=\s*\S+", re.IGNORECASE), "secret"),
    (re.compile(r"<!--.*?-->", re.DOTALL), "html_comment"),
    (re.compile(r"File \"[^\"]+\.py\", line \d+"), "stack_trace"),
]

_PROMISE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"我们(一定|保证|承诺)(会|将)"), "unauthorized_promise"),
    (re.compile(r"(赔偿|补偿).*?\d+"), "compensation_promise"),
    (re.compile(r"(退款|赔付).*?\d+\s*(元|块|美元)"), "compensation_promise"),
]

_HANDOFF_INTERNAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ai_active|handoff_requested|waiting_human|human_active"), "handoff_state_leak"),
    (re.compile(r"HANDOFF-[A-Z0-9]{8}"), "handoff_ticket_id"),
    (re.compile(r"HandoffState\.\w+"), "handoff_enum_leak"),
    (re.compile(r"handoff_state[\"']?\s*[:=]"), "handoff_internal_field"),
    (re.compile(r"trigger_type[\"']?\s*[:=]"), "handoff_internal_field"),
]

_COMPLAINT_INTERNAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"COMPLAINT-[A-Z0-9]{8}"), "complaint_ticket_id"),
    (re.compile(r"severity[\"']?\s*[:=]\s*(low|medium|high)"), "complaint_severity_leak"),
    (re.compile(r"matched_patterns[\"']?\s*[:=]"), "complaint_pattern_leak"),
    (re.compile(r"投诉工单.*?内部"), "complaint_internal_ref"),
]


class CSOutputGuard:

    def check(self, response: str, cs_context: dict | None = None) -> OutputGuardResult:
        """对客服回复文本执行五层过滤。"""
        if not response:
            return OutputGuardResult(text=response)

        reasons: list[str] = []
        text = response

        text, internal_hits = self._remove_internal_info(text)
        reasons.extend(internal_hits)

        text, promise_hits = self._remove_uncommitted_promises(text)
        reasons.extend(promise_hits)

        if cs_context:
            text, user_hits = self._mask_other_user_info(text, cs_context)
            reasons.extend(user_hits)

        text, handoff_hits = self._remove_handoff_internal_info(text)
        reasons.extend(handoff_hits)

        text, complaint_hits = self._remove_complaint_internal_info(text)
        reasons.extend(complaint_hits)

        filtered = len(reasons) > 0
        if filtered:
            logger.info(
                f"[OutputGuard] 过滤命中: reasons={reasons}"
            )

        return OutputGuardResult(
            text=text,
            filtered=filtered,
            reasons=reasons,
        )

    @staticmethod
    def _remove_internal_info(text: str) -> tuple[str, list[str]]:
        reasons: list[str] = []
        for pattern, label in _INTERNAL_PATTERNS:
            if pattern.search(text):
                reasons.append(label)
                text = pattern.sub("[已过滤]", text)
        return text, reasons

    @staticmethod
    def _remove_uncommitted_promises(text: str) -> tuple[str, list[str]]:
        reasons: list[str] = []
        for pattern, label in _PROMISE_PATTERNS:
            if pattern.search(text):
                reasons.append(label)
                text = pattern.sub("[已过滤]", text)
        return text, reasons

    @staticmethod
    def _mask_other_user_info(text: str, cs_context: dict) -> tuple[str, list[str]]:
        reasons: list[str] = []
        current_user_id = cs_context.get("authenticated_user_id", "")
        if not current_user_id:
            return text, reasons

        known_ids = cs_context.get("known_other_user_ids", [])
        for other_id in known_ids:
            if str(other_id) != str(current_user_id) and str(other_id) in text:
                text = text.replace(str(other_id), "[其他用户信息]")
                reasons.append("other_user_info")

        return text, reasons

    @staticmethod
    def _remove_handoff_internal_info(text: str) -> tuple[str, list[str]]:
        reasons: list[str] = []
        for pattern, label in _HANDOFF_INTERNAL_PATTERNS:
            if pattern.search(text):
                reasons.append(label)
                text = pattern.sub("[已过滤]", text)
        return text, reasons

    @staticmethod
    def _remove_complaint_internal_info(text: str) -> tuple[str, list[str]]:
        reasons: list[str] = []
        for pattern, label in _COMPLAINT_INTERNAL_PATTERNS:
            if pattern.search(text):
                reasons.append(label)
                text = pattern.sub("[已过滤]", text)
        return text, reasons


_guard_instance: CSOutputGuard | None = None
_guard_lock = threading.Lock()


def get_output_guard() -> CSOutputGuard:
    global _guard_instance
    if _guard_instance is None:
        with _guard_lock:
            if _guard_instance is None:
                _guard_instance = CSOutputGuard()
    return _guard_instance

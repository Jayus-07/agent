"""customer_service/security/input_guard.py — 客服输入安全检查

客服专用输入防护层，在 CS router 前运行。
平台级 InputGuard 处理通用 injection/scope，本模块专注 CS 特有检查：
  - 越权意图 (查询他人数据)
  - 敏感信息输入 (密码/银行卡)
  - 系统探测 (prompt 探测)

设计参考: docs/customer-service/design.md §15.1
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import Enum

from backend.shared.logger import logger


class GuardAction(str, Enum):
    ALLOW = "allow"
    CLARIFY = "clarify"
    BLOCK = "block"


class GuardCategory(str, Enum):
    FORMAT = "format"
    INJECTION = "injection"
    SQL_INJECTION = "sql_injection"
    SCOPE = "scope"
    SENSITIVE = "sensitive"
    SYSTEM_PROBE = "system_probe"


@dataclass
class CheckResult:
    action: GuardAction
    category: GuardCategory
    reason: str = ""
    message: str = ""


@dataclass
class CSInputGuardResult:
    action: GuardAction = GuardAction.ALLOW
    category: GuardCategory | None = None
    reason: str = ""
    message: str = "您好，请问有什么可以帮您？"

    @classmethod
    def aggregate(cls, checks: list[CheckResult]) -> CSInputGuardResult:
        """聚合多个检查结果：BLOCK > CLARIFY > ALLOW"""
        block = next((c for c in checks if c.action == GuardAction.BLOCK), None)
        if block:
            return cls(
                action=GuardAction.BLOCK,
                category=block.category,
                reason=block.reason,
                message=block.message or "抱歉，您的请求无法处理。",
            )

        clarify = next((c for c in checks if c.action == GuardAction.CLARIFY), None)
        if clarify:
            return cls(
                action=GuardAction.CLARIFY,
                category=clarify.category,
                reason=clarify.reason,
                message=clarify.message or "请问您具体想咨询什么问题？",
            )

        return cls(action=GuardAction.ALLOW)


# ── 检测模式 ──────────────────────────────────────────────

_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"忽略(之前|上面|以上)(的|地)?(所有)?(指令|规则|设定)"), "ignore_instruction"),
    (re.compile(r"(你现在|请你?|请)(是|作为|扮演)(一个|一名)?"), "role_override"),
    (re.compile(r"(系统|system)\s*(prompt|指令|消息)"), "system_prompt_probe"),
    (re.compile(r"(DAN|do\s+anything\s+now)", re.IGNORECASE), "dan_mode"),
]

_SQL_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"'\s*;\s*(DROP|DELETE|UPDATE|INSERT)\s+", re.IGNORECASE), "sql_command"),
    (re.compile(r"UNION\s+(ALL\s+)?SELECT", re.IGNORECASE), "union_select"),
    (re.compile(r"\b(OR|AND)\s+\d+\s*=\s*\d+"), "tautology"),
    (re.compile(r"--\s*$", re.MULTILINE), "sql_comment"),
]

_SCOPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(查|看|查?看)(一下)?(别人|其他|所有)(用户|人)(的)?.{0,4}(订单|信息|数据)"), "query_other_user"),
    (re.compile(r"(帮我|给我)(修改|改|删除|删)(别人|其他)(的)"), "modify_other_user"),
]

_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{16,19}\b"), "bank_card"),  # 银行卡号
    (re.compile(r"\b\d{17}[\dXx]\b"), "id_card"),  # 身份证号
    (re.compile(r"密码(是|为|:)\s*\S+"), "password_input"),
]

_SYSTEM_PROBE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(你的|你是)(什么|哪个)(模型|语言模型|AI)"), "model_probe"),
    (re.compile(r"(系统|system)(版本|配置|参数)"), "system_config_probe"),
]


class CSInputGuard:
    """客服输入安全检查"""

    def check(self, query: str, cs_context: dict | None = None) -> CSInputGuardResult:
        """对客服输入执行安全检查。"""
        if not query or not query.strip():
            return CSInputGuardResult(
                action=GuardAction.CLARIFY,
                category=GuardCategory.FORMAT,
                reason="empty_input",
                message="请输入您想咨询的问题。",
            )

        if len(query) > 2000:
            return CSInputGuardResult(
                action=GuardAction.BLOCK,
                category=GuardCategory.FORMAT,
                reason="too_long",
                message="输入内容过长，请简化后重试。",
            )

        checks = [
            self._check_injection(query),
            self._check_sql_injection(query),
            self._check_scope(query),
            self._check_sensitive(query),
            self._check_system_probe(query),
        ]
        return CSInputGuardResult.aggregate(checks)

    def _check_injection(self, query: str) -> CheckResult:
        """检测 Prompt Injection"""
        for pattern, label in _INJECTION_PATTERNS:
            if pattern.search(query):
                logger.info(f"[CSInputGuard] injection detected: {label}")
                return CheckResult(
                    action=GuardAction.BLOCK,
                    category=GuardCategory.INJECTION,
                    reason=label,
                    message="抱歉，无法处理您的请求。",
                )
        return CheckResult(action=GuardAction.ALLOW, category=GuardCategory.INJECTION)

    def _check_sql_injection(self, query: str) -> CheckResult:
        """检测 SQL Injection"""
        for pattern, label in _SQL_INJECTION_PATTERNS:
            if pattern.search(query):
                logger.info(f"[CSInputGuard] sql_injection detected: {label}")
                return CheckResult(
                    action=GuardAction.BLOCK,
                    category=GuardCategory.SQL_INJECTION,
                    reason=label,
                    message="输入格式有误，请检查后重试。",
                )
        return CheckResult(action=GuardAction.ALLOW, category=GuardCategory.SQL_INJECTION)

    def _check_scope(self, query: str) -> CheckResult:
        """检测越权意图 — 试图操作其他用户的数据"""
        for pattern, label in _SCOPE_PATTERNS:
            if pattern.search(query):
                logger.info(f"[CSInputGuard] scope violation detected: {label}")
                return CheckResult(
                    action=GuardAction.BLOCK,
                    category=GuardCategory.SCOPE,
                    reason=label,
                    message="您只能查询和操作自己的数据。",
                )
        return CheckResult(action=GuardAction.ALLOW, category=GuardCategory.SCOPE)

    def _check_sensitive(self, query: str) -> CheckResult:
        """检测敏感信息输入"""
        for pattern, label in _SENSITIVE_PATTERNS:
            if pattern.search(query):
                logger.info(f"[CSInputGuard] sensitive info detected: {label}")
                return CheckResult(
                    action=GuardAction.BLOCK,
                    category=GuardCategory.SENSITIVE,
                    reason=label,
                    message="为保护您的安全，请勿在对话中输入银行卡号、密码等敏感信息。",
                )
        return CheckResult(action=GuardAction.ALLOW, category=GuardCategory.SENSITIVE)

    def _check_system_probe(self, query: str) -> CheckResult:
        """检测系统探测"""
        for pattern, label in _SYSTEM_PROBE_PATTERNS:
            if pattern.search(query):
                logger.info(f"[CSInputGuard] system probe detected: {label}")
                return CheckResult(
                    action=GuardAction.CLARIFY,
                    category=GuardCategory.SYSTEM_PROBE,
                    reason=label,
                    message="我是客服助手，可以帮您查询订单、处理售后等问题。请问有什么可以帮您？",
                )
        return CheckResult(action=GuardAction.ALLOW, category=GuardCategory.SYSTEM_PROBE)


_guard_instance: CSInputGuard | None = None
_guard_lock = threading.Lock()


def get_cs_input_guard() -> CSInputGuard:
    """获取 CS Input Guard 单例"""
    global _guard_instance
    if _guard_instance is None:
        with _guard_lock:
            if _guard_instance is None:
                _guard_instance = CSInputGuard()
    return _guard_instance

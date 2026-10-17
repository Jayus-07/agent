"""证据/摘要脱敏 — 质量记录与 Trace 只保留可审计的掩码形态（2026-10-18）。

质量记录、Trace 摘要与异常消息会保存「删掉了什么」「哪段没覆盖」的样本。
样本来自正文，可能包含手机号、邮箱、Token、口令——证据只需要可定位、可
对账，不需要也不允许保留明文。掩码口径与索引期 PII 过滤一致（手机号/身份
证/银行卡），另外补充邮箱与常见凭据形态。

本模块只依赖标准库：Cleaner、CoverageAudit、QualityGate 都可以直接引用，
不引入 preprocessing 内部的循环依赖。
"""
from __future__ import annotations

import re

# 与索引期 PII 过滤同一组规则与掩码形态（前 3 后 4 等），保证"审计看到的
# 文本"和"入库看到的文本"口径一致。
PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)'), 'phone'),
    (re.compile(r'(?<!\d)(\d{17}[\dXx])(?!\d)'), 'id_card'),
    (re.compile(r'(?<!\d)(\d{16,19})(?!\d)'), 'bank_card'),
]

_EMAIL_RE = re.compile(
    r'([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})'
)
# 键值型凭据：中英文键名 + 纯 ASCII 值（≥6 字符）。值限定 ASCII，避免把
# 「密钥管理规范」这类中文正文整段掩掉。
_CREDENTIAL_KV_RE = re.compile(
    r'(?i)(\b(?:bearer|token|api[_-]?key|access[_-]?key|private[_-]?key|'
    r'client[_-]?secret|secret|password|passwd|pwd|authorization)\b'
    r'|口令|密码|密钥|令牌|凭据)'
    r'(\s*[:=：]?\s*)'
    r'([A-Za-z0-9!@#$%^&*._\-+=/]{6,})'
)
_PREFIXED_SECRET_RE = re.compile(
    r'\b(?:sk|pk|rk|ghp|gho|ghs|xox[abp]|AKIA|ASIA)[-_A-Za-z0-9]{6,}\b'
)
_JWT_RE = re.compile(
    r'\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{4,}\b'
)


def mask_pii_text(text: str) -> tuple[str, list[str]]:
    """PII 脱敏（手机号/身份证/银行卡）。返回 (脱敏后文本, 命中类型列表)。"""
    masked_types: list[str] = []
    value = str(text or "")
    for pattern, pii_type in PII_PATTERNS:
        if not pattern.search(value):
            continue
        if pii_type == 'phone':
            value = pattern.sub(
                lambda m: m.group(1)[:3] + '****' + m.group(1)[-4:], value,
            )
        elif pii_type == 'id_card':
            value = pattern.sub(
                lambda m: m.group(1)[:6] + '********' + m.group(1)[-4:], value,
            )
        elif pii_type == 'bank_card':
            value = pattern.sub(
                lambda m: m.group(1)[:4] + '********' + m.group(1)[-4:], value,
            )
        masked_types.append(pii_type)
    return value, masked_types


def mask_sensitive_text(text: str, limit: int | None = None) -> str:
    """证据用脱敏：PII + 邮箱 + 凭据形态，保留可对账的掩码结构。

    Args:
        text: 原始样本
        limit: 截断长度（None 表示不截断）；截断在脱敏之后，避免把掩码切坏。
    """
    value = str(text or "")
    if not value:
        return ""
    value, _ = mask_pii_text(value)
    value = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value)
    value = _CREDENTIAL_KV_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", value,
    )
    value = _PREFIXED_SECRET_RE.sub("[REDACTED]", value)
    value = _JWT_RE.sub("[REDACTED]", value)
    if limit is not None and limit >= 0 and len(value) > limit:
        value = value[:limit]
    return value

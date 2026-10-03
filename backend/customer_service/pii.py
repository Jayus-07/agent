"""customer_service/pii.py — PII 脱敏进 LLM（2026-10-03，演进分析 C11）。

个保法最小化原则：用户消息在进入 LLM prompt 前做占位符替换，回复回来后
按 vault 还原。设计约束：

  - 只做**规则可确定性检测**的 PII 类：手机号/座机/身份证/邮箱/详细地址/
    自报姓名（我叫XX/收件人XX）。不可靠的猜测（无标记中文人名）宁漏勿错
    ——误掩码会破坏回复语义，比漏掩更伤对话；
  - 同一原文映射同一占位符（会话内一致，LLM 可理解上下文指代）；
  - 还原只认本 vault 签发的占位符，不碰用户自己的文本；
  - 覆盖口径见 tests/customer_service/test_pii.py 的 20 条 payload 断言
    （验收 C11：脱敏率 100% / 还原正确率 100%）。

接线点（当前）：experts/knowledge.py（RAG 问答主路径）；
supervisor LLM 兜底/query/action/complaint 专家待其文件所在线落库后跟进。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 占位符模板（中文，LLM 可读；{i} 会话内递增）
_PLACEHOLDER = "[隐私信息{i}]"

# 检测规则：顺序即优先级（身份证先于手机号，防止 18 位被截走前 11 位）
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("landline", re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)")),
    ("email", re.compile(r"(?<![\w.])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # 详细地址：≥2 级行政区划 + （街道词段 或 号/栋/室/院收尾）——保守阈值防误伤
    (
        "address",
        re.compile(
            r"[\u4e00-\u9fa5]{2,12}?(?:省|自治区|市)[\u4e00-\u9fa5]{2,12}?"
            r"(?:市|区|县|旗)"
            r"(?:[\u4e00-\u9fa5]{0,12}?(?:路|街|巷|道|村|镇|街道)"
            r"[\u4e00-\u9fa5A-Za-z0-9]{0,20}(?:\d+号)?)?"
            r"[\u4e00-\u9fa5A-Za-z0-9]{0,16}"
            r"(?:\d+号|栋|幢|号楼|单元|室|院)"
        ),
    ),
)

# 自报姓名（我叫XX/我是XX/收件人XX）：捕获后按 name 类掩码。
# 「我是」带否定前瞻——「我是不是可以退货」不可掩成"叫是不"。
_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:我叫|我叫作|名字叫|姓名叫|本人(?:姓|叫)|联系人[:：]?)\s*([\u4e00-\u9fa5]{2,4})"),
    re.compile(r"我是(?!(?:不是|是否|不)|在想|在用|在学)([\u4e00-\u9fa5]{2,4})"),
    re.compile(r"收件人[:：]?\s*([\u4e00-\u9fa5]{2,4})"),
)


@dataclass
class PiiVault:
    """一次掩码会话的占位符↔原文映射（还原只认本 vault）。"""

    mappings: dict[str, str] = field(default_factory=dict)  # placeholder -> original
    _reverse: dict[str, str] = field(default_factory=dict)  # original -> placeholder

    def placeholder_for(self, original: str, kind: str) -> str:
        if original in self._reverse:
            return self._reverse[original]
        placeholder = _PLACEHOLDER.format(i=len(self.mappings) + 1)
        self.mappings[placeholder] = original
        self._reverse[original] = placeholder
        return placeholder

    def __len__(self) -> int:
        return len(self.mappings)


def mask_pii(text: str) -> tuple[str, PiiVault]:
    """掩码文本中的确定性 PII，返回 (掩码后文本, vault)。

    无 PII 时原样返回、vault 为空（零开销路径）。
    """
    vault = PiiVault()
    if not text:
        return text, vault

    def _sub(match: re.Match[str]) -> str:
        return vault.placeholder_for(match.group(0), "pii")

    masked = text
    for _kind, pattern in _PII_PATTERNS:
        masked = pattern.sub(_sub, masked)

    # 自报姓名：只掩捕获组（保留引导词，"我叫[隐私信息1]"语义仍通）
    for pattern in _NAME_PATTERNS:
        masked = pattern.sub(lambda m: m.group(0).replace(m.group(1), vault.placeholder_for(m.group(1), "name")), masked)

    return masked, vault


def unmask_text(text: str, vault: PiiVault) -> str:
    """按 vault 还原占位符；非本 vault 签发的占位符原样保留。"""
    if not vault or not vault.mappings or not text:
        return text
    restored = text
    for placeholder, original in vault.mappings.items():
        restored = restored.replace(placeholder, original)
    return restored


def contains_placeholder(text: str, vault: PiiVault) -> bool:
    """断言辅助：payload 中是否仍有本 vault 未还原的占位符。"""
    return bool(vault and any(p in text for p in vault.mappings))

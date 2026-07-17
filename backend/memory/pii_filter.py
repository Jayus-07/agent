"""
PII 过滤器 — 正则匹配 + 脱敏，防止敏感信息进入记忆库。

规则来源:
  - GB/T 35273-2020 个人信息安全规范
  - 中国网络安全法 个人信息定义

设计原则:
  - 纯正则，不依赖 LLM，确定性执行
  - 检测到 PII 时返回脱敏版本而非直接丢弃（保留语义骨架）
  - 可配置开关，默认开启
"""

import re
from dataclasses import dataclass, field

from backend.config import L3_PII_FILTER_ENABLED
from backend.shared.logger import logger


# =====================================================
# PII 检测规则
# =====================================================

_PII_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # (名称, 正则, 替换模板)

    # 身份证号 (18位) — 用 (?<!\d)/(?!\d) 替代 \b，兼容中文上下文
    ("身份证号",
     re.compile(r'(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)'),
     "[身份证号]"),

    # 手机号 (中国大陆)
    ("手机号",
     re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
     "[手机号]"),

    # 银行卡号 (16-19位)
    ("银行卡号",
     re.compile(r'(?<!\d)\d{16,19}(?!\d)'),
     "[银行卡号]"),

    # 电子邮箱 (ASCII only, \b 仍可用)
    ("邮箱",
     re.compile(r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}(?![A-Za-z0-9.-])'),
     "[邮箱]"),

    # IP 地址
    ("IP地址",
     re.compile(r'(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)'),
     "[IP地址]"),

    # 车牌号 (中国大陆) — 首字为中文省份简称
    ("车牌号",
     re.compile(r'(?<![A-Za-z0-9])[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼][A-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳](?![A-Za-z0-9])'),
     "[车牌号]"),

    # 统一社会信用代码 (18位)
    ("统一社会信用代码",
     re.compile(r'(?<![A-Z0-9])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![A-Z0-9])'),
     "[统一社会信用代码]"),
]


@dataclass
class PiiScanResult:
    """PII 扫描结果"""
    original: str
    sanitized: str
    detections: list[dict] = field(default_factory=list)
    has_pii: bool = False


def scan_and_sanitize(text: str) -> PiiScanResult:
    """
    扫描文本中的 PII 并替换为类型标签。

    保留语义骨架: "张三的身份证号是110101199001011234" → "张三的身份证号是[身份证号]"
    这样记忆库仍知道"张三有身份证号"，但不存具体号码。
    """
    if not L3_PII_FILTER_ENABLED:
        return PiiScanResult(original=text, sanitized=text, has_pii=False)

    result = PiiScanResult(original=text, sanitized=text)
    sanitized = text

    for name, pattern, replacement in _PII_PATTERNS:
        matches = pattern.findall(sanitized)
        if matches:
            # 对银行卡号做二次校验：排除明显不是银行卡的数字串（如纯0、年份）
            if name == "银行卡号":
                valid_matches = []
                for m in matches:
                    # 排除全0、年份、电话号码
                    if m == "0" * len(m) or m.startswith(("19", "20")):
                        continue
                    if len(m) == 18 and (m.startswith("1") and m[7:11] in ("1990",)):
                        continue
                    valid_matches.append(m)
                if not valid_matches:
                    continue
                matches = valid_matches

            for _ in matches:
                result.detections.append({"type": name, "replaced_with": replacement})
            sanitized = pattern.sub(replacement, sanitized)

    result.sanitized = sanitized
    result.has_pii = len(result.detections) > 0

    if result.has_pii:
        logger.info(
            f"[PII] 检测到 {len(result.detections)} 处敏感信息: "
            f"{[d['type'] for d in result.detections]}"
        )

    return result



"""
llm_polisher.py — LLM 润色 + 事实锁定校验

核心安全机制：提取 → 锁定 → 校验
  1. 润色前提取所有数值 token（数字、百分比、日期、金额）
  2. LLM 润色（system prompt 严格约束只改语言不改事实）
  3. 润色后再次提取数值，逐条比对
  4. 不匹配 → 告警 → 返回原始初稿（安全回退）

完全不依赖 LLM 的"自觉"，硬校验兜底。
"""

import re
from typing import List, Set, Tuple

from backend.llm.llm_factory import llm
from backend.shared.logger import logger

# =====================================================
# LLM Prompt
# =====================================================

POLISH_SYSTEM = """你是专业的报告润色助手。你的任务是改进报告的语言表达，使其更流畅、更专业、更易读。

## 严格规则（违反将被自动拒绝）

1. **禁止修改任何数字**：包括但不限于金额、百分比、数量、ID、日期（年月日）、统计数据
2. **禁止增删数据行**：表格中的每一行数据必须原样保留
3. **禁止修改事实陈述**：不添加任何新的数据、结论或统计信息
4. **禁止修改 Markdown 结构**：标题层级（##/###）、表格列数、列表结构保持原样
5. **允许的改动**：
   - 优化句式流畅度（如"的"字句拆分、长句断句）
   - 改进措辞专业度
   - 调整段落之间的衔接语
   - 修正错别字和语法错误
   - 为表格添加对齐格式

## 输出

直接返回润色后的完整 Markdown，不要加任何前缀解释或后缀说明。"""


# =====================================================
# 数值提取
# =====================================================

# 匹配所有可能的"事实 token"
_NUMBER_PATTERNS = [
    # 浮点数/整数（包括逗号分隔的三位分节） 1,234.56
    re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?%?"),
    # 中文日期 2026年05月24日
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"),
    # ISO 日期 2026-05-24
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    # 金额符号 ¥100 / $200
    re.compile(r"[¥￥$]\s*\d+(?:,\d{3})*(?:\.\d+)?"),
    # 百分比 85.5%
    re.compile(r"\d+\.?\d*\s*%"),
    # 时间 HH:MM:SS
    re.compile(r"\d{2}:\d{2}(:\d{2})?"),
]

# 排除模式（非事实性数字：标题编号、列表序号）
_EXCLUDE_PATTERNS = [
    re.compile(r"^#{1,6}\s+\d+"),       # Markdown 标题中的数字
    re.compile(r"^\s*\d+[\.\)]\s"),      # 有序列表编号 "1. ", "1) "
]

# 用于在数值提取前剥离 base64 图片数据（图片中的数字不是"事实"）
_BASE64_IMAGE = re.compile(r"!\[.*?\]\(data:image/\w+;base64,[A-Za-z0-9+/=]+\)")


def _strip_base64_images(text: str) -> str:
    """移除 Markdown 中的 base64 图片嵌入，避免污染数值提取"""
    return _BASE64_IMAGE.sub("[IMAGE]", text)


def _extract_numerical_tokens(text: str) -> Set[str]:
    """
    从文本中提取所有数值 token。
    用于润色前后比对——这些 token 在润色后必须全部存在。

    注意：先剥离 base64 图片数据，避免图片编码中的数字污染提取结果。

    返回:
        token 集合（规范化后的字符串）
    """
    # 剥离 base64 图片数据
    clean_text = _strip_base64_images(text)

    tokens: Set[str] = set()

    for pattern in _NUMBER_PATTERNS:
        for match in pattern.finditer(clean_text):
            token = match.group()
            normalized = token.strip().replace(" ", "").replace("　", "")
            tokens.add(normalized)

    return tokens


def _extract_key_facts(text: str) -> Set[str]:
    """
    提取文本中的关键"事实行"——表格数据行和统计陈述。
    用于粗粒度校验：这些行在润色后必须能匹配到。
    """
    clean_text = _strip_base64_images(text)
    facts: Set[str] = set()

    # Markdown 表格中的数据行（非表头非分隔行）
    for line in clean_text.split("\n"):
        stripped = line.strip()
        # 表格数据行：以 | 开头，包含数字
        if stripped.startswith("|") and re.search(r"\d", stripped):
            # 提取数字部分作为指纹
            nums = re.findall(r"\d+(?:\.\d+)?", stripped)
            if nums:
                facts.add("|".join(nums))

    return facts


# =====================================================
# 校验
# =====================================================

def _verify_numbers(original: Set[str], polished: Set[str]) -> Tuple[bool, Set[str]]:
    """
    校验润色后的数值是否覆盖了原始数值。

    返回:
        (是否通过, 丢失的 token 集合)
    """
    missing = set()
    for token in original:
        if token not in polished:
            # 对于包含逗号分隔的数字（如 1,234），也尝试无逗号形式
            if "," in token:
                alt = token.replace(",", "")
                if alt in polished:
                    continue
            missing.add(token)

    return len(missing) == 0, missing


def _verify_facts(original: Set[str], polished: Set[str]) -> Tuple[bool, int]:
    """
    校验关键事实行是否保留。
    容忍率：允许 ≤10% 的事实行无法匹配（LLM 可能合并了行）。
    """
    if not original:
        return True, 0

    missing_count = 0
    for fact in original:
        if fact not in polished:
            missing_count += 1

    loss_rate = missing_count / len(original) if original else 0
    return loss_rate <= 0.1, missing_count


# =====================================================
# 主润色器
# =====================================================

class LLMPolisher:
    """LLM 润色器：润色 + 校验 + 安全回退"""

    def __init__(self, max_retries: int = 2):
        """
        参数:
            max_retries: 校验失败后的重试次数
        """
        self.max_retries = max_retries

    def polish(self, draft: str) -> str:
        """
        对初稿进行语言润色。

        参数:
            draft: Jinja2 模板渲染后的 Markdown 初稿

        返回:
            润色后的 Markdown（校验通过）或原始初稿（校验失败）
        """
        # ① 提取阶段：记录原始数值和事实
        original_numbers = _extract_numerical_tokens(draft)
        original_facts = _extract_key_facts(draft)

        logger.info(f"[LLMPolisher] 润色前提取: {len(original_numbers)} 个数值 token, "
                    f"{len(original_facts)} 个事实行指纹")

        for attempt in range(self.max_retries + 1):
            try:
                # ② LLM 润色
                polished = self._call_llm(draft, attempt)

                # 空响应保护
                if not polished or len(polished.strip()) < 50:
                    logger.warning(f"[LLMPolisher] LLM 返回内容过短 ({len(polished)} 字符)，重试")
                    continue

                # ③ 校验阶段：比对数值
                polished_numbers = _extract_numerical_tokens(polished)
                nums_ok, missing_nums = _verify_numbers(original_numbers, polished_numbers)

                # ④ 校验阶段：比对事实行
                polished_facts = _extract_key_facts(polished)
                facts_ok, missing_fact_count = _verify_facts(original_facts, polished_facts)

                if nums_ok and facts_ok:
                    logger.info(f"[LLMPolisher] 润色校验通过 "
                                f"(数值={len(original_numbers)}→{len(polished_numbers)}, "
                                f"事实行={len(original_facts)}→{len(polished_facts)})")
                    return polished

                # 校验失败，记录详情
                if not nums_ok:
                    logger.warning(
                        f"[LLMPolisher] 数值校验失败 (第{attempt+1}次): "
                        f"丢失 {len(missing_nums)} 个 token: "
                        f"{list(missing_nums)[:5]}..."
                    )
                if not facts_ok:
                    logger.warning(
                        f"[LLMPolisher] 事实行校验失败 (第{attempt+1}次): "
                        f"丢失 {missing_fact_count} 行"
                    )

                # 重试时在 prompt 中强调
                if attempt < self.max_retries:
                    continue

            except Exception as e:
                logger.error(f"[LLMPolisher] LLM 调用异常 (第{attempt+1}次): {e}")
                if attempt >= self.max_retries:
                    logger.warning("[LLMPolisher] 润色失败，返回原始初稿")
                    return draft

        # 所有重试耗尽，安全回退
        logger.warning("[LLMPolisher] 所有重试耗尽，返回原始初稿")
        return draft

    def _call_llm(self, draft: str, attempt: int) -> str:
        """
        调用 LLM 进行润色。
        重试时会在用户消息中追加额外约束。
        """
        user_msg = (
            "请润色以下报告，只改进表达不改变数据：\n\n" + draft
        )

        if attempt > 0:
            user_msg += (
                f"\n\n【重要提醒 — 第{attempt+1}次尝试】"
                f"上次输出因为修改了数字或删除了数据行而被拒绝。"
                f"请这次严格保持所有数字、百分比、日期原样不变。"
            )

        messages = [
            ("system", POLISH_SYSTEM),
            ("human", user_msg),
        ]

        resp = llm.invoke(messages)
        return resp.content.strip()


# 全局单例
llm_polisher = LLMPolisher()

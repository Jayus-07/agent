"""脏数据过滤器 — 质量过滤 + SimHash 去重 + PII 脱敏"""
import hashlib
import re

from backend import config
from backend.rag.preprocessing.chunking import is_table_chunk
from backend.rag.preprocessing.redaction import mask_pii_text
from backend.shared.logger import logger


class DuplicateDetector:
    """基于 SimHash 的近似去重器。

    算法：
    1. 对文本做字符级 n-gram (n=3) 分词
    2. 对每个 n-gram 计算 hash 权重
    3. 合并得到 64-bit SimHash
    4. 与已存 hash 比较汉明距离
    5. 距离 <= threshold -> 判为重复
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.seen_hashes: list[int] = []

    def is_duplicate(self, text: str) -> bool:
        """检查文本是否与已见过的文本重复。首次调用返回 False。"""
        if not text or len(text) < 20:
            return False  # 太短不检测

        h = self._simhash(text)

        for seen in self.seen_hashes:
            if self._hamming_distance(h, seen) <= self.threshold:
                return True

        self.seen_hashes.append(h)
        # 限制内存：保留最近 10000 个 hash
        if len(self.seen_hashes) > 10000:
            self.seen_hashes = self.seen_hashes[-5000:]
        return False

    def reset(self):
        """清空已记录的 hash"""
        self.seen_hashes.clear()

    def _simhash(self, text: str) -> int:
        """计算 64-bit SimHash（使用 MD5 确定性哈希）"""
        # 字符级 3-gram
        grams = [text[i:i+3] for i in range(max(len(text) - 2, 0))]
        if not grams:
            return 0

        v = [0] * 64
        for g in grams:
            h = self._hash_gram(g)
            for i in range(64):
                if h & (1 << i):
                    v[i] += 1
                else:
                    v[i] -= 1

        result = 0
        for i in range(64):
            if v[i] > 0:
                result |= (1 << i)
        return result

    @staticmethod
    def _hash_gram(gram: str) -> int:
        """对单个 n-gram 计算确定性 64-bit 哈希"""
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "little")

    @staticmethod
    def _hamming_distance(a: int, b: int) -> int:
        """计算两个整数的汉明距离"""
        return (a ^ b).bit_count()


# ── PII 脱敏 ──────────────────────────────────────
# 规则与掩码形态统一在 redaction.mask_pii_text（证据脱敏复用同一口径）；
# 这里保留 _mask_pii 名称以兼容既有调用方（indexer / 覆盖对账）。

# 财务专用 PII 脱敏正则（从 domain_data 导入，兑底本地定义）
try:
    from backend.rag.preprocessing.domain_data import FINANCIAL_PII_PATTERNS
except ImportError:
    FINANCIAL_PII_PATTERNS = []


def _mask_pii(text: str) -> tuple[str, list[str]]:
    """PII 脱敏。返回 (脱敏后文本, 操作列表)。规则见 redaction.mask_pii_text。"""
    return mask_pii_text(text)


def _mask_financial_pii(text: str) -> tuple[str, list[str]]:
    """财务专用 PII 脱敏。

    脱敏财务报表中常见的敏感信息：
      - 银行卡号（16-19位数字）→ 前4后4
      - 统一社会信用代码（18位字母数字）→ 前6后4
      - 身份证号（18位）→ 前6后4
      - 纳税人识别号（15-20位）→ 前6后4
    """
    masked_types = []
    for pattern, pii_type in FINANCIAL_PII_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            if pii_type == 'bank_card':
                text = pattern.sub(lambda m: m.group(1)[:4] + '********' + m.group(1)[-4:], text)
            elif pii_type in ('tax_id', 'tax_number', 'id_card'):
                # 统一社会信用代码/纳税号：前6后4
                text = pattern.sub(lambda m: m.group(1)[:6] + '********' + m.group(1)[-4:], text)
            masked_types.append(pii_type)
    return text, masked_types


# ── 有效内容识别（P0-C，2026-10-18）──────────────────
# 移植自 PR #66（codex/rag-upload-b1-20261014）的 filter.py：英文正文/技术
# 标识符不应被中文占比规则误杀，表格行不套用正文阈值与判重。
# 本任务只取最小必要部分（不含 #66 依赖新 chunk metadata 的
# split_group_id 分支），不搬 #66 的 chunking/coverage 改造。
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_LATIN_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*(?:[-_/][A-Z0-9]+)+(?![A-Za-z0-9])"
)
# CJK 词（≥2 个连续汉字）、拉丁词、字母数字混排或 ≥4 位数字（编号/SKU/状态值）
_CJK_WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_MIXED_IDENTIFIER_RE = re.compile(r"[A-Za-z]+\d|\d+[A-Za-z]")
_LONG_DIGITS_RE = re.compile(r"\d{4,}")


def _has_meaningful_latin_text(text: str) -> bool:
    """识别英文正文和代码标识，避免中文比例规则误杀有效内容（源自 #66）。"""
    identifiers = _LATIN_IDENTIFIER_RE.findall(text)
    if any(
        len(identifier) >= 6
        or (len(identifier) >= 4 and any(char.isdigit() for char in identifier))
        for identifier in identifiers
    ):
        return True

    words = _LATIN_WORD_RE.findall(text)
    latin_letters = sum(len(word) for word in words)
    all_letters = sum(1 for char in text if char.isalpha())
    if not _CJK_CHAR_RE.search(text):
        # 纯英文/技术字段：没有中文本身不是缺陷（中文占比规则用于识别
        # 中英混杂的低信息碎片，不用于拒绝英文正文与配置/代码）。
        return len(words) >= 2 and latin_letters >= 6
    return (
        len(words) >= 3
        and latin_letters >= 18
        and latin_letters / max(all_letters, 1) >= 0.6
    )


def _short_text_carries_content(text: str) -> bool:
    """短文本是否承载可检索事实（短段落/SKU/订单号/状态值/英文技术字段）。

    短 ≠ 噪音：PDF 的「附录 A：常用单据」、状态值「已发货」、订单号
    「SO-2026-0001」都是唯一来源内容。长度阈值只能拦"无内容"碎片
    （裸页码、单符号、编号孤字），不能拦有效短内容。
    """
    if _CJK_WORD_RE.search(text):
        return True
    if _LATIN_WORD_RE.search(text):
        return True
    if _MIXED_IDENTIFIER_RE.search(text):
        return True
    return bool(_LONG_DIGITS_RE.search(text))


class ChunkFilter:
    """Chunk 质量过滤器。

    检查项（按顺序）：
    1. 空白内容
    2. 超短文本（< min_length；表格行与承载事实的 Leaf 短文本豁免）
    3. 纯符号比率过高
    4. 中文占比过低（表格行与英文/技术字段豁免）
    5. SimHash 去重（Parent / Leaf 已见集合隔离；表格行不判重）
    6. PII 脱敏（不拒绝，仅修改文本）
    """

    def __init__(self):
        self.min_length = config.FILTER_MIN_CHUNK_LENGTH
        self.max_symbol_ratio = config.FILTER_MAX_SYMBOL_RATIO
        self.min_chinese_ratio = config.FILTER_MIN_CHINESE_RATIO
        self.simhash_threshold = config.FILTER_SIMHASH_THRESHOLD
        self.enable_pii = config.FILTER_ENABLE_PII_MASK
        self.financial_pii_force = getattr(config, 'FINANCIAL_PII_MASK_FORCE', True)
        self._dup_detector = DuplicateDetector(threshold=self.simhash_threshold)
        # Parent 有意包含对应 Leaf 的正文，不能让 Parent 的 SimHash 把可检索
        # Leaf 判成重复（否则覆盖门禁必然缺口）。Parent 之间仍独立去重。
        # 移植自 PR #66 的同名隔离实现。
        self._parent_dup_detector = DuplicateDetector(
            threshold=self.simhash_threshold
        )

    def should_keep(self, text: str, metadata: dict) -> tuple[bool, str]:
        """检查 Chunk 是否应保留。

        Returns:
            (是否保留, 原因标签)
            - "clean": 通过所有检查
            - "empty" / "too_short" / "all_symbols" / "low_chinese_ratio" / "duplicate": 被拒绝
        """
        metadata.setdefault("filter_status", "clean")
        metadata.setdefault("filter_reason", "")

        # 1. 空白检查
        if not text or not text.strip():
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "empty"
            return False, "empty"

        # 2. 长度检查：表格行与"承载事实的短文本"不套用正文长度阈值。
        #    表格 Leaf 的短行是有效记录；PDF 短段落/状态值/编号同样是唯一
        #    来源内容，删掉就会在覆盖审计里变成真实缺口。
        stripped = text.strip()
        table_chunk = is_table_chunk(metadata.get("chunk_type"))
        # 短内容豁免只给 Leaf：Leaf 是唯一可检索来源内容，Parent 只是上下文
        # 容器，沿用原阈值 + 「被引用 Parent 保留」既有覆盖路径，避免把
        # Parent 的过滤/保留语义改掉。
        is_parent = metadata.get("granularity") == "parent"
        if (
            len(stripped) < self.min_length
            and not table_chunk
            and (is_parent or not _short_text_carries_content(stripped))
        ):
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "too_short"
            return False, "too_short"

        # 3. 纯符号比率（非字母/数字/中文 = 符号）
        # P1:把 Unicode replacement char (��) 和私用区码点视为字母,
        # 因为 PDF parser 解码失败时会用 �� 占位,这是正常的解码异常,
        # 不应该被当 symbol 拒绝整篇文档。
        def _is_meaningful_char(c: str) -> bool:
            if c.isalnum() or '一' <= c <= '鿿':
                return True
            if c == '\ufffd':  # replacement char
                return True
            return False

        symbol_count = sum(1 for c in stripped if not _is_meaningful_char(c))
        if len(stripped) > 0 and symbol_count / len(stripped) > self.max_symbol_ratio:
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "all_symbols"
            return False, "all_symbols"

        # 4. 中文占比（中文知识库场景）
        # P1:把 �� (replacement char) 也算中文 — PDF parser 解码失败时
        # 用 �� 占位,本应是中文字符。低 chinese_ratio 不应误杀。
        def _is_chinese_or_replacement(c: str) -> bool:
            return '一' <= c <= '鿿' or c == '\ufffd'

        chinese_count = sum(1 for c in stripped if _is_chinese_or_replacement(c))
        total_alpha = sum(1 for c in stripped if _is_meaningful_char(c))
        low_chinese_ratio = (
            total_alpha > 0
            and chinese_count / total_alpha < self.min_chinese_ratio
        )
        meaningful_english = (
            False if is_parent else _has_meaningful_latin_text(stripped)
        )
        if low_chinese_ratio and not (table_chunk or meaningful_english):
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "low_chinese_ratio"
            return False, "low_chinese_ratio"

        # 5. SimHash 去重：Parent 与 Leaf 使用彼此隔离的已见集合；
        #    表格行本身是源数据，重复行不构成冗余正文。
        duplicate_detector = (
            self._parent_dup_detector
            if metadata.get("granularity") == "parent"
            else self._dup_detector
        )
        if not table_chunk and duplicate_detector.is_duplicate(stripped):
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "duplicate"
            return False, "duplicate"

        # 6. PII 脱敏（不拒绝内容，仅修改文本）
        # 财务文档强制 PII 脱敏：覆写全局开关，始终对 financial doc_type 执行
        doc_type = metadata.get("doc_type", "")
        # 「是不是表格 chunk」的判定口径只有一份（chunking.is_table_chunk）——
        # 此前这里内联 startswith("table_")，与生产点的类型名各写一套
        # （结构病审查 P3-1：失配的后果是财务表格 PII 漏脱敏）。
        is_financial = (doc_type == "financial") or is_table_chunk(
            metadata.get("chunk_type")
        )
        should_mask = self.enable_pii or (is_financial and self.financial_pii_force)
        if should_mask:
            new_text, masked_types = _mask_pii(stripped)
            if is_financial and self.financial_pii_force:
                # 财务文档额外执行财务专用 PII 脱敏
                new_text, fin_masked = _mask_financial_pii(new_text)
                masked_types.extend(fin_masked)
            if masked_types:
                metadata["pii_masked"] = masked_types
                # 脱敏后文本写回 chunk（调用方需更新 page_content）
                metadata["_masked_text"] = new_text

        metadata["filter_status"] = "clean"
        metadata["filter_reason"] = ""
        return True, "clean"

    def reset(self):
        """重置去重检测器（知识库重建时调用）"""
        self._dup_detector.reset()
        self._parent_dup_detector.reset()

    @staticmethod
    def apply_pii_mask(text: str) -> str:
        """对文本应用 PII 脱敏，返回脱敏后的文本。"""
        masked_text, _ = _mask_pii(text)
        return masked_text

    @staticmethod
    def apply_financial_pii_mask(text: str) -> str:
        """对文本应用财务专用 PII 脱敏，返回脱敏后的文本。

        用于财务文档的强制脱敏，包含银行账号/税号/社会信用代码等。
        """
        masked_text, _ = _mask_pii(text)
        masked_text, _ = _mask_financial_pii(masked_text)
        return masked_text

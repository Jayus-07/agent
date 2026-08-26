"""脏数据过滤器 — 质量过滤 + SimHash 去重 + PII 脱敏"""
import hashlib
import re

from backend import config
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


# ── PII 脱敏正则 ──────────────────────────────────

_PII_PATTERNS = [
    (re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)'), 'phone'),
    (re.compile(r'(?<!\d)(\d{17}[\dXx])(?!\d)'), 'id_card'),
    (re.compile(r'(?<!\d)(\d{16,19})(?!\d)'), 'bank_card'),
]

# 财务专用 PII 脱敏正则（从 domain_data 导入，兑底本地定义）
try:
    from backend.rag.preprocessing.domain_data import FINANCIAL_PII_PATTERNS
except ImportError:
    FINANCIAL_PII_PATTERNS = []


def _mask_pii(text: str) -> tuple[str, list[str]]:
    """PII 脱敏。返回 (脱敏后文本, 操作列表)"""
    masked_types = []
    for pattern, pii_type in _PII_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            if pii_type == 'phone':
                text = pattern.sub(lambda m: m.group(1)[:3] + '****' + m.group(1)[-4:], text)
            elif pii_type == 'id_card':
                text = pattern.sub(lambda m: m.group(1)[:6] + '********' + m.group(1)[-4:], text)
            elif pii_type == 'bank_card':
                text = pattern.sub(lambda m: m.group(1)[:4] + '********' + m.group(1)[-4:], text)
            masked_types.append(pii_type)
    return text, masked_types


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


class ChunkFilter:
    """Chunk 质量过滤器。

    检查项（按顺序）：
    1. 空白内容
    2. 超短文本（< min_length）
    3. 纯符号比率过高
    4. 中文占比过低
    5. SimHash 去重
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

        # 2. 长度检查
        stripped = text.strip()
        if len(stripped) < self.min_length:
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
        if total_alpha > 0 and chinese_count / total_alpha < self.min_chinese_ratio:
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "low_chinese_ratio"
            return False, "low_chinese_ratio"

        # 5. SimHash 去重
        if self._dup_detector.is_duplicate(stripped):
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "duplicate"
            return False, "duplicate"

        # 6. PII 脱敏（不拒绝内容，仅修改文本）
        # 财务文档强制 PII 脱敏：覆写全局开关，始终对 financial doc_type 执行
        doc_type = metadata.get("doc_type", "")
        is_financial = (doc_type == "financial") or (
            metadata.get("chunk_type", "").startswith("table_")
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

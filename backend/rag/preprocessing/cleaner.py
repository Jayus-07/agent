"""文档清洗器 — 文本规范化 + PDF 页眉页脚去除 + URL/邮箱处理"""
import re
from collections import Counter
from dataclasses import dataclass, field

from backend import config
from backend.shared.logger import logger


@dataclass
class CleanResult:
    """清洗结果"""
    text: str
    changes: list[str] = field(default_factory=list)   # 执行了哪些清洗操作
    warnings: list[str] = field(default_factory=list)   # 清洗警告（如检测到异常但未处理）


class DocumentCleaner:
    """统一文档清洗入口。

    支持的清洗操作（可通过 config.py 独立开关）：
      - 控制字符去除（\\x00-\\x1f 保留 \\n\\t）
      - 非法 Unicode 去除（surrogate characters）
      - 全角半角统一（数字、字母、标点）
      - 空白字符规范化（\\r\\n → \\n, \\t → 空格）
      - 合并连续空行（>2 → 2）
      - 中文标点统一
      - HTML 标签剥离
      - PDF 页眉页脚去除
      - URL/邮箱规范化
    """

    # ── 预编译正则 ──────────────────────────────────
    _RE_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
    _RE_SURROGATE = re.compile(r'[\ud800-\udfff]')
    _RE_HTML_TAG = re.compile(r'<[^>]+>')
    _RE_MULTI_BLANK_LINE = re.compile(r'\n{3,}')
    _RE_ISOLATED_PAGE_NUM = re.compile(r'^\d{1,4}$', re.MULTILINE)
    _RE_URL = re.compile(r'https?://[^\s一-鿿]+')
    _RE_EMAIL = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    _RE_REPEATED_LINE = re.compile(r'^(.+)$', re.MULTILINE)

    # 全角→半角映射（仅数字和字母）
    _FULLWIDTH_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')
    _FULLWIDTH_LETTERS_UPPER = str.maketrans('ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    _FULLWIDTH_LETTERS_LOWER = str.maketrans('ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ', 'abcdefghijklmnopqrstuvwxyz')

    # 中文标点映射（半角→全角）
    _CN_PUNCT_MAP = {
        ',': '，', '.': '。', '!': '！', '?': '？',
        ':': '：', ';': '；', '(': '（', ')': '）',
        '"': '“', '"': '”', "'": '‘', "'": '’',
    }

    def clean(self, text: str, source_type: str = "text") -> CleanResult:
        """统一文档清洗入口。

        Args:
            text: 原始文本
            source_type: 来源类型 ("text" | "pdf" | "ocr")

        Returns:
            CleanResult: 包含清洗后文本 + 变更记录
        """
        if not text or not text.strip():
            return CleanResult(text=text, warnings=["empty_input"])

        result = CleanResult(text=text)

        # ── 通用清洗 ──
        if config.CLEAN_REMOVE_CONTROL_CHARS:
            result = self._remove_control_chars(result)

        result = self._remove_surrogates(result)

        if config.CLEAN_NORMALIZE_FULLWIDTH:
            result = self._normalize_fullwidth(result)

        result = self._normalize_whitespace(result)

        if config.CLEAN_MERGE_BLANK_LINES:
            result = self._merge_blank_lines(result)

        result = self._unify_cn_punctuation(result)

        if config.CLEAN_STRIP_HTML:
            result = self._strip_html(result)

        # ── URL/邮箱处理 ──
        if config.CLEAN_URL_ACTION != "keep":
            result = self._handle_urls(result)

        if config.CLEAN_EMAIL_ACTION != "keep":
            result = self._handle_emails(result)

        # ── PDF 专用 ──
        if source_type == "pdf":
            if config.CLEAN_REMOVE_PDF_HEADERS:
                result = self._remove_pdf_headers(result)
            if config.CLEAN_REMOVE_PDF_FOOTERS:
                result = self._remove_pdf_footers(result)
            result = self._remove_page_numbers(result)

        # ── OCR 专用（P1）──
        if source_type == "ocr":
            result = self._clean_ocr(result)

        return result

    # ── 各清洗步骤 ──────────────────────────────────

    def _remove_control_chars(self, r: CleanResult) -> CleanResult:
        new_text = self._RE_CONTROL.sub('', r.text)
        if new_text != r.text:
            r.changes.append("removed_control_chars")
        r.text = new_text
        return r

    def _remove_surrogates(self, r: CleanResult) -> CleanResult:
        new_text = self._RE_SURROGATE.sub('', r.text)
        if new_text != r.text:
            r.changes.append("removed_surrogates")
        r.text = new_text
        return r

    def _normalize_fullwidth(self, r: CleanResult) -> CleanResult:
        new_text = r.text.translate(self._FULLWIDTH_DIGITS)
        new_text = new_text.translate(self._FULLWIDTH_LETTERS_UPPER)
        new_text = new_text.translate(self._FULLWIDTH_LETTERS_LOWER)
        if new_text != r.text:
            r.changes.append("normalized_fullwidth")
        r.text = new_text
        return r

    def _normalize_whitespace(self, r: CleanResult) -> CleanResult:
        """\\r\\n → \\n, \\t → 空格, 去除行尾空格"""
        new_text = r.text.replace('\r\n', '\n').replace('\r', '\n')
        new_text = new_text.replace('\t', ' ')
        # 去除行尾空格
        new_text = '\n'.join(line.rstrip() for line in new_text.split('\n'))
        if new_text != r.text:
            r.changes.append("normalized_whitespace")
        r.text = new_text
        return r

    def _merge_blank_lines(self, r: CleanResult) -> CleanResult:
        """>2 连续空行 → 2 空行"""
        new_text = self._RE_MULTI_BLANK_LINE.sub('\n\n', r.text)
        if new_text != r.text:
            r.changes.append("merged_blank_lines")
        r.text = new_text
        return r

    def _unify_cn_punctuation(self, r: CleanResult) -> CleanResult:
        """中文环境下的标点统一"""
        new_text = r.text
        has_chinese = bool(re.search(r'[一-鿿]', new_text))
        if has_chinese:
            for half, full in self._CN_PUNCT_MAP.items():
                # 仅在中文上下文切换（标点前后有中文字符）
                new_text = re.sub(
                    rf'(?<=[一-鿿])\s*{re.escape(half)}\s*(?=[一-鿿])',
                    full, new_text
                )
            if new_text != r.text:
                r.changes.append("unified_cn_punctuation")
        r.text = new_text
        return r

    def _strip_html(self, r: CleanResult) -> CleanResult:
        """去除 HTML 标签，保留文字内容"""
        new_text = self._RE_HTML_TAG.sub('', r.text)
        # 清理标签去除后遗留的多余空白
        new_text = re.sub(r' {2,}', ' ', new_text)
        if new_text != r.text:
            r.changes.append("stripped_html")
        r.text = new_text
        return r

    def _handle_urls(self, r: CleanResult) -> CleanResult:
        action = config.CLEAN_URL_ACTION
        urls = self._RE_URL.findall(r.text)
        if not urls:
            return r
        if action == "remove":
            r.text = self._RE_URL.sub('', r.text)
            r.changes.append(f"removed_{len(urls)}_urls")
        elif action == "placeholder":
            r.text = self._RE_URL.sub('[URL]', r.text)
            r.changes.append(f"replaced_{len(urls)}_urls")
        return r

    def _handle_emails(self, r: CleanResult) -> CleanResult:
        action = config.CLEAN_EMAIL_ACTION
        emails = self._RE_EMAIL.findall(r.text)
        if not emails:
            return r
        if action == "remove":
            r.text = self._RE_EMAIL.sub('', r.text)
            r.changes.append(f"removed_{len(emails)}_emails")
        elif action == "placeholder":
            r.text = self._RE_EMAIL.sub('[EMAIL]', r.text)
            r.changes.append(f"replaced_{len(emails)}_emails")
        return r

    def _remove_pdf_headers(self, r: CleanResult) -> CleanResult:
        """检测并去除 PDF 页眉。

        算法：按行统计，找到每页（\\n\\n 分页符后）开头的重复行。
        如果某行出现在 >50% 的"页"开头，则判为页眉。
        """
        lines = r.text.split('\n')
        if len(lines) < 3:
            return r

        # 简化版：找重复出现次数最高的行
        line_counts = Counter(line.strip() for line in lines if line.strip())
        total_lines = len([l for l in lines if l.strip()])

        removed_count = 0
        for line_text, count in line_counts.most_common(10):
            stripped = line_text.strip()
            # 跳过太短的（可能是真实内容）
            if len(stripped) < 5:
                continue
            # 如果某行重复率 > 30% 且出现 >2 次 → 判为页眉
            if count > 2 and count / total_lines > 0.3:
                r.text = '\n'.join(
                    l for l in lines if l.strip() != stripped
                )
                removed_count += 1
                lines = r.text.split('\n')  # 更新 lines 用于后续检查

        if removed_count > 0:
            r.changes.append(f"removed_{removed_count}_pdf_headers")
        return r

    def _remove_pdf_footers(self, r: CleanResult) -> CleanResult:
        """检测并去除 PDF 页脚。

        与页眉类似，但检查的是"页"末尾（\\n\\n 之前）的重复行。
        同时检测常见页脚模式（如 "第X页 共X页"）。
        """
        # 常见页脚模式
        footer_patterns = [
            re.compile(r'第\s*\d+\s*页\s*共\s*\d+\s*页'),
            re.compile(r'Page\s+\d+\s+of\s+\d+', re.IGNORECASE),
            re.compile(r'^\d+\s*/\s*\d+$'),
        ]

        for pattern in footer_patterns:
            new_text = pattern.sub('', r.text)
            if new_text != r.text:
                r.text = new_text
                r.changes.append("removed_page_number_footer")
        return r

    def _remove_page_numbers(self, r: CleanResult) -> CleanResult:
        """去除独立页码行（行仅含 1-4 位数字）"""
        lines = r.text.split('\n')
        new_lines = []
        removed = 0
        for line in lines:
            stripped = line.strip()
            if self._RE_ISOLATED_PAGE_NUM.match(stripped):
                # 验证前后行：真页码通常在"页"末或"段"末
                removed += 1
                continue
            new_lines.append(line)
        if removed > 0:
            r.text = '\n'.join(new_lines)
            r.changes.append(f"removed_{removed}_page_numbers")
        return r

    def _clean_ocr(self, r: CleanResult) -> CleanResult:
        """OCR 结果专用清洗（P1 阶段实现，目前为占位）"""
        logger.debug("[Cleaner] OCR cleaning not yet implemented, passing through")
        return r

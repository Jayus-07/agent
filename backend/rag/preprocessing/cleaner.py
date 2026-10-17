"""文档清洗器 — 文本规范化 + PDF 页眉页脚去除 + URL/邮箱处理"""
import re
from collections import Counter
from dataclasses import dataclass, field

from backend import config
from backend.rag.preprocessing.redaction import mask_sensitive_text
from backend.shared.logger import logger


# 单节点删除证据上限：质量记录只留可审计摘要，不允许无界膨胀
_MAX_DROP_EVIDENCE = 200


@dataclass
class CleanResult:
    """清洗结果"""
    text: str
    changes: list[str] = field(default_factory=list)   # 执行了哪些清洗操作
    warnings: list[str] = field(default_factory=list)   # 清洗警告（如检测到异常但未处理）
    # 删除/改写证据（2026-10-18 内容完整性修复）：每条记录操作名、噪音分类与被删行样本。
    # 只记录"删掉了什么"，供 Raw AST → 清洗输出 的审计对账，不作为放行依据。
    dropped: list[dict] = field(default_factory=list)


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
    # 维基类模板行（消歧义横幅/Unihan 声明/医学声明）——整行剔除，
    # 2026-10-04 福州旅游攻略 chunk 审计发现三类行混入检索块
    _RE_TEMPLATE_LINE = re.compile(
        r'标题相近或相同的条目|本页有Unihan新版汉字|维基百科中的医学内容'
    )
    # 爬虫语料模板图例行清洗规则。
    # 模板图例行：行首 *[📞]: 电话 / *[查]: 查看该模板（行内 🕘💰 图标是正文清单信息，不在此列）
    _RE_WIKI_LEGEND_LINE = re.compile(r'^\*\[[^\]]{1,8}\]:')
    # 维护横幅：| 此章节**尚无参考来源** ，内容或许**无法查证** 。
    _RE_WIKI_BANNER_LINE = re.compile(r'^\|.*(尚无参考来源|无法查证)')
    # 繁体/闽南罗马字对照行：全汉：沙茶麵 / 全罗：Sa-te-mī
    _RE_POJ_ROSETTA_LINE = re.compile(r'^全[汉罗]：')
    # Wikivoyage 双坐标粘连行：26.09119.17 / 24.49746118.13556
    _RE_MERGED_COORD_LINE = re.compile(r'^\d{1,2}\.\d+\.\d+$')

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

    def clean(
        self,
        text: str,
        source_type: str = "text",
        *,
        structured: bool = False,
    ) -> CleanResult:
        """统一文档清洗入口。

        Args:
            text: 原始文本
            source_type: 来源类型 ("text" | "pdf" | "ocr")
            structured: 传入文本是否已是解析器切好的结构化节点（AST 节点文本）。
                节点文本自带语义边界，**没有**原始版面上下文；依赖"前后空行"
                这类版面证据的启发式（图注残行）在节点级不成立，必须停用，
                否则每个独立节点都会被当成孤立段落删除（2026-10-18 供应商
                规范 fixture：5 个内容叶节点被清空）。

        Returns:
            CleanResult: 包含清洗后文本 + 变更记录 + 删除证据
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

        # ── 模板行/维基噪音行剔除 ──
        if config.CLEAN_DROP_TEMPLATE_LINES:
            result = self._remove_template_lines(result)
            result = self._remove_wiki_noise_lines(result)
            if source_type == "text" and not structured:
                # 图注残留整段孤立，仅在 text（Markdown 抓取稿）全篇文本上启用；
                # PDF 抽取文本的孤立短行多为真实标题/小节名，不适用。
                # 结构化节点（structured=True）没有版面上下文，规则停用——
                # 见 clean() docstring。
                result = self._remove_isolated_caption_lines(result)

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

    @staticmethod
    def _note_drop(r: CleanResult, operation: str, line: str,
                   category: str = "deterministic_noise") -> None:
        """记录一条删除证据（操作名/噪音分类/原行样本），供清洗对账审计。"""
        if len(r.dropped) >= _MAX_DROP_EVIDENCE:
            return
        # 证据可能来自正文：只保留脱敏后的掩码形态（手机号/邮箱/凭据），
        # 既能定位被删内容，又不把明文秘密写进质量记录与 Trace 摘要。
        r.dropped.append({
            "operation": operation,
            "category": category,
            "line": mask_sensitive_text(str(line).strip(), limit=120),
        })

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

    def _remove_template_lines(self, r: CleanResult) -> CleanResult:
        """维基类模板行整行剔除（消歧义横幅/Unihan 声明/医学声明）。

        只删命中行、不动其余内容——这三类是页面级模板，整行即全部噪音，
        不存在"行内混有正文"的形态（2026-10-04 chunk 审计确认）。
        """
        kept, dropped = [], 0
        for line in r.text.split('\n'):
            if self._RE_TEMPLATE_LINE.search(line):
                dropped += 1
                self._note_drop(r, "template_line", line)
                continue
            kept.append(line)
        if dropped:
            r.text = '\n'.join(kept)
            r.changes.append(f"dropped_template_lines({dropped})")
        return r

    def _remove_wiki_noise_lines(self, r: CleanResult) -> CleanResult:
        """第二批维基抓取稿四类机械噪音行（整行形态稳定，逐类计数可观测）。"""
        patterns = {
            "legend": self._RE_WIKI_LEGEND_LINE,
            "banner": self._RE_WIKI_BANNER_LINE,
            "rosetta": self._RE_POJ_ROSETTA_LINE,
            "coord": self._RE_MERGED_COORD_LINE,
        }
        kept, dropped = [], []
        for line in r.text.split('\n'):
            hit = next((name for name, pat in patterns.items() if pat.search(line)), None)
            if hit:
                dropped.append(hit)
                self._note_drop(r, f"wiki_{hit}", line)
                continue
            kept.append(line)
        if dropped:
            r.text = '\n'.join(kept)
            for name in patterns:
                n = dropped.count(name)
                if n:
                    r.changes.append(f"dropped_wiki_{name}_lines({n})")
        return r

    def _remove_isolated_caption_lines(self, r: CleanResult) -> CleanResult:
        """图片 alt 残留/占位短行：前后皆空行的孤立段落，≤40 字、无句读。

        2026-10-04 保留语料全量扫描（55 处命中全部为图注/[添加列表项]/面包屑，
        零正文命中）后定形：含 CJK、非标题/列表/表格/引用前缀、不含 '|'，
        不含中英文句末与键值标点。有句读的短行是正常陈述，半角问号结尾的 FAQ
        问题也必须保留。
        """
        lines = r.text.split('\n')
        # 保护：整块只有一行时，该行是块的唯一内容，不构成"孤立图注"证据。
        # 版面启发式必须至少有两个非空行才可能判定"孤立"。
        if sum(1 for line in lines if line.strip()) < 2:
            return r
        kept, dropped = [], 0
        for i, line in enumerate(lines):
            s = line.strip()
            if (
                6 <= len(s) <= 40
                and (i == 0 or lines[i - 1].strip() == '')
                and (i == len(lines) - 1 or lines[i + 1].strip() == '')
                and not re.match(r'^[#\-*|>]', s)
                and '|' not in s
                and not any(t in s for t in '。！？：；…?!:;.')
                and re.search(r'[一-鿿]', s)
            ):
                dropped += 1
                self._note_drop(r, "isolated_caption", line, category="layout_heuristic")
                continue
            kept.append(line)
        if dropped:
            r.text = '\n'.join(kept)
            r.changes.append(f"dropped_isolated_captions({dropped})")
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
                self._note_drop(r, "pdf_header_line", stripped)
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
            for match in pattern.findall(r.text):
                self._note_drop(r, "page_footer", match)
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
                self._note_drop(r, "page_number_line", stripped)
                removed += 1
                continue
            new_lines.append(line)
        if removed > 0:
            r.text = '\n'.join(new_lines)
            r.changes.append(f"removed_{removed}_page_numbers")
        return r

    # 审计侧噪音形态：只收"行形态即可判定"的确定性噪音（页码/页脚/
    # 模板行/维基噪音/URL 邮箱/纯符号）。故意**不含**孤立图注这类版面
    # 启发式——它必须依靠版面证据，不能自己免除自己的嫌疑。
    _AUDIT_NOISE_PATTERNS = {
        "page_number": re.compile(r'^\d{1,4}$'),
        "page_footer": re.compile(r'^第\s*\d+\s*页\s*共\s*\d+\s*页$'),
        "page_footer_en": re.compile(r'^Page\s+\d+\s+of\s+\d+$', re.IGNORECASE),
        "template_line": _RE_TEMPLATE_LINE,
        "wiki_legend": _RE_WIKI_LEGEND_LINE,
        "wiki_banner": _RE_WIKI_BANNER_LINE,
        "wiki_rosetta": _RE_POJ_ROSETTA_LINE,
        "wiki_coord": _RE_MERGED_COORD_LINE,
    }

    @classmethod
    def classify_noise_line(cls, line: str) -> str:
        """判定一行是否属于可独立论证的确定性噪音；返回类别，非噪音返回空串。

        审计用它区分"合法噪音删除"与"异常正文丢失"：只有形态上就是页码/
        模板行/纯符号的行才允许在清洗后消失。
        """
        s = (line or "").strip()
        if not s:
            return "blank"
        for name, pattern in cls._AUDIT_NOISE_PATTERNS.items():
            if pattern.search(s):
                return name
        if cls._RE_URL.fullmatch(s) or cls._RE_EMAIL.fullmatch(s):
            return "url_or_email"
        if re.fullmatch(r'[\W_]+', s, re.UNICODE) and not re.search(r'[一-鿿]', s):
            return "symbol_only"
        return ""

    def normalize_for_compare(self, text: str) -> str:
        """只做规范化、不删任何内容，用于清洗前后对账。

        原文与清洗文本允许合法规范化差异（控制字符/全半角/空白/中文标点/
        HTML 标签），但删除一行正文不属于规范化，审计必须能分辨两者。
        """
        r = CleanResult(text=str(text or ""))
        if config.CLEAN_REMOVE_CONTROL_CHARS:
            r = self._remove_control_chars(r)
        r = self._remove_surrogates(r)
        if config.CLEAN_NORMALIZE_FULLWIDTH:
            r = self._normalize_fullwidth(r)
        r = self._normalize_whitespace(r)
        if config.CLEAN_MERGE_BLANK_LINES:
            r = self._merge_blank_lines(r)
        r = self._unify_cn_punctuation(r)
        if config.CLEAN_STRIP_HTML:
            r = self._strip_html(r)
        return r.text

    def _clean_ocr(self, r: CleanResult) -> CleanResult:
        """OCR 结果专用清洗（P1 阶段实现，目前为占位）"""
        logger.debug("[Cleaner] OCR cleaning not yet implemented, passing through")
        return r

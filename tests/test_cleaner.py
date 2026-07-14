"""测试文档清洗器"""
import pytest
from preprocessing.cleaner import DocumentCleaner, CleanResult


class TestTextNormalization:
    def setup_method(self):
        """每个测试前：启用所有清洗开关，确保测试可独立运行"""
        import config
        # 临时启用所有清洗功能（默认值均为 false，测试需显式开启）
        config.CLEAN_REMOVE_CONTROL_CHARS = True
        config.CLEAN_NORMALIZE_FULLWIDTH = True
        config.CLEAN_MERGE_BLANK_LINES = True
        config.CLEAN_STRIP_HTML = True
        config.CLEAN_REMOVE_PDF_HEADERS = True
        config.CLEAN_REMOVE_PDF_FOOTERS = True
        config.CLEAN_URL_ACTION = "placeholder"
        config.CLEAN_EMAIL_ACTION = "placeholder"
        self.cleaner = DocumentCleaner()

    def test_remove_control_chars(self):
        """去除控制字符，保留换行和制表符"""
        text = "hello\x00\x01\x02 world\nline2"
        result = self.cleaner.clean(text, source_type="text")
        assert "\x00" not in result.text
        assert "\n" in result.text  # 保留换行

    def test_normalize_fullwidth(self):
        """全角半角统一"""
        text = "１２３ａｂｃ，。！"
        result = self.cleaner.clean(text, source_type="text")
        assert "123" in result.text
        assert "abc" in result.text

    def test_merge_blank_lines(self):
        """合并连续空行，最多保留2个"""
        text = "line1\n\n\n\n\nline2"
        result = self.cleaner.clean(text, source_type="text")
        assert "\n\n\n\n\n" not in result.text
        assert "line1\n\nline2" in result.text

    def test_strip_html_tags(self):
        """HTML 标签剥离"""
        text = "<div><p>Hello</p><br>World</div>"
        result = self.cleaner.clean(text, source_type="text")
        assert "<div>" not in result.text
        assert "Hello" in result.text
        assert "World" in result.text

    def test_unify_chinese_punctuation(self):
        """中文标点统一"""
        text = "你好,这是测试.请确认!"
        result = self.cleaner.clean(text, source_type="text")
        assert "，" in result.text  # 英文逗号转中文
        assert "。" in result.text  # 英文句号转中文

    def test_pdf_header_removal(self):
        """PDF 页眉检测和去除"""
        text = """电商平台运营手册
第一章 概述
电商平台运营手册
1.1 背景介绍
电商平台运营手册
这是正文内容。"""
        result = self.cleaner.clean(text, source_type="pdf")
        # "电商平台运营手册" 重复出现3次，应被识别为页眉并去除
        count = result.text.count("电商平台运营手册")
        assert count <= 1  # 最多保留一次（可能是正文中的引用）

    def test_pdf_page_number_removal(self):
        """PDF 页码去除"""
        text = "这是正文内容。\n42\n\n下一页内容。\n43\n"
        result = self.cleaner.clean(text, source_type="pdf")
        assert "\n42\n" not in result.text

    def test_clean_result_structure(self):
        """验证返回结构"""
        text = "clean text"
        result = self.cleaner.clean(text, source_type="text")
        assert isinstance(result, CleanResult)
        assert isinstance(result.text, str)
        assert isinstance(result.changes, list)
        assert isinstance(result.warnings, list)

    def test_empty_text(self):
        """空文本不崩溃"""
        result = self.cleaner.clean("", source_type="text")
        assert result.text == ""

    def test_url_placeholder(self):
        """URL 替换为占位符"""
        text = "详情见 https://example.com/doc/123 页面"
        result = self.cleaner.clean(text, source_type="text")
        # URL 应被替换为占位符
        assert "https://" not in result.text
        assert "[URL]" in result.text

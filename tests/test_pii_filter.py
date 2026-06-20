"""PII Filter 测试 — 正则脱敏"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.pii_filter import scan_and_sanitize, has_pii


class TestPiiFilter:
    """PII 正则检测与脱敏"""

    def test_phone_number(self):
        result = scan_and_sanitize("联系电话：13800138001")
        assert result.has_pii
        assert "13800138001" not in result.sanitized
        assert "[手机号]" in result.sanitized
        assert any(d["type"] == "手机号" for d in result.detections)

    def test_phone_number_preserves_context(self):
        """脱敏保留语义骨架"""
        result = scan_and_sanitize("张伟的手机号是13912345678，请记录")
        assert "张伟" in result.sanitized
        assert "13912345678" not in result.sanitized
        assert "[手机号]" in result.sanitized
        assert "请记录" in result.sanitized

    def test_id_card_number(self):
        result = scan_and_sanitize("身份证号：110101199001011234")
        assert result.has_pii
        assert "[身份证号]" in result.sanitized

    def test_email(self):
        result = scan_and_sanitize("邮箱：test@example.com")
        assert result.has_pii
        assert "test@example.com" not in result.sanitized
        assert "[邮箱]" in result.sanitized

    def test_ip_address(self):
        result = scan_and_sanitize("服务器地址：192.168.1.100")
        assert result.has_pii
        assert "[IP地址]" in result.sanitized

    def test_bank_card(self):
        result = scan_and_sanitize("银行卡：6222021234567890123")
        assert result.has_pii
        assert "[银行卡号]" in result.sanitized

    def test_credit_code(self):
        result = scan_and_sanitize("统一信用代码：91310000MA1FL1NEXB")
        assert result.has_pii
        assert "[统一社会信用代码]" in result.sanitized

    def test_no_pii_text(self):
        result = scan_and_sanitize("今天天气很好，适合出去散步")
        assert not result.has_pii
        assert result.sanitized == result.original

    def test_empty_text(self):
        result = scan_and_sanitize("")
        assert not result.has_pii
        assert result.sanitized == ""

    def test_multiple_pii_types(self):
        text = "张三的电话13800138001，邮箱zhangsan@test.com"
        result = scan_and_sanitize(text)
        assert result.has_pii
        assert len(result.detections) >= 2
        types = [d["type"] for d in result.detections]
        assert "手机号" in types
        assert "邮箱" in types

    def test_has_pii_true(self):
        assert has_pii("手机号: 13800138001")

    def test_has_pii_false(self):
        assert not has_pii("这是一段普通文本")

    def test_original_preserved(self):
        text = "联系 13800138001"
        result = scan_and_sanitize(text)
        assert result.original == text  # 原始文本不变

    def test_phone_in_sentence(self):
        """手机号嵌入中文句子"""
        result = scan_and_sanitize("请拨打13800138001咨询")
        assert result.has_pii
        assert "拨打" in result.sanitized
        assert "[手机号]" in result.sanitized

    def test_idcard_prefix_boundary(self):
        """身份证前后是非数字时也能匹配"""
        result = scan_and_sanitize("号码：110101199003077894。")
        assert result.has_pii
        assert "[身份证号]" in result.sanitized

    def test_bankcard_excludes_year(self):
        """银行卡检测排除年份和全0"""
        result = scan_and_sanitize("成立于2020年，编号0000000000000000")
        # 2020 是年份可能被排除，全0被排除
        assert "2020" in result.sanitized  # 年份保留

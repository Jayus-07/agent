"""tests/test_text_split.py — 列表串统一拆分（shared/text_split.py）测试。

背景：2026-09-21 格式巡检发现 travel POI 标签、邮件收件人、数据采集
键三处各自为政地拆分，顿号（中文列举最常用分隔符）全仓无人处理。
"""
import pytest

from backend.shared.text_split import split_list


class TestSplitList:
    @pytest.mark.parametrize("raw,expected", [
        ("a,b,c", ["a", "b", "c"]),
        ("a，b，c", ["a", "b", "c"]),
        ("自然、人文、美食", ["自然", "人文", "美食"]),      # 顿号：巡检发现的主病灶
        ("a; b；c", ["a", "b", "c"]),
        ("a b c", ["a", "b", "c"]),
        ("lat:26.08 lng:119.29", ["lat:26.08", "lng:119.29"]),  # 冒号不是分隔符
        ("  a@example.com ， B@example.com  ", ["a@example.com", "B@example.com"]),
        ("a,,b", ["a", "b"]),
        ("a@example.com", ["a@example.com"]),
    ])
    def test_separators(self, raw, expected):
        assert split_list(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty(self, raw):
        assert split_list(raw) == []

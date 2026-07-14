"""测试脏数据过滤器"""
import pytest
from preprocessing.filter import ChunkFilter, DuplicateDetector, _mask_pii


class TestChunkFilter:
    def setup_method(self):
        self.filter = ChunkFilter()
        # 默认 PII 脱敏关闭，PII 测试中按需开启
        self.filter.enable_pii = False

    def test_filter_empty_content(self):
        """过滤空白内容"""
        ok, reason = self.filter.should_keep("   \n  \t  ", {})
        assert not ok
        assert reason == "empty"

    def test_keep_valid_content(self):
        """保留正常内容"""
        ok, reason = self.filter.should_keep("这是一段有意义的电商知识内容。", {})
        assert ok
        assert reason == "clean"

    def test_filter_too_short(self):
        """过滤超短文本"""
        ok, reason = self.filter.should_keep("你好", {})
        assert not ok
        assert reason == "too_short"

    def test_filter_all_symbols(self):
        """过滤纯符号文本"""
        ok, reason = self.filter.should_keep("★★★★★ ====== >>>>>>", {})
        assert not ok
        assert reason == "all_symbols"

    def test_filter_low_chinese_ratio(self):
        """中文知识库过滤纯英文/数字文本"""
        ok, reason = self.filter.should_keep("This is all English text with no Chinese at all", {})
        assert not ok
        assert reason == "low_chinese_ratio"

    def test_pii_masking_phone(self):
        """手机号脱敏 — 通过 apply_pii_mask 静态方法"""
        self.filter.enable_pii = True
        text = "请联系客服：13812345678 获取帮助"
        meta = {}
        ok, reason = self.filter.should_keep(text, meta)
        assert ok
        assert meta.get("pii_masked") == ["phone"]
        # 验证脱敏后的文本
        masked = ChunkFilter.apply_pii_mask(text)
        assert "13812345678" not in masked
        assert "138****5678" in masked

    def test_pii_masking_id_card(self):
        """身份证号脱敏 — 通过 apply_pii_mask 静态方法"""
        self.filter.enable_pii = True
        text = "身份证号：110101199001011234 请核实"
        meta = {}
        ok, reason = self.filter.should_keep(text, meta)
        assert ok
        assert meta.get("pii_masked") == ["id_card"]
        # 验证脱敏后的文本
        masked = ChunkFilter.apply_pii_mask(text)
        assert "110101199001011234" not in masked

    def test_metadata_enriched(self):
        """metadata 中包含过滤状态"""
        meta = {}
        text = "这是一段正常的电商知识库内容，包含商品管理相关信息。"
        ok, reason = self.filter.should_keep(text, meta)
        assert ok
        assert meta.get("filter_status") == "clean"


class TestDuplicateDetector:
    def test_exact_duplicate(self):
        """精确重复检测"""
        dd = DuplicateDetector(threshold=3)
        text1 = "电商平台运营手册第一章概述，包含商品管理和订单履约流程。"
        text2 = "电商平台运营手册第一章概述，包含商品管理和订单履约流程。"
        assert not dd.is_duplicate(text1)  # 第一个不重复
        assert dd.is_duplicate(text2)       # 第二个重复

    def test_near_duplicate(self):
        """近似重复检测"""
        dd = DuplicateDetector(threshold=3)
        base = (
            "电商平台运营手册是一本全面介绍跨境电商运营的指南，"
            "涵盖了从商品选品、上架优化、订单管理、物流配送、仓储库存、"
            "客户服务、广告投放、数据分析到财务管理的全流程知识体系，"
            "旨在帮助运营人员快速掌握平台规则和运营技巧，提升店铺的竞争力和盈利能力。"
        )
        text1 = base + "本书适合新手和有经验的运营人员阅读。"
        text2 = base + "本书适合新手和有经验的运营人员阅读"  # 差一个句号
        dd.is_duplicate(text1)
        assert dd.is_duplicate(text2)  # SimHash 汉明距离很小

    def test_different_content(self):
        """不同内容不判重复"""
        dd = DuplicateDetector(threshold=3)
        text1 = "电商平台运营手册第一章概述，介绍商品管理基础知识。"
        text2 = "这是完全不同的另一段文本内容，讲述客户服务流程。"
        dd.is_duplicate(text1)
        assert not dd.is_duplicate(text2)

    def test_reset(self):
        """重置检测器"""
        dd = DuplicateDetector(threshold=3)
        dd.is_duplicate("电商平台运营手册第一章概述内容")
        dd.reset()
        assert not dd.is_duplicate("电商平台运营手册第一章概述内容")  # 重置后不判重复


class TestPIIMask:
    """PII 脱敏函数单元测试"""

    def test_mask_phone(self):
        """手机号脱敏：保留前3后4"""
        text = "联系 13812345678 或 15900001111"
        masked, types = _mask_pii(text)
        assert "13812345678" not in masked
        assert "15900001111" not in masked
        assert "138****5678" in masked
        assert "159****1111" in masked
        assert types == ["phone"]

    def test_mask_id_card(self):
        """身份证脱敏：保留前6后4"""
        text = "身份证 110101199001011234"
        masked, types = _mask_pii(text)
        assert "110101199001011234" not in masked
        assert types == ["id_card"]

    def test_mask_bank_card(self):
        """银行卡脱敏：保留前4后4"""
        text = "卡号 6222021234567890123 请保存"
        masked, types = _mask_pii(text)
        assert "6222021234567890123" not in masked
        assert types == ["bank_card"]

    def test_no_pii(self):
        """无 PII 文本原样返回"""
        text = "这是普通文本，不含敏感信息"
        masked, types = _mask_pii(text)
        assert masked == text
        assert types == []

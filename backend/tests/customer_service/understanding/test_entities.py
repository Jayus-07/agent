"""test_entities.py — 实体提取器单元测试（零改写原则）。"""
import pytest

from backend.customer_service.understanding.entities import extract_entities
from backend.customer_service.understanding.types import EntityType
from backend.security.input_guard.normalize import normalize_query


def _ids(text: str) -> list[str]:
    return [e.match() for e in extract_entities(normalize_query(text))
            if e.type == EntityType.ORDER_ID]


class TestOrderIdExtraction:
    def test_single_segment(self):
        assert _ids("查一下订单 DEMO-1006 的状态") == ["DEMO-1006"]

    def test_two_segment_full_match(self):
        # 两段式不得截断（WP-G 缺陷回归）
        assert _ids("订单 ORD-20260915-0042 申请退款") == ["ORD-20260915-0042"]

    def test_multiple_orders(self):
        assert _ids("DEMO-1001 和 DEMO-1002 都帮我看看") == ["DEMO-1001", "DEMO-1002"]

    def test_fullwidth_normalized_by_nfkc(self):
        # 全角 → 半角属于编码归一（normalize_query 事实源），允许
        assert _ids("查订单 ＤＥＭＯ－１００３") == ["DEMO-1003"]

    def test_lowercase_casefold_allowed(self):
        assert _ids("查 demo-1006") == ["DEMO-1006"]

    def test_zero_rewrite_on_lookalike_typo(self):
        # 0/O 形近错别字：必须原样保留，禁止静默纠正
        assert _ids("查一下订单 DEM0-1006 的状态") == ["DEM0-1006"]
        assert _ids("查一下订单 DEMO-1O08 的状态") == ["DEMO-1O08"]

    def test_no_hyphen_not_claimed(self):
        # 缺连字符不擅自补（DEMO1006 不是一个合法抽取形态）
        assert _ids("查下 DEMO1006 的状态") == []

    def test_digits_in_chinese_text_not_order(self):
        assert _ids("我等了 1006 天了") == []


class TestOtherEntities:
    def test_amount(self):
        ents = extract_entities(normalize_query("退款 358.00 元到原卡"))
        amount = [e.value for e in ents if e.type == EntityType.AMOUNT]
        assert amount == ["358.00"]

    def test_phone_and_email(self):
        ents = extract_entities(normalize_query(
            "手机号 13800138000，邮箱 a.b@test.com"))
        by = {e.type: [x.value for x in ents if x.type == e.type]
              for e in ents}
        assert by[EntityType.PHONE] == ["13800138000"]
        assert by[EntityType.EMAIL] == ["a.b@test.com"]

    def test_tracking_with_carrier_prefix(self):
        ents = extract_entities(normalize_query("运单号 SF1380001234567 到哪了"))
        track = [e.match() for e in ents if e.type == EntityType.TRACKING_NO]
        assert track == ["SF1380001234567"]

    def test_bare_tracking_requires_context(self):
        # 无语境的 12 位数字不认领为运单
        assert [e for e in extract_entities("1380001234567")
                if e.type == EntityType.TRACKING_NO] == []
        assert [e for e in extract_entities("快递单号 1380001234567 到哪了")
                if e.type == EntityType.TRACKING_NO]

    def test_quoted_product(self):
        ents = extract_entities(normalize_query("「石墨烯暖手宝」还有货吗"))
        prod = [e.value for e in ents if e.type == EntityType.PRODUCT]
        assert prod == ["石墨烯暖手宝"]

    @pytest.mark.parametrize("raw", ["", None], ids=["empty", "none"])
    def test_empty_input(self, raw):
        assert extract_entities(raw or "") == []

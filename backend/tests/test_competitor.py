"""竞品分析模块完整测试套件

覆盖:
  - adapters.py: 平台识别 / 价格抽取 / 促销 / 评价数 / 币种 / 规则抽取 / 可信度
  - extractor.py: LLM JSON 解析 / 字段归一化 / LLM+规则混合抽取
  - store.py: watchlist CRUD / snapshot append-only / history / latest_snapshot
  - pipeline.py: analyze_url / scan_watchlist / history_report / _compare_with_previous / _fmt_price
  - tools/competitor.py: tool 入口各 action 分支 / URL 提取 / watchlist 格式化
"""
import json
import os
import sys
import tempfile
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ────────────────────────────────────────────────────────────────────────────
#  adapters.py 测试
# ────────────────────────────────────────────────────────────────────────────
from backend.competitor.adapters import (
    _extract_highlights,
    _extract_promo,
    _extract_rating,
    _extract_review_count,
    _guess_title,
    _parse_price_tokens,
    confidence,
    detect_currency,
    detect_platform,
    extract_by_rules,
)
from backend.competitor.crypto import (
    decrypt_cookie,
    encrypt_cookie,
    maybe_decrypt,
    maybe_encrypt,
)
from backend.competitor.qr_login import (
    QrStatus,
    get_supported_platforms,
    poll_qr_login,
    start_qr_login,
)


# ── detect_platform ──────────────────────────────────────────────────────


class TestDetectPlatform:
    def test_jd(self):
        assert detect_platform("https://item.jd.com/100012345.html") == "jd"

    def test_tmall(self):
        assert detect_platform("https://detail.tmall.com/item.htm?id=123") == "tmall"

    def test_taobao(self):
        assert detect_platform("https://item.taobao.com/item.htm?id=456") == "taobao"

    def test_amazon_com(self):
        assert detect_platform("https://www.amazon.com/dp/B0EXAMPLE") == "amazon"

    def test_amazon_co_jp(self):
        assert detect_platform("https://www.amazon.co.jp/dp/B0EXAMPLE") == "amazon"

    def test_pdd(self):
        assert detect_platform("https://mobile.yangkeduo.com/goods.html?goods_id=123") == "pdd"

    def test_suning(self):
        assert detect_platform("https://product.suning.com/0070066836/12345.html") == "suning"

    def test_douyin(self):
        assert detect_platform("https://www.douyin.com/video/7301234567890") == "douyin"

    def test_jinritemai(self):
        assert detect_platform("https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=123") == "douyin"

    def test_generic_unknown(self):
        assert detect_platform("https://www.example.com/product/123") == "generic"

    def test_case_insensitive(self):
        assert detect_platform("https://ITEM.JD.COM/12345.html") == "jd"

    def test_empty_url(self):
        assert detect_platform("") == "generic"


# ── _parse_price_tokens ──────────────────────────────────────────────────


class TestParsePriceTokens:
    def test_yen_symbol(self):
        assert _parse_price_tokens("价格 ¥199.00 元") == [199.0]

    def test_fullwidth_yen(self):
        assert _parse_price_tokens("￥1,299.00") == [1299.0]

    def test_price_with_yuan_suffix(self):
        assert _parse_price_tokens("售价 299.00元") == [299.0]

    def test_json_price_field(self):
        assert _parse_price_tokens('"price":"129.00"') == [129.0]

    def test_multiple_prices_returns_first_pattern_match(self):
        text = "现价 ¥199 原价 ¥299"
        prices = _parse_price_tokens(text)
        assert prices[0] == 199.0
        assert 299.0 in prices

    def test_no_price_returns_empty(self):
        assert _parse_price_tokens("这是一段没有价格的文字") == []

    def test_filters_unreasonable_prices(self):
        # 低于 0.5 或高于 10_000_000 的价格应被过滤
        assert _parse_price_tokens("¥0.01") == []
        # 注意: ¥99999999 正则只匹配前 6 位 999999（在合理区间内），需用逗号分隔的大数触发过滤
        assert _parse_price_tokens("¥10,000,001.00") == []

    def test_handles_comma_separator(self):
        assert _parse_price_tokens("¥1,299,999.00") == [1299999.0]


# ── _guess_title ─────────────────────────────────────────────────────────


class TestGuessTitle:
    def test_picks_first_reasonable_line(self):
        md = "# 标题\n\nApple iPhone 15 Pro Max 256GB 原色钛金属 全网通5G手机\n\n价格 ¥9999"
        title = _guess_title(md, "jd")
        assert "iPhone" in title

    def test_skips_short_lines(self):
        md = "短\n\n这是一个足够长的商品标题描述文本内容\n\n其他内容"
        assert _guess_title(md, "generic") == "这是一个足够长的商品标题描述文本内容"

    def test_skips_price_only_lines(self):
        md = "¥199 299 399\n\n这个商品标题内容在这里等着被选中\n\n更多内容"
        assert _guess_title(md, "generic") == "这个商品标题内容在这里等着被选中"

    def test_returns_empty_when_no_match(self):
        assert _guess_title("", "generic") == ""

    def test_title_max_length(self):
        # 超过 200 字符的行不选（Amazon 商品标题通常 100-200 字符）
        md = "x" * 201 + "\n合适的标题文本内容在这里等着被选中\n"
        assert _guess_title(md, "generic") == "合适的标题文本内容在这里等着被选中"

    def test_skips_account_name_line(self):
        # 淘宝登录后页面首行是用户昵称（纯 ASCII 账号名），不应被当作标题
        md = "tb50348234\n\n网页无障碍\n\n这是一个足够长的商品标题描述文本内容\n\n¥76"
        assert _guess_title(md, "taobao") == "这是一个足够长的商品标题描述文本内容"

    def test_taobao_anchor_on_sold_line(self):
        # taobao/tmall: 以“已售”行为锚点，取其上方第一条有效文本行；
        # 促销文案（含 券/补贴/限时）不应被误选为标题
        md = (
            "水桶包配件diy材料\n搜索\n"
            "图文详情\n"
            "平台补贴！开通88VIP领185元消费券，限时有效！\n"
            "趣织社奶油甜筒包diy编织钩针单肩包包毛线团自制材料包\n"
            "已售 100+\n"
            "￥\n76\n起\n"
        )
        assert _guess_title(md, "taobao") == "趣织社奶油甜筒包diy编织钩针单肩包包毛线团自制材料包"

    def test_taobao_rule_extraction_integration(self):
        # 登录后真实页面正文（精简版）：标题/价格/评价数均应命中
        md = (
            "tb50348234\n搜索\n图文详情\n"
            "平台补贴！开通88VIP领185元消费券，限时有效！\n"
            "趣织社奶油甜筒包diy编织钩针单肩包包毛线团自制材料包\n"
            "已售 100+\n用户评价·100+\n￥\n76\n起\n满200减25\n有货"
        )
        result = extract_by_rules("taobao", md)
        assert result["title"] == "趣织社奶油甜筒包diy编织钩针单肩包包毛线团自制材料包"
        assert result["price"] == 76.0
        assert result["review_count"] == 100


# ── _extract_promo ───────────────────────────────────────────────────────


class TestExtractPromo:
    def test_extracts_promo_lines(self):
        md = "普通文字\n限时折扣 满300减50\n更多内容\n满减优惠券领取"
        result = _extract_promo(md)
        assert "折扣" in result
        assert "满减" in result

    def test_no_promo_returns_empty(self):
        assert _extract_promo("这是一段完全普通的文本没有任何关键词") == ""

    def test_max_three_hits(self):
        lines = ["促销1 满减", "促销2 折扣", "促销3 优惠券", "促销4 秒杀"]
        md = "\n".join(lines)
        result = _extract_promo(md)
        # 最多 3 条
        assert result.count("|") == 2  # 3 条用 " | " 分隔 = 2 个分隔符

    def test_skips_long_lines(self):
        md = "x" * 81 + " 满减" + "\n短行 限时折扣"
        result = _extract_promo(md)
        assert "折扣" in result
        assert "满减" not in result  # 超过 80 字符的行被跳过

    def test_case_insensitive_keywords(self):
        assert _extract_promo("Big SALE today") != ""


# ── _extract_review_count ────────────────────────────────────────────────


class TestExtractReviewCount:
    def test_simple_count(self):
        assert _extract_review_count("10000条评价") == 10000

    def test_with_comma(self):
        assert _extract_review_count("1,234 条评论") == 1234

    def test_with_wan(self):
        assert _extract_review_count("5万+评价") == 50000

    def test_english_reviews(self):
        assert _extract_review_count("2500 reviews") == 2500

    def test_no_reviews(self):
        assert _extract_review_count("没有评价信息") is None

    def test_taobao_review_dot_format(self):
        # 淘宝: 用户评价·100+
        assert _extract_review_count("用户评价·100+") == 100

    def test_taobao_sold_format(self):
        # 淘宝: 已售 100+
        assert _extract_review_count("已售 100+") == 100

    def test_taobao_sold_with_wan(self):
        assert _extract_review_count("已售 2万+") == 20000


# ── _extract_rating / _extract_highlights ──────────────────────


class TestExtractRating:
    def test_amazon_out_of_5(self):
        assert _extract_rating("4.6 out of 5 stars") == 4.6

    def test_chinese_stars(self):
        assert _extract_rating("4.5 颗星，最多 5 颗星") == 4.5

    def test_jd_good_rate(self):
        # 京东好评率 98% → 换算 5 分制约 4.9
        assert _extract_rating("好评率 98%") == 4.9

    def test_no_rating(self):
        assert _extract_rating("普通商品描述文本") is None


class TestExtractHighlights:
    def test_english_section(self):
        md = "# Title\nAbout this item\n* Long battery life 20h\n* USB-C fast charging\nOther text"
        hl = _extract_highlights(md)
        assert "battery" in hl and "USB-C" in hl

    def test_chinese_section(self):
        md = "产品特点\n- 大容量电池续航持久\n- 支持快速充电协议\n结束行"
        hl = _extract_highlights(md)
        assert "续航" in hl and "快速充电" in hl

    def test_no_section(self):
        assert _extract_highlights("没有卖点区块的普通文本") == ""


# ── detect_currency ──────────────────────────────────────────────────────


class TestDetectCurrency:
    def test_gbp(self):
        assert detect_currency("Price: £199.00") == "GBP"

    def test_eur(self):
        assert detect_currency("Preis: €299.00") == "EUR"

    def test_usd(self):
        assert detect_currency("Price: $199.00") == "USD"

    def test_cny_default(self):
        assert detect_currency("价格 199 元") == "CNY"

    def test_only_checks_head(self):
        # 只在 head 3000 字符内检查
        text = "x" * 3001 + "£"
        assert detect_currency(text) == "CNY"


# ── extract_by_rules ─────────────────────────────────────────────────────


class TestExtractByRules:
    def test_full_extraction(self):
        md = """# Apple iPhone 15 Pro Max 256GB 原色钛金属
价格 ¥9999 划线价 ¥10999
限时满减 满5000减200
50000条评价
"""
        result = extract_by_rules("jd", md)
        assert result["price"] == 9999.0
        assert result["original_price"] == 10999.0
        assert result["currency"] == "CNY"
        assert result["promo_text"] != ""
        assert result["review_count"] == 50000
        assert result["in_stock"] == 1

    def test_out_of_stock(self):
        md = "商品标题\n¥199\n无货"
        result = extract_by_rules("generic", md)
        assert result["in_stock"] == 0

    def test_sold_out(self):
        md = "商品标题\n¥199\n售罄"
        result = extract_by_rules("generic", md)
        assert result["in_stock"] == 0

    def test_missing_fields_are_none(self):
        result = extract_by_rules("generic", "只有标题文本内容太短")
        assert result["price"] is None
        assert result["review_count"] is None


# ── confidence ───────────────────────────────────────────────────────────


class TestConfidence:
    def test_full_result(self):
        result = {"price": 199, "title": "商品标题", "review_count": 100,
                  "promo_text": "", "rating": 4.5, "highlights": "卖点"}
        assert confidence(result) == 1.0

    def test_price_only(self):
        assert confidence({"price": 199, "title": ""}) == 0.5

    def test_title_only(self):
        assert confidence({"price": None, "title": "标题"}) == 0.3

    def test_nothing(self):
        assert confidence({"price": None, "title": ""}) == 0.0

    def test_price_and_promo(self):
        # 0.5(价格) + 0.1(促销/评价) = 0.6
        result = {"price": 199, "title": "", "promo_text": "满减", "review_count": None}
        assert confidence(result) == 0.6


# ────────────────────────────────────────────────────────────────────────────
#  extractor.py 测试
# ────────────────────────────────────────────────────────────────────────────
from backend.competitor.extractor import (
    _normalize,
    _parse_llm_json,
    _smart_window,
    extract_fields,
)


class TestSmartWindow:
    """智能截断：长文档保留头部 + 折叠区关键段（评分/评价/卖点）"""

    def test_short_doc_unchanged(self):
        md = "短文档 评价 100"
        assert _smart_window(md) == md

    def test_long_doc_keeps_tail_keywords(self):
        # 头部 4000 + 填充 + 尾部关键段（超出简单截断范围）
        filler = "无关填充行内容\n" * 400  # 约 4000+ 字符
        tail = "\nAbout this item\n* 超长续航电池卖点\n4.6 out of 5 stars\n"
        md = "HEAD 标题 价格 ¥199\n" + filler + tail + "尾部杂讯\n" * 50
        window = _smart_window(md, limit=8000)
        assert len(window) <= 8000
        assert "HEAD" in window
        assert "out of 5" in window  # 折叠区评分被保留
        assert "超长续航电池卖点" in window  # 卖点被保留


class TestParseLlmJson:
    def test_valid_json(self):
        data = _parse_llm_json('{"title": "iPhone", "price": 999}')
        assert data["title"] == "iPhone"
        assert data["price"] == 999

    def test_json_in_code_block(self):
        text = "```json\n{\"title\": \"iPhone\"}\n```"
        data = _parse_llm_json(text)
        assert data["title"] == "iPhone"

    def test_json_with_surrounding_text(self):
        text = "这是结果：{\"title\": \"iPhone\"} 希望对您有帮助"
        data = _parse_llm_json(text)
        assert data["title"] == "iPhone"

    def test_invalid_json_returns_none(self):
        assert _parse_llm_json("这不是 JSON") is None

    def test_empty_string_returns_none(self):
        assert _parse_llm_json("") is None

    def test_json_array_returns_none(self):
        assert _parse_llm_json("[1, 2, 3]") is None

    def test_malformed_json_returns_none(self):
        assert _parse_llm_json("{bad json}") is None


class TestNormalize:
    def test_basic_normalization(self):
        data = {
            "title": "iPhone 15 Pro Max",
            "price": 9999,
            "original_price": 10999,
            "currency": "CNY",
            "promo_text": "满减",
            "rating": 4.8,
            "review_count": 50000,
            "in_stock": True,
            "highlights": "钛金属,A17 Pro",
        }
        result = _normalize(data)
        assert result["title"] == "iPhone 15 Pro Max"
        assert result["price"] == 9999.0
        assert result["in_stock"] == 1

    def test_string_numbers_converted(self):
        data = {"price": "1,299.00", "review_count": "500"}
        result = _normalize(data)
        assert result["price"] == 1299.0
        assert result["review_count"] == 500

    def test_null_values(self):
        data = {"price": None, "original_price": "null", "review_count": ""}
        result = _normalize(data)
        assert result["price"] is None
        assert result["original_price"] is None
        assert result["review_count"] is None

    def test_bool_price_becomes_none(self):
        data = {"price": True}
        result = _normalize(data)
        assert result["price"] is None

    def test_title_truncated(self):
        data = {"title": "x" * 200}
        result = _normalize(data)
        assert len(result["title"]) == 120

    def test_in_stock_variants(self):
        assert _normalize({"in_stock": True})["in_stock"] == 1
        assert _normalize({"in_stock": 1})["in_stock"] == 1
        assert _normalize({"in_stock": "true"})["in_stock"] == 1
        assert _normalize({"in_stock": "有货"})["in_stock"] == 1
        assert _normalize({"in_stock": False})["in_stock"] == 0
        assert _normalize({"in_stock": "无货"})["in_stock"] == 0

    def test_currency_default_cny(self):
        result = _normalize({})
        assert result["currency"] == "CNY"

    def test_yen_in_price_stripped(self):
        data = {"price": "¥199.00"}
        result = _normalize(data)
        assert result["price"] == 199.0


class TestExtractFields:
    def _mock_llm_module(self, mock_llm):
        """Helper: 构造一个 mock backend.infra.llm 模块，避免触发代理对象初始化"""
        mod = ModuleType("backend.infra.llm")
        mod.llm = mock_llm
        return patch.dict(sys.modules, {"backend.infra.llm": mod})

    def test_without_llm_uses_rules(self):
        md = "# 商品标题足够长的文本内容\n¥299.00\n满减促销"
        result = extract_fields("jd", md, use_llm=False)
        assert result["extract_method"] == "regex"
        assert result["price"] == 299.0

    def test_with_llm_success(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=json.dumps({
            "title": "LLM 抽取的商品标题",
            "price": 199.0,
            "original_price": 299.0,
            "currency": "CNY",
            "promo_text": "满减50",
            "rating": 4.5,
            "review_count": 1000,
            "in_stock": True,
            "highlights": "卖点1,卖点2",
        }))
        md = "# 商品标题足够长的文本内容\n¥199"
        with self._mock_llm_module(mock_llm):
            result = extract_fields("jd", md, use_llm=True)
        assert result["extract_method"] == "llm"
        assert result["title"] == "LLM 抽取的商品标题"

    def test_llm_failure_falls_back_to_rules(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("LLM 调用失败")
        md = "# 商品标题足够长的文本内容\n¥199"
        with self._mock_llm_module(mock_llm):
            result = extract_fields("jd", md, use_llm=True)
        assert result["extract_method"] == "regex"
        assert result["price"] == 199.0

    def test_llm_no_price_rule_price_preserved(self):
        """LLM 没抽出价格但正则抽到了 → 用正则价格补"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=json.dumps({
            "title": "LLM 抽取的标题很长内容",
            "price": None,
            "promo_text": "促销",
            "in_stock": True,
        }))
        md = "# 标题内容文本\n¥599"
        with self._mock_llm_module(mock_llm):
            result = extract_fields("jd", md, use_llm=True)
        assert result["price"] == 599.0

    def test_llm_low_confidence_falls_back(self):
        """LLM 抽取可信度低（无价格无标题）→ 降级"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=json.dumps({
            "title": "",
            "price": None,
            "promo_text": "",
            "in_stock": False,
        }))
        md = "一些没有结构化信息的文本"
        with self._mock_llm_module(mock_llm):
            result = extract_fields("generic", md, use_llm=True)
        assert result["extract_method"] == "regex"

    def test_currency_from_rules_overrides_llm(self):
        """币种符号比 LLM 判断更可靠"""
        # use_llm=False 路径也能验证 rule_currency 逻辑
        md = "$199.00 USD price"
        result = extract_fields("amazon", md, use_llm=False)
        assert result["currency"] == "USD"


# ────────────────────────────────────────────────────────────────────────────
#  store.py 测试
# ────────────────────────────────────────────────────────────────────────────
from backend.competitor.store import CompetitorStore


@pytest.fixture
def store(tmp_path):
    """每个测试独立的临时数据库"""
    db_path = str(tmp_path / "test_competitor.db")
    return CompetitorStore(db_path=db_path)


class TestCompetitorStoreWatchlist:
    def test_add_watch(self, store):
        watch = store.add_watch("iPhone 15", "https://item.jd.com/123.html", platform="jd")
        assert watch["name"] == "iPhone 15"
        assert watch["url"] == "https://item.jd.com/123.html"
        assert watch["platform"] == "jd"
        assert watch["enabled"] == 1

    def test_add_watch_upsert(self, store):
        """URL 重复时更新名称"""
        store.add_watch("旧名称", "https://item.jd.com/123.html")
        updated = store.add_watch("新名称", "https://item.jd.com/123.html")
        assert updated["name"] == "新名称"
        # 只有一条记录
        all_items = store.list_watch(enabled_only=False)
        assert len(all_items) == 1

    def test_list_watch_enabled_only(self, store):
        store.add_watch("A", "https://a.com")
        store.add_watch("B", "https://b.com")
        # 手动停用 B
        with store._lock, store._connect() as conn:
            conn.execute("UPDATE competitor_watchlist SET enabled = 0 WHERE url = ?", ("https://b.com",))
        enabled = store.list_watch(enabled_only=True)
        all_items = store.list_watch(enabled_only=False)
        assert len(enabled) == 1
        assert len(all_items) == 2

    def test_get_watch_by_url(self, store):
        store.add_watch("Test", "https://test.com")
        found = store.get_watch_by_url("https://test.com")
        assert found is not None
        assert found["name"] == "Test"

    def test_get_watch_by_url_not_found(self, store):
        assert store.get_watch_by_url("https://nonexist.com") is None


class TestCompetitorStoreSnapshots:
    def test_save_snapshot(self, store):
        snap_id = store.save_snapshot({
            "url": "https://item.jd.com/123.html",
            "platform": "jd",
            "title": "iPhone",
            "price": 9999.0,
        })
        assert snap_id is not None
        assert snap_id > 0

    def test_save_snapshot_auto_crawled_at(self, store):
        """不传 crawled_at 时自动填充"""
        snap_id = store.save_snapshot({"url": "https://test.com"})
        snap = store.latest_snapshot("https://test.com")
        assert snap["crawled_at"] is not None

    def test_latest_snapshot(self, store):
        store.save_snapshot({"url": "https://test.com", "price": 100.0})
        store.save_snapshot({"url": "https://test.com", "price": 90.0})
        latest = store.latest_snapshot("https://test.com")
        assert latest["price"] == 90.0

    def test_latest_snapshot_before_id(self, store):
        id1 = store.save_snapshot({"url": "https://test.com", "price": 100.0})
        store.save_snapshot({"url": "https://test.com", "price": 90.0})
        prev = store.latest_snapshot("https://test.com", before_id=id1 + 1)
        assert prev["price"] == 100.0

    def test_latest_snapshot_not_found(self, store):
        assert store.latest_snapshot("https://nonexist.com") is None

    def test_history(self, store):
        for price in [100.0, 95.0, 90.0]:
            store.save_snapshot({"url": "https://test.com", "price": price})
        snaps = store.history("https://test.com", limit=10)
        assert len(snaps) == 3
        # 新→旧顺序
        assert snaps[0]["price"] == 90.0
        assert snaps[2]["price"] == 100.0

    def test_history_limit(self, store):
        for i in range(5):
            store.save_snapshot({"url": "https://test.com", "price": float(i)})
        snaps = store.history("https://test.com", limit=3)
        assert len(snaps) == 3

    def test_history_empty(self, store):
        assert store.history("https://nonexist.com") == []

    def test_snapshot_defaults(self, store):
        """测试快照字段默认值"""
        store.save_snapshot({"url": "https://test.com"})
        snap = store.latest_snapshot("https://test.com")
        assert snap["extract_method"] == "llm"
        assert snap["currency"] == "CNY"


# ────────────────────────────────────────────────────────────────────────────
#  pipeline.py 测试
# ────────────────────────────────────────────────────────────────────────────
from backend.competitor.pipeline import (
    _compare_with_previous,
    _fmt_price,
    _is_login_page,
    analyze_url,
    history_report,
    scan_watchlist,
)


class TestFmtPrice:
    def test_cny(self):
        assert _fmt_price(199.0, "CNY") == "¥199.00"

    def test_usd(self):
        assert _fmt_price(29.99, "USD") == "$29.99"

    def test_gbp(self):
        assert _fmt_price(99.0, "GBP") == "£99.00"

    def test_eur(self):
        assert _fmt_price(49.5, "EUR") == "€49.50"

    def test_none_returns_unknown(self):
        assert _fmt_price(None) == "未知"

    def test_unknown_currency(self):
        result = _fmt_price(100.0, "JPY")
        assert "100.00" in result

    def test_thousands_separator(self):
        assert _fmt_price(1234567.89, "CNY") == "¥1,234,567.89"


class TestCompareWithPrevious:
    @patch("backend.competitor.pipeline.get_store")
    def test_first_capture(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        snap_id = store.save_snapshot({"url": "https://test.com", "price": 199.0})
        # 第一次对比 (没有之前的快照)
        result = _compare_with_previous(snap_id, "https://test.com", 199.0)
        assert "首次" in result

    @patch("backend.competitor.pipeline.get_store")
    def test_price_drop(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.save_snapshot({"url": "https://test.com", "price": 299.0})
        snap_id2 = store.save_snapshot({"url": "https://test.com", "price": 199.0})
        result = _compare_with_previous(snap_id2, "https://test.com", 199.0)
        assert "降价" in result

    @patch("backend.competitor.pipeline.get_store")
    def test_price_rise(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.save_snapshot({"url": "https://test.com", "price": 199.0})
        snap_id2 = store.save_snapshot({"url": "https://test.com", "price": 299.0})
        result = _compare_with_previous(snap_id2, "https://test.com", 299.0)
        assert "涨价" in result

    @patch("backend.competitor.pipeline.get_store")
    def test_price_same(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.save_snapshot({"url": "https://test.com", "price": 199.0})
        snap_id2 = store.save_snapshot({"url": "https://test.com", "price": 199.0})
        result = _compare_with_previous(snap_id2, "https://test.com", 199.0)
        assert "持平" in result

    def test_no_price_returns_skip(self):
        result = _compare_with_previous(1, "https://test.com", None)
        assert "无法对比" in result


# ── analyze_url (需要 mock web_crawl_tool) ──────────────────────────────

MOCK_MARKDOWN = """# Apple iPhone 15 Pro Max 256GB 原色钛金属 全网通5G手机

价格 ¥9999.00 划线价 ¥10999.00

限时满减 满5000减200

50000条评价

有货
"""

# 模拟淘宝登录重定向页内容
LOGIN_PAGE_MARKDOWN = """[](javascript:void(0))
# [](https://www.taobao.com "淘宝网")
[密码登录](javascript:void(0);)[短信登录](javascript:void(0);)
忘记密码
[免费注册](https://reg.taobao.com/register.htm)
已阅读并同意以下协议
"""


class TestAnalyzeUrl:
    @patch("backend.competitor.pipeline.anti_ban")
    @patch("backend.competitor.pipeline.get_store")
    @patch("backend.tools.crawler_runtime.crawl")
    def test_analyze_success(self, mock_crawl, mock_get_store, mock_antiban, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_antiban.robots_allowed.return_value = True
        mock_crawl.return_value = {"ok": True, "content": MOCK_MARKDOWN, "error": ""}

        result = analyze_url("https://item.jd.com/123.html", use_llm=False)
        assert "竞品分析" in result
        assert "jd" in result
        assert "9,999.00" in result
        assert "快照" in result
        assert "regex" in result  # use_llm=False → regex
        mock_antiban.acquire.assert_called_once()      # 限流闸门被调用
        mock_antiban.report_success.assert_called_once()  # 成功反馈

    @patch("backend.competitor.pipeline.anti_ban")
    @patch("backend.competitor.pipeline.get_store")
    @patch("backend.tools.crawler_runtime.crawl")
    def test_analyze_crawl_failed(self, mock_crawl, mock_get_store, mock_antiban, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_antiban.robots_allowed.return_value = True
        mock_crawl.return_value = {"ok": False, "content": "", "error": "无法访问"}

        result = analyze_url("https://item.jd.com/123.html")
        assert "失败" in result
        assert "抓取失败" in result
        mock_antiban.report_failure.assert_called_once()  # 失败反馈（指数退避依据）

    @patch("backend.competitor.pipeline.anti_ban")
    @patch("backend.competitor.pipeline.get_store")
    @patch("backend.tools.crawler_runtime.crawl")
    def test_analyze_empty_content(self, mock_crawl, mock_get_store, mock_antiban, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_antiban.robots_allowed.return_value = True
        mock_crawl.return_value = {"ok": False, "content": "", "error": "页面无有效正文内容"}

        result = analyze_url("https://item.jd.com/123.html")
        assert "失败" in result

    @patch("backend.competitor.pipeline.anti_ban")
    @patch("backend.competitor.pipeline.get_store")
    @patch("backend.tools.crawler_runtime.crawl")
    def test_analyze_no_response(self, mock_crawl, mock_get_store, mock_antiban, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_antiban.robots_allowed.return_value = True
        mock_crawl.return_value = {"ok": False, "content": "", "error": "无响应"}

        result = analyze_url("https://item.jd.com/123.html")
        assert "失败" in result

    @patch("backend.competitor.pipeline.anti_ban")
    @patch("backend.competitor.pipeline.get_store")
    @patch("backend.tools.crawler_runtime.crawl")
    def test_analyze_saves_snapshot(self, mock_crawl, mock_get_store, mock_antiban, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_antiban.robots_allowed.return_value = True
        mock_crawl.return_value = {"ok": True, "content": MOCK_MARKDOWN, "error": ""}

        analyze_url("https://item.jd.com/123.html", use_llm=False)
        snaps = store.history("https://item.jd.com/123.html")
        assert len(snaps) == 1
        assert snaps[0]["price"] == 9999.0

    @patch("backend.competitor.pipeline.anti_ban")
    @patch("backend.competitor.pipeline.get_store")
    @patch("backend.tools.crawler_runtime.crawl")
    def test_analyze_with_name(self, mock_crawl, mock_get_store, mock_antiban, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_antiban.robots_allowed.return_value = True
        mock_crawl.return_value = {"ok": True, "content": MOCK_MARKDOWN, "error": ""}

        result = analyze_url("https://item.jd.com/123.html", name="我的竞品", use_llm=False)
        # name 作为备选标题（但规则抽取可能找到更好的标题）
        assert "竞品分析" in result

    @patch("backend.competitor.pipeline.anti_ban")
    @patch("backend.competitor.pipeline.get_store")
    @patch("backend.tools.crawler_runtime.crawl")
    def test_analyze_login_redirect(self, mock_crawl, mock_get_store, mock_antiban, tmp_path):
        """淘宝/天猫登录重定向页应返回清晰错误，保存 login_blocked 标记快照"""
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_antiban.robots_allowed.return_value = True
        mock_crawl.return_value = {"ok": True, "content": LOGIN_PAGE_MARKDOWN, "error": ""}

        result = analyze_url("https://item.taobao.com/item.htm?id=123", use_llm=False)
        assert "登录拦截" in result
        assert "Cookie 配置" in result or "CRAWLER_COOKIES" in result
        mock_antiban.report_login_redirect.assert_called_once()  # Cookie 疑似失效上报
        # 登录拦截应保存 login_blocked 标记快照
        snaps = store.history("https://item.taobao.com/item.htm?id=123")
        assert len(snaps) == 1
        assert snaps[0]["extract_method"] == "login_blocked"
        assert snaps[0]["price"] is None


class TestLoginPageDetection:
    def test_taobao_login_page(self):
        assert _is_login_page(LOGIN_PAGE_MARKDOWN) is True

    def test_normal_product_page(self):
        assert _is_login_page(MOCK_MARKDOWN) is False

    def test_empty_content(self):
        assert _is_login_page("") is False

    def test_single_keyword_not_triggered(self):
        # 只有一个关键词不应触发（避免误判含"请登录"提示的正常页面）
        content = "商品价格 ¥199.00 请登录后查看更多详情"
        assert _is_login_page(content) is False


class TestScanWatchlist:
    @patch("backend.competitor.pipeline.get_store")
    def test_empty_watchlist(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        result = scan_watchlist()
        assert "监控列表为空" in result

    @patch("backend.competitor.pipeline.anti_ban.is_cookie_suspect", return_value=False)
    @patch("backend.competitor.pipeline.analyze_url")
    @patch("backend.competitor.pipeline.get_store")
    def test_scan_items(self, mock_get_store, mock_analyze, mock_suspect, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.add_watch("竞品A", "https://a.com", platform="jd")
        store.add_watch("竞品B", "https://b.com", platform="tmall")
        mock_analyze.return_value = "## 竞品分析: 结果"

        result = scan_watchlist()
        assert "竞品巡检" in result
        assert "2 项" in result
        assert mock_analyze.call_count == 2

    @patch("backend.competitor.pipeline.anti_ban.is_cookie_suspect", return_value=False)
    @patch("backend.competitor.pipeline.analyze_url")
    @patch("backend.competitor.pipeline.get_store")
    def test_scan_halts_on_global_halt(self, mock_get_store, mock_analyze, mock_suspect, tmp_path):
        """全局停采（L2）时应立即终止本轮巡检"""
        from backend.competitor import anti_ban
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.add_watch("竞品A", "https://a.com", platform="jd")
        store.add_watch("竞品B", "https://b.com", platform="jd")
        mock_analyze.side_effect = anti_ban.GlobalHaltError("全局停采中")

        result = scan_watchlist()
        assert "巡检终止" in result
        assert mock_analyze.call_count == 1  # 第一项触发停采后不再继续

    @patch("backend.competitor.pipeline.anti_ban.is_cookie_suspect", return_value=False)
    @patch("backend.competitor.pipeline.analyze_url")
    @patch("backend.competitor.pipeline.get_store")
    def test_scan_skips_on_platform_stop(self, mock_get_store, mock_analyze, mock_suspect, tmp_path):
        """平台停采（L1）时跳过该项但继续其他项"""
        from backend.competitor import anti_ban
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.add_watch("竞品A", "https://a.com", platform="jd")
        store.add_watch("竞品B", "https://b.com", platform="jd")
        mock_analyze.side_effect = [
            anti_ban.PlatformStoppedError("jd 今日已停采"),
            "## 竞品分析: 结果",
        ]

        result = scan_watchlist()
        assert "防封闸门跳过" in result
        assert "被防封策略跳过" in result
        assert mock_analyze.call_count == 2


class TestHistoryReport:
    @patch("backend.competitor.pipeline.get_store")
    def test_no_history(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        result = history_report("https://nonexist.com")
        assert "暂无抓取历史" in result

    @patch("backend.competitor.pipeline.get_store")
    def test_with_history(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.save_snapshot({"url": "https://test.com", "price": 100.0, "currency": "CNY"})
        store.save_snapshot({"url": "https://test.com", "price": 95.0, "currency": "CNY"})
        store.save_snapshot({"url": "https://test.com", "price": 90.0, "currency": "CNY"})

        result = history_report("https://test.com")
        assert "价格历史" in result
        assert "¥100.00" in result
        assert "¥90.00" in result
        assert "区间" in result

    @patch("backend.competitor.pipeline.get_store")
    def test_single_snapshot_no_range(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.save_snapshot({"url": "https://test.com", "price": 100.0})

        result = history_report("https://test.com")
        assert "价格历史" in result
        # 单条记录无区间
        assert "区间" not in result


# ────────────────────────────────────────────────────────────────────────────
#  tools/competitor.py 测试
# ────────────────────────────────────────────────────────────────────────────
from backend.tools.competitor import (
    _extract_url,
    _format_watchlist,
    competitor_analyze_tool,
)


class TestExtractUrl:
    def test_simple_url(self):
        assert _extract_url("帮我分析 https://item.jd.com/123.html 这个竞品") == "https://item.jd.com/123.html"

    def test_url_with_trailing_punctuation(self):
        assert _extract_url("看看 https://item.jd.com/123.html。") == "https://item.jd.com/123.html"

    def test_no_url(self):
        assert _extract_url("没有链接的纯文本") == ""

    def test_empty_input(self):
        assert _extract_url("") == ""
        assert _extract_url(None) == ""

    def test_chinese_parentheses(self):
        url = _extract_url("分析这个（https://item.jd.com/123.html）")
        assert url == "https://item.jd.com/123.html"


class TestFormatWatchlist:
    @patch("backend.tools.competitor.get_store")
    def test_empty(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        result = _format_watchlist()
        assert "监控列表为空" in result

    @patch("backend.tools.competitor.get_store")
    def test_with_items(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.add_watch("竞品A", "https://a.com", platform="jd")
        store.add_watch("竞品B", "https://b.com", platform="tmall")
        result = _format_watchlist()
        assert "竞品监控列表" in result
        assert "竞品A" in result
        assert "竞品B" in result
        assert "jd" in result


class TestCompetitorAnalyzeTool:
    @patch("backend.tools.competitor.analyze_url")
    def test_action_analyze(self, mock_analyze):
        mock_analyze.return_value = "## 竞品分析: 结果"
        result = competitor_analyze_tool.invoke({"action": "analyze", "url": "https://test.com"})
        assert "竞品分析" in result
        mock_analyze.assert_called_once_with("https://test.com", name="")

    def test_action_analyze_no_url(self):
        result = competitor_analyze_tool.invoke({"action": "analyze", "url": ""})
        assert "请提供" in result

    @patch("backend.tools.competitor.scan_watchlist")
    def test_action_watch(self, mock_scan):
        mock_scan.return_value = "## 竞品巡检"
        result = competitor_analyze_tool.invoke({"action": "watch"})
        assert "巡检" in result

    @patch("backend.tools.competitor.history_report")
    def test_action_history(self, mock_history):
        mock_history.return_value = "## 价格历史"
        result = competitor_analyze_tool.invoke({"action": "history", "url": "https://test.com"})
        assert "价格历史" in result

    def test_action_history_no_url(self):
        result = competitor_analyze_tool.invoke({"action": "history", "url": ""})
        assert "请提供" in result

    @patch("backend.tools.competitor.analyze_url")
    @patch("backend.tools.competitor.get_store")
    def test_action_add(self, mock_get_store, mock_analyze, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_analyze.return_value = "## 竞品分析: 首次抓取"
        result = competitor_analyze_tool.invoke({
            "action": "add", "url": "https://item.jd.com/123.html", "name": "我的竞品"
        })
        assert "已加入监控" in result
        # 验证 watchlist 已创建
        watch = store.get_watch_by_url("https://item.jd.com/123.html")
        assert watch is not None
        assert watch["name"] == "我的竞品"

    def test_action_add_no_url(self):
        result = competitor_analyze_tool.invoke({"action": "add", "url": ""})
        assert "请提供" in result

    @patch("backend.tools.competitor._format_watchlist")
    def test_action_list(self, mock_format):
        mock_format.return_value = "## 竞品监控列表"
        result = competitor_analyze_tool.invoke({"action": "list"})
        assert "监控列表" in result

    def test_unknown_action(self):
        result = competitor_analyze_tool.invoke({"action": "unknown_action"})
        assert "未知 action" in result

    @patch("backend.tools.competitor.analyze_url")
    def test_url_extracted_from_question(self, mock_analyze):
        mock_analyze.return_value = "分析结果"
        result = competitor_analyze_tool.invoke({
            "action": "analyze",
            "url": "",
            "question": "帮我分析 https://item.jd.com/456.html 这个竞品",
        })
        mock_analyze.assert_called_once()
        call_args = mock_analyze.call_args
        assert "item.jd.com/456.html" in call_args[0][0]

    @patch("backend.tools.competitor.analyze_url")
    def test_exception_returns_failed(self, mock_analyze):
        mock_analyze.side_effect = RuntimeError("unexpected error")
        result = competitor_analyze_tool.invoke({"action": "analyze", "url": "https://test.com"})
        assert "COMPETITOR FAILED" in result


# ────────────────────────────────────────────────────────────────────────────
#  修复验证测试 (Phase 2)
# ────────────────────────────────────────────────────────────────────────────


class TestBugFixes:
    """验证已修复的 Bug"""

    # ── B1: _extract_review_count 支持 `5万+条评价` ──

    def test_review_count_wan_plus_tiao(self):
        """修复: `5万+条评价` 现在能正确匹配并换算为 50000"""
        assert _extract_review_count("5万+条评价") == 50000

    def test_review_count_wan_pingjia(self):
        """`5万+评价` 换算为 50000"""
        assert _extract_review_count("5万+评价") == 50000

    def test_review_count_tiao_only(self):
        """`10000条评价` 仍然正常"""
        assert _extract_review_count("10000条评价") == 10000

    # ── B2: detect_platform 不再误匹配 amazon.community ──

    def test_amazon_community_is_generic(self):
        """修复: amazon.community 不应被识别为 amazon"""
        assert detect_platform("https://www.amazon.community/post/123") == "generic"

    def test_amazon_company_is_generic(self):
        """修复: amazon.company 不应被识别为 amazon"""
        assert detect_platform("https://www.amazon.company/about") == "generic"

    def test_amazon_com_still_works(self):
        """amazon.com 仍然正常"""
        assert detect_platform("https://www.amazon.com/dp/B0EXAMPLE") == "amazon"

    def test_amazon_com_with_path(self):
        """amazon.com/xxx 仍然正常"""
        assert detect_platform("https://www.amazon.com/gp/product/123") == "amazon"

    # ── B3: _extract_promo 不再误匹配 office ──

    def test_promo_office_not_matched(self):
        """修复: 'office' 不再触发促销关键词"""
        assert _extract_promo("Microsoft Office 365 subscription") == ""

    def test_promo_offer_not_matched(self):
        """修复: 'offer' 不再触发促销关键词"""
        assert _extract_promo("Special offer for new employees") == ""

    def test_promo_sale_still_matched(self):
        """'sale' 仍然被识别"""
        assert _extract_promo("Big sale today 50% discount") != ""


# ────────────────────────────────────────────────────────────────────────────
#  新功能测试 (Phase 2 优化)
# ────────────────────────────────────────────────────────────────────────────


class TestStoreNewFeatures:
    """store.py 新功能测试"""

    def test_remove_watch(self, store):
        store.add_watch("A", "https://a.com")
        assert store.remove_watch("https://a.com") is True
        assert store.get_watch_by_url("https://a.com") is None

    def test_remove_watch_not_found(self, store):
        assert store.remove_watch("https://nonexist.com") is False

    def test_toggle_watch_disable(self, store):
        store.add_watch("A", "https://a.com")
        result = store.toggle_watch("https://a.com", enabled=False)
        assert result["enabled"] == 0

    def test_toggle_watch_enable(self, store):
        store.add_watch("A", "https://a.com")
        store.toggle_watch("https://a.com", enabled=False)
        result = store.toggle_watch("https://a.com", enabled=True)
        assert result["enabled"] == 1

    def test_toggle_watch_not_found(self, store):
        assert store.toggle_watch("https://nonexist.com", enabled=True) is None

    def test_add_watch_upsert_updates_frequency(self, store):
        """修复: upsert 现在更新 frequency 字段"""
        store.add_watch("A", "https://a.com", frequency="daily")
        updated = store.add_watch("A", "https://a.com", frequency="weekly")
        assert updated["frequency"] == "weekly"


class TestResetStore:
    """reset_store() 测试"""

    def test_reset_store(self):
        from backend.competitor.store import get_store, reset_store
        # 确保单例已创建
        s1 = get_store()
        assert s1 is not None
        # 重置后再次获取应是新实例
        reset_store()
        s2 = get_store()
        # 两个实例不是同一个对象（重置成功）
        assert s1 is not s2
        reset_store()  # 清理


class TestScanWatchlistErrorIsolation:
    """B5: 巡检单项失败不影响其他项"""

    @patch("backend.competitor.pipeline.analyze_url")
    @patch("backend.competitor.pipeline.get_store")
    def test_one_failure_continues(self, mock_get_store, mock_analyze, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.add_watch("A", "https://a.com")
        store.add_watch("B", "https://b.com")

        # A 失败，B 成功
        mock_analyze.side_effect = [
            RuntimeError("network error"),
            "## 竞品分析: B 成功",
        ]
        result = scan_watchlist()
        assert "❌ A" in result or "A" in result
        assert "B 成功" in result
        assert "1 项巡检失败" in result


class TestHistoryReportTrend:
    """O3: history_report 趋势摘要"""

    @patch("backend.competitor.pipeline.get_store")
    def test_trend_price_drop(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.save_snapshot({"url": "https://test.com", "price": 100.0, "currency": "CNY"})
        store.save_snapshot({"url": "https://test.com", "price": 80.0, "currency": "CNY"})
        result = history_report("https://test.com")
        assert "趋势" in result
        assert "降价" in result

    @patch("backend.competitor.pipeline.get_store")
    def test_trend_price_rise(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.save_snapshot({"url": "https://test.com", "price": 80.0, "currency": "CNY"})
        store.save_snapshot({"url": "https://test.com", "price": 100.0, "currency": "CNY"})
        result = history_report("https://test.com")
        assert "趋势" in result
        assert "涨价" in result

    @patch("backend.competitor.pipeline.get_store")
    def test_trend_stable(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.save_snapshot({"url": "https://test.com", "price": 100.0, "currency": "CNY"})
        store.save_snapshot({"url": "https://test.com", "price": 100.0, "currency": "CNY"})
        result = history_report("https://test.com")
        assert "趋势" in result
        assert "平稳" in result


class TestAnalyzeUrlQualityGate:
    """O4: 抽取质量低时跳过快照入库"""

    @patch("backend.competitor.pipeline.anti_ban")
    @patch("backend.competitor.pipeline.get_store")
    @patch("backend.tools.crawler_runtime.crawl")
    def test_low_quality_skips_snapshot(self, mock_crawl, mock_get_store, mock_antiban, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        mock_antiban.robots_allowed.return_value = True
        # 全部短行（<8字符），无价格，无标题 → 抽取质量低
        mock_crawl.return_value = {"ok": True, "content": "短文本\n无信息\n测试", "error": ""}
        result = analyze_url("https://example.com/random", use_llm=False)
        # 不应存入快照
        snaps = store.history("https://example.com/random")
        assert len(snaps) == 0
        assert "抽取质量低" in result or "未存入快照" in result


class TestToolNewActions:
    """O2: tool 新 action (remove / toggle)"""

    @patch("backend.tools.competitor.get_store")
    def test_action_remove_success(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.add_watch("A", "https://a.com")
        result = competitor_analyze_tool.invoke({"action": "remove", "url": "https://a.com"})
        assert "已" in result and "移除" in result
        assert store.get_watch_by_url("https://a.com") is None

    @patch("backend.tools.competitor.get_store")
    def test_action_remove_not_found(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        result = competitor_analyze_tool.invoke({"action": "remove", "url": "https://nonexist.com"})
        assert "未找到" in result

    def test_action_remove_no_url(self):
        result = competitor_analyze_tool.invoke({"action": "remove", "url": ""})
        assert "请提供" in result

    @patch("backend.tools.competitor.get_store")
    def test_action_toggle_disable(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.add_watch("A", "https://a.com")
        result = competitor_analyze_tool.invoke({
            "action": "toggle", "url": "https://a.com", "enabled": False
        })
        assert "停用" in result

    @patch("backend.tools.competitor.get_store")
    def test_action_toggle_enable(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        store.add_watch("A", "https://a.com")
        store.toggle_watch("https://a.com", enabled=False)
        result = competitor_analyze_tool.invoke({
            "action": "toggle", "url": "https://a.com", "enabled": True
        })
        assert "启用" in result

    def test_action_toggle_no_url(self):
        result = competitor_analyze_tool.invoke({"action": "toggle", "url": ""})
        assert "请提供" in result

    @patch("backend.tools.competitor.get_store")
    def test_action_toggle_not_found(self, mock_get_store, tmp_path):
        store = CompetitorStore(db_path=str(tmp_path / "cmp.db"))
        mock_get_store.return_value = store
        result = competitor_analyze_tool.invoke({
            "action": "toggle", "url": "https://nonexist.com"
        })
        assert "未找到" in result


# ────────────────────────────────────────────────────────────────────────────
#  crypto.py 测试
# ────────────────────────────────────────────────────────────────────────────


class TestCrypto:
    def test_plain_text_when_no_key(self):
        """未设置 COOKIE_ENCRYPTION_KEY 时，明文存储"""
        original = "tb_token=abc123; cna=xyz"
        encrypted = encrypt_cookie(original)
        assert encrypted == original  # 无密钥时不加密

    def test_decrypt_plain_text(self):
        """解密明文（非 enc: 前缀）返回原文"""
        plain = "tb_token=abc123"
        assert decrypt_cookie(plain) == plain
        assert decrypt_cookie("") == ""
        assert decrypt_cookie(None) is None

    def test_maybe_encrypt_decrypt_cookie_key(self):
        """crawler_cookies key 自动加解密"""
        original = "session_id=xyz; token=abc"
        encrypted = maybe_encrypt("crawler_cookies", original)
        decrypted = maybe_decrypt("crawler_cookies", encrypted)
        assert decrypted == original

    def test_maybe_encrypt_decrypt_platform_cookie_key(self):
        """多平台分键 crawler_cookies:<platform> 同样自动加解密（回环一致）"""
        from backend.competitor.crypto import _is_encrypted_key

        # 分键被识别为敏感键，而 meta 键不会
        assert _is_encrypted_key("crawler_cookies")
        assert _is_encrypted_key("crawler_cookies:douyin")
        assert not _is_encrypted_key("crawler_cookies_meta:douyin")

        original = "unb=123; pt_key=abc"
        encrypted = maybe_encrypt("crawler_cookies:douyin", original)
        decrypted = maybe_decrypt("crawler_cookies:douyin", encrypted)
        assert decrypted == original

    def test_maybe_encrypt_non_cookie_key(self):
        """非 cookie key 不加密"""
        original = "some_value"
        assert maybe_encrypt("other_key", original) == original
        assert maybe_decrypt("other_key", original) == original

    def test_encrypted_roundtrip_with_key(self, monkeypatch):
        """设置密钥后加解密 roundtrip"""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("COOKIE_ENCRYPTION_KEY", key)
        # 重置全局缓存
        import backend.competitor.crypto as crypto_mod
        crypto_mod._fernet = None

        original = "tb_token=abc123; cna=xyz"
        encrypted = encrypt_cookie(original)
        assert encrypted != original
        assert encrypted.startswith("enc:")
        assert decrypt_cookie(encrypted) == original

    def test_maybe_decrypt_none(self):
        """maybe_decrypt(None) 返回 None"""
        assert maybe_decrypt("crawler_cookies", None) is None


# ────────────────────────────────────────────────────────────────────────────
#  qr_login.py 测试
# ────────────────────────────────────────────────────────────────────────────


class TestQrLogin:
    """QR 登录测试（Playwright 浏览器方案）

    新架构使用 _qr_sessions 全局会话表存储浏览器状态，
    轮询时检查 URL 变化 / 新增 Cookie / 页面文本。
    """

    def test_supported_platforms(self):
        platforms = get_supported_platforms()
        assert "taobao" in platforms
        assert "jd" in platforms
        assert "tmall" in platforms  # 天猫复用淘宝
        assert "douyin" in platforms  # 抖音弹窗扫码

    def test_douyin_config_complete(self):
        """抖音配置必须包含所有必需键（弹窗登录 + 异步 QR 渲染）"""
        from backend.competitor.qr_login import _PLATFORM_CONFIG
        cfg = _PLATFORM_CONFIG["douyin"]
        assert cfg["qr_selector"] == "#animate_qrcode_container img"
        assert cfg["pre_click_selector"]  # 需先点登录按钮
        assert cfg["login_domain"] is None  # 弹窗不跳转
        assert cfg.get("qr_timeout", 10000) >= 20000  # 异步渲染需长超时

    def test_unsupported_platform_raises(self):
        import asyncio
        with pytest.raises(ValueError, match="不支持的平台"):
            asyncio.run(start_qr_login("amazon"))

    def test_poll_expired_no_session(self):
        """不存在的 token 返回 expired"""
        import asyncio

        # 确保 _qr_sessions 中没有这个 token
        from backend.competitor.qr_login import _qr_sessions
        _qr_sessions.pop("nonexistent_token", None)

        result = asyncio.run(
            poll_qr_login("taobao", "nonexistent_token", "")
        )
        assert result["status"] == "expired"
        assert "saved" not in result

    def test_poll_new_status_no_save(self):
        """NEW 状态：URL 未变、无新 Cookie、QR 码仍可见"""
        import asyncio
        import time
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.competitor.qr_login import _qr_sessions, _PLATFORM_CONFIG

        mock_page = MagicMock()
        mock_page.url = "https://login.taobao.com/member/login.jhtml"

        mock_context = MagicMock()
        # 初始 Cookie 和当前 Cookie 相同（无新增）
        mock_context.cookies = AsyncMock(return_value=[
            {"name": "cna", "value": "abc"},
            {"name": "t", "value": "123"},
        ])

        mock_frame = MagicMock()
        mock_frame.evaluate = AsyncMock(return_value="手机扫码登录 打开淘宝APP")
        mock_frame.query_selector = AsyncMock(return_value=MagicMock())  # QR 仍可见

        token = "test_new_status"
        _qr_sessions[token] = {
            "page": mock_page,
            "browser": MagicMock(),
            "context": mock_context,
            "pw": MagicMock(),
            "platform": "taobao",
            "config": _PLATFORM_CONFIG["taobao"],
            "qr_frame": mock_frame,
            "created_at": time.time(),
            "initial_url": "https://login.taobao.com/member/login.jhtml",
            "initial_cookie_names": {"cna", "t"},
        }

        try:
            result = asyncio.run(
                poll_qr_login("taobao", token, "")
            )
        finally:
            _qr_sessions.pop(token, None)

        assert result["status"] == "new"
        assert "saved" not in result

    def test_poll_confirmed_saves_cookies(self, tmp_path):
        """CONFIRMED：URL 变化后自动提取 Cookie 并入库"""
        import asyncio
        import time
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.competitor.store import CompetitorStore, reset_store
        from backend.competitor.qr_login import _qr_sessions, _PLATFORM_CONFIG

        store = CompetitorStore(db_path=str(tmp_path / "qr.db"))

        mock_page = MagicMock()
        # URL 已从登录页跳转 = 登录成功
        mock_page.url = "https://www.taobao.com/"

        mock_context = MagicMock()
        mock_context.cookies = AsyncMock(return_value=[
            {"name": "cna", "value": "abc"},
            {"name": "unb", "value": "123456"},
            {"name": "_tb_token_", "value": "xyz"},
        ])

        mock_browser = MagicMock()
        mock_browser.close = AsyncMock()
        mock_pw = MagicMock()
        mock_pw.stop = AsyncMock()

        token = "test_confirmed"
        _qr_sessions[token] = {
            "page": mock_page,
            "browser": mock_browser,
            "context": mock_context,
            "pw": mock_pw,
            "platform": "taobao",
            "config": _PLATFORM_CONFIG["taobao"],
            "qr_frame": MagicMock(),
            "created_at": time.time(),
            "initial_url": "https://login.taobao.com/member/login.jhtml",
            "initial_cookie_names": {"cna"},
        }

        try:
            with patch("backend.competitor.cookie_manager.get_store", return_value=store):
                result = asyncio.run(
                    poll_qr_login("taobao", token, "")
                )
        finally:
            _qr_sessions.pop(token, None)

        assert result["status"] == "confirmed"
        assert result["saved"] is True
        assert result["cookie_length"] > 0

        # 验证 Cookie 已按平台入库（taobao 分键 + source=qr 元数据）
        saved = store.get_config("crawler_cookies:taobao")
        assert saved is not None
        assert "unb=123456" in saved
        import json as _json
        meta = _json.loads(store.get_config("crawler_cookies_meta:taobao") or "{}")
        assert meta.get("source") == "qr"
        reset_store()

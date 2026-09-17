"""tests/travel/test_slot_filler.py — 槽位抽取边界

重点在「不猜」：
  - 没有货币单位的数字不是预算（"3天" 不是三千块）
  - 中文数字与阿拉伯数字等价
  - 负向提及的地点不得进必去清单
  - 缺失必填槽位时产出追问而非默认值
"""
from __future__ import annotations

from datetime import date

from backend.travel.models.brief import TravelBrief
from backend.travel.slot_filler import (
    build_clarification,
    extract_avoid,
    extract_brief,
    extract_budget,
    extract_days,
    extract_days_range,
    extract_destination,
    extract_must_go,
    extract_pace,
    extract_party_size,
    extract_preferences,
    extract_start_date,
    extract_unsupported_city,
    merge_brief,
    party_size_source,
    slot_filler_node,
)


class TestScalarExtraction:
    def test_days_arabic(self):
        assert extract_days("福州玩3天") == 3

    def test_days_chinese_numeral(self):
        assert extract_days("想玩五天") == 5

    def test_days_compound_cn_numeral(self):
        """复合中文数字：「十二天」是 12，不能被单字符匹配劈成 2。"""
        assert extract_days("福州玩十二天") == 12
        assert extract_days("计划二十天") == 20
        assert extract_days("二十五日游") == 25
        assert extract_days("就玩十天") == 10

    def test_date_cn_not_misparsed_as_days(self):
        """「9月21日」的「21日」是日期不是 21 天（评测 T-E02 实测踩过：
        「9月21日福州一日游」抽出 days=21，整条行程直接排成 21 天）。"""
        assert extract_days("9月21日福州一日游，1个人，必去福建博物院") == 1
        assert extract_days("12月3日出发，玩3日") == 3
        assert extract_days("4月5日去厦门，安排1天") == 1

    def test_date_range_shorthand_not_misparsed_as_days(self):
        """省写日期区间「9月21到25日」必须整体保护，残留「25日」也不许误读。"""
        assert extract_days("9月21到25日去福州玩2天") == 2
        assert extract_days_range("9月21到25日玩吧") is None

    def test_plain_days_unaffected_by_date_guard(self):
        """日期保护不能误伤真正的天数表达。"""
        assert extract_days("玩21天") == 21
        assert extract_days("福州2天行程") == 2
        assert extract_days_range("福州玩个两三天") == (2, 3, "两三天")

    def test_party_size(self):
        assert extract_party_size("我们3个人去") == 3

    def test_party_size_compound_cn_numeral(self):
        assert extract_party_size("我们二十五个人去") == 25

    def test_party_family_kou(self):
        """「一家三口」是明确的人数事实。"""
        assert extract_party_size("一家三口去福州玩三天") == 3
        assert extract_party_size("三口之家想去厦门") == 3

    def test_party_companion_phrases(self):
        """同伴表达的无数字兜底：带爸妈 +2、和女朋友 +1。"""
        assert extract_party_size("带爸妈去福州玩") == 3
        assert extract_party_size("和女朋友去厦门") == 2
        assert extract_party_size("跟朋友一起玩") == 2

    def test_party_numeric_wins_over_companion(self):
        assert extract_party_size("两个人带着孩子") == 2

    def test_budget_requires_currency_unit(self):
        """「3天」不能被当成 3000 元预算。"""
        assert extract_budget("福州玩3天") is None

    def test_budget_yuan(self):
        assert extract_budget("预算 5000 元") == 5000.0

    def test_budget_wan(self):
        assert extract_budget("预算大概1.5万") == 15000.0

    def test_start_date_cn(self):
        got = extract_start_date("10月1日出发", today=date(2026, 9, 14))
        assert got == date(2026, 10, 1)

    def test_start_date_rolls_to_next_year_when_past(self):
        got = extract_start_date("3月1日出发", today=date(2026, 9, 14))
        assert got == date(2027, 3, 1)

    def test_start_date_iso(self):
        assert extract_start_date("2026-10-01 出发") == date(2026, 10, 1)

    def test_no_date_returns_none(self):
        assert extract_start_date("福州玩3天", today=date(2026, 9, 14)) is None

    def test_preferences(self):
        got = extract_preferences("喜欢人文和美食，顺便拍拍照")
        assert "人文" in got and "美食" in got and "摄影" in got

    def test_pace_relaxed(self):
        assert extract_pace("轻松一点，别太赶") == "relaxed"

    def test_destination_from_catalog(self):
        assert extract_destination("想去厦门") == "厦门"

    def test_destination_alias(self):
        assert extract_destination("去鹭岛转转") == "厦门"

    def test_unknown_destination_returns_empty(self):
        assert extract_destination("想去火星") == ""


class TestMustGoAndAvoid:
    def test_must_go_from_catalog_match(self):
        got = extract_must_go("一定要去三坊七巷", "福州")
        assert "三坊七巷" in got

    def test_must_go_from_trigger_words(self):
        got = extract_must_go("想去西湖和灵隐寺", "杭州")
        assert "西湖" in got

    def test_avoid_excludes_catalog_name(self):
        assert "河坊街" in extract_avoid("别去河坊街")

    def test_negated_poi_not_in_must_go(self):
        """核心边界：名录匹配会让「别去河坊街」命中河坊街，
        必须靠 avoid 过滤掉，否则会产生「必去地点未能排入」的假警告。"""
        brief = extract_brief("杭州2天行程，别去河坊街")
        assert "河坊街" in brief.avoid
        assert "河坊街" not in brief.must_go

    def test_destination_not_in_must_go(self):
        brief = extract_brief("福州3天，一定要去三坊七巷")
        assert "福州" not in brief.must_go
        assert "三坊七巷" in brief.must_go

    def test_must_go_with_connector_particles(self):
        """触发词与地名之间的连接词不得吞进地名。

        实测 bug："再加一个必去的：烟台山" 产出 must_go 含 "的：烟台山"
        并直出行程单。名录命中会给 "烟台山"，触发词捕获必须给出同一个
        名字才能去重，而不是并排出 "的：烟台山"。
        """
        got = extract_must_go("再加一个必去的：烟台山", "福州")
        assert got == ["烟台山"]
        assert "的：烟台山" not in got

        got2 = extract_must_go("想去的是鼓山", "福州")
        assert "鼓山" in got2
        assert "是鼓山" not in got2

        got3 = extract_avoid("避开的是河坊街")
        assert "河坊街" in got3
        assert "是河坊街" not in got3

    def test_captured_noise_dropped(self):
        """触发词捕获的半截话不得进清单（实测直出过行程单的脏条目）。"""
        # 「我想去福州玩」—— 想去 捕获「福州玩」
        assert "福州玩" not in extract_must_go("我想去福州玩", "福州")
        # 「想去的地方很多」—— 捕获「地方很多」
        brief = extract_brief("想去的地方很多，比如三坊七巷")
        assert "地方很多" not in brief.must_go
        assert "三坊七巷" in brief.must_go
        # 「不要去人多拥挤的地方」—— 非地名描述不进避雷清单
        assert extract_avoid("不要去人多拥挤的地方") == []
        # 「想去福州玩两天」—— 日期天数不能被吞进地名
        assert "玩两天" not in extract_must_go("我想去福州玩两天", "福州")
        assert "两天" not in extract_must_go("我想去福州玩两天", "福州")


class TestMergeBrief:
    def test_party_persists_when_unmentioned(self):
        prev = extract_brief("福州两天一家三口")
        merged = extract_brief("节奏改紧凑一点", previous=prev)
        assert merged.party_size == 3
        assert merged.pace == "intense"

    def test_avoid_removes_previous_must_go(self):
        """上一轮必去被这一轮拉黑后必须移出必去清单（avoid 优先）。"""
        prev = extract_brief("福州两天必去三坊七巷和鼓山")
        merged = extract_brief("不想去鼓山了", previous=prev)
        assert "鼓山" in merged.avoid
        assert "鼓山" not in merged.must_go
        assert "三坊七巷" in merged.must_go

    def test_destination_switch(self):
        prev = extract_brief("厦门两天")
        merged = extract_brief("换成福州，三天", previous=prev)
        assert merged.destination == "福州"
        assert merged.days == 3


class TestAmbiguityTransparency:
    """猜测与区间说法不拦流程，但必须产生用户可见的提示。"""

    def test_days_range_detection(self):
        assert extract_days_range("玩个两三天") == (2, 3, "两三天")
        assert extract_days_range("安排3-5天") == (3, 5, "3-5天")
        assert extract_days_range("两到三天") is not None
        # 复合数字与单数字不是区间
        assert extract_days_range("福州玩十二天") is None
        assert extract_days_range("玩两天") is None

    def test_range_days_note_emitted(self):
        update = slot_filler_node({"user_message": "玩个两三天吧，去福州，两个人"})
        assert any("区间" in n and "3 天" in n for n in update["notes"])

    def test_party_guess_note_emitted(self):
        update = slot_filler_node({"user_message": "带爸妈去福州玩两天"})
        assert any("3 人估算" in n for n in update["notes"])

    def test_explicit_party_no_note(self):
        update = slot_filler_node({"user_message": "福州两天，3个人"})
        assert not any("估算" in n for n in update["notes"])

    def test_party_size_source(self):
        assert party_size_source("我们3个人去") == "explicit"
        assert party_size_source("一家三口去玩") == "explicit"
        assert party_size_source("带爸妈去玩") == "guess"
        assert party_size_source("福州两天") == "none"


class TestUnsupportedCity:
    def test_known_unsupported_city_detected(self):
        assert extract_unsupported_city("我想去北京玩") == "北京"
        assert extract_unsupported_city("去泉州逛逛") == "泉州"
        # 已支持城市不报
        assert extract_unsupported_city("福州两天") == ""
        assert extract_unsupported_city("杭州两日游") == ""
        # 非城市词不报
        assert extract_unsupported_city("去趟乐园") == ""

    def test_clarification_mentions_unsupported(self):
        text = build_clarification(TravelBrief(), "我想去北京玩")
        assert "北京" in text and "暂时无法规划" in text
        assert "福州" in text  # 仍给出可规划城市

    def test_clarification_generic_without_city(self):
        text = build_clarification(TravelBrief(), "")
        assert "暂时无法规划" not in text

    def test_city_not_in_must_go(self):
        """城市名是目的地槽位的事，不是必去 POI——进了清单必然假警告。"""
        brief = extract_brief("我想去北京玩")
        assert brief.must_go == []
        brief2 = extract_brief("杭州2天，必去三坊七巷")
        assert brief2.must_go == ["三坊七巷"]

    def test_merge_drops_historical_city_in_must_go(self):
        """历史脏状态（旧版本把城市写进了 must_go）也要在 merge 时清掉。"""
        prev = TravelBrief(destination="", must_go=["北京"], days=3)
        merged = merge_brief(prev, extract_brief("福州三天"))
        assert "北京" not in merged.must_go
        assert merged.destination == "福州"


class TestBriefAndClarification:
    def test_full_extraction(self):
        brief = extract_brief(
            "帮我规划福州3天行程，2个人，喜欢人文和摄影，"
            "一定要去三坊七巷和鼓山，预算3000元"
        )
        assert brief.destination == "福州"
        assert brief.days == 3
        assert brief.party_size == 2
        assert brief.budget_cny == 3000.0
        assert brief.is_ready() is True
        assert "三坊七巷" in brief.must_go and "鼓山" in brief.must_go

    def test_missing_slots_when_destination_absent(self):
        brief = extract_brief("我想玩3天")
        assert brief.missing_slots() == ["destination"]
        assert brief.is_ready() is False

    def test_clarification_lists_each_missing_slot(self):
        text = build_clarification(TravelBrief())
        assert "去哪个城市" in text
        assert "玩几天" in text

    def test_no_clarification_when_ready(self):
        assert build_clarification(
            TravelBrief(destination="福州", days=2)) == ""

    def test_merge_keeps_previous_values(self):
        """追问后用户只补了「3天」，不能把之前说的目的地弄丢。"""
        prev = TravelBrief(destination="福州", party_size=2, budget_cny=5000.0)
        merged = merge_brief(prev, extract_brief("3天"))
        assert merged.destination == "福州"
        assert merged.days == 3
        assert merged.party_size == 2
        assert merged.budget_cny == 5000.0

    def test_merge_unions_lists(self):
        prev = TravelBrief(destination="福州", must_go=["三坊七巷"])
        merged = merge_brief(prev, extract_brief("还想去鼓山"))
        assert set(merged.must_go) == {"三坊七巷", "鼓山"}

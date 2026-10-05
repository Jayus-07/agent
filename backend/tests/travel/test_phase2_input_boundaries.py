"""tests/travel/test_phase2_input_boundaries.py — 二期「输入与展示 P0」验收测试

覆盖验收清单 #63/#71/#82/#115/#83/#73/#72/#66 八项：
  - #63 相对日期解析（下周五/这周末/大后天），歧义取周六并回显
  - #71 ISO 完整过去日期拦截 + 拦截原因可区分（extract_past_date）
  - #82 首末日时间（消息解析 → brief 字段 → 排程光标承接 → 指纹传导）
  - #115 ICS VTIMEZONE Asia/Shanghai + DTSTART;TZID
  - #83 同名异地 POI 同城校验（resolve_place 丢弃异地 hit）
  - #73 多目的地检测（主目的地之外的城市明示未纳入）
  - #72 天数上限裁剪（配置化，notes 明示；契约层不炸存量 checkpoint）
  - #66 avoid 通道贯穿（候选池唯一出口过滤 + repair 重排不引入新点）
"""
from __future__ import annotations

from datetime import date, timedelta

import backend.travel.services.poi_service as poi_service_module
from backend.config import travel as T
from backend.tests.travel.conftest import make_day, make_item, make_itinerary, make_poi
from backend.travel.agents.requirement_agent import (
    detect_multi_city,
    extract_arrival_time,
    extract_departure_time,
    extract_fresh_brief,
    extract_past_date,
    extract_relative_date_expr,
    extract_start_date,
)
from backend.travel.graph_state import brief_fingerprint
from backend.travel.models.brief import TravelBrief
from backend.travel.services.transit_service import schedule_day
from backend.tools.travel.live_map import _same_city, resolve_place


TODAY = date(2026, 10, 4)  # 周日（锚定相对日期断言）


# ============================================================
# #63 相对日期
# ============================================================
class TestRelativeDate:
    def test_tomorrow_family(self):
        assert extract_start_date("明天去福州", today=TODAY) == TODAY + timedelta(days=1)
        assert extract_start_date("后天出发", today=TODAY) == TODAY + timedelta(days=2)
        assert extract_start_date("大后天玩", today=TODAY) == TODAY + timedelta(days=3)

    def test_next_friday_acceptance_phrase(self):
        """验收原话：「下周五去泉州玩三天」。TODAY 是周日 → 下周五 = 10-09。"""
        got = extract_start_date("下周五去泉州玩三天", today=TODAY)
        assert got == date(2026, 10, 9)
        assert extract_relative_date_expr("下周五去泉州玩三天") == "下周五"

    def test_this_weekday_past_rolls_next_week(self):
        """今天周六说「这周五」——本周五已过，顺延下周（用户不指过去）。"""
        saturday = date(2026, 10, 3)
        assert extract_start_date("这周五出发", today=saturday) == date(2026, 10, 9)

    def test_this_weekday_future_stays_this_week(self):
        """今天周一说「这周五」——本周五未过，取本周五。"""
        monday = date(2026, 9, 28)
        assert extract_start_date("周五出发", today=monday) == date(2026, 10, 2)

    def test_weekend_takes_saturday_and_echoes(self):
        """「周末」歧义（两天）取周六并回显原文。"""
        wednesday = date(2026, 10, 7)
        assert extract_start_date("这周末去厦门", today= wednesday) == date(2026, 10, 10)
        assert extract_relative_date_expr("这周末去厦门") == "这周末"

    def test_weekend_today_when_sunday(self):
        """今天周日说「这周末」——周六已过，周末剩余部分=今天。"""
        assert extract_start_date("这周末玩", today=TODAY) == TODAY

    def test_next_weekend(self):
        assert extract_start_date("下周末出发", today=TODAY) == date(2026, 10, 10)

    def test_explicit_date_beats_relative(self):
        """明确日期优先于相对词，回显函数必须让路（否则误导用户）。"""
        message = "10月20日（周五）出发"
        assert extract_start_date(message, today=TODAY) == date(2026, 10, 20)
        assert extract_relative_date_expr(message) == ""

    def test_no_date_word_returns_none(self):
        assert extract_start_date("去福州玩3天", today=TODAY) is None
        assert extract_relative_date_expr("去福州玩3天") == ""


# ============================================================
# #71 过去日期拦截
# ============================================================
class TestPastDate:
    def test_past_iso_date_rejected(self):
        assert extract_start_date("2026-10-01 出发", today=TODAY) is None

    def test_past_date_is_distinguishable_from_unsaid(self):
        """extract_past_date 让「给了但已过去」与「没说」可区分（提示依据）。"""
        assert extract_past_date("2026-10-01 出发", today=TODAY) == date(2026, 10, 1)
        assert extract_past_date("2026-10-20 出发", today=TODAY) is None
        assert extract_past_date("去福州玩3天", today=TODAY) is None

    def test_cn_month_day_never_past(self):
        """中文月日自动进位明年，不产生过去日期。"""
        got = extract_start_date("3月1日出发", today=TODAY)
        assert got == date(2027, 3, 1)
        assert extract_past_date("3月1日出发", today=TODAY) is None


# ============================================================
# #82 首末日时间：抽取 → 契约 → 排程
# ============================================================
class TestTimeWindowExtraction:
    def test_arrival_explicit(self):
        assert extract_arrival_time("16点到泉州") == "16:00"
        assert extract_arrival_time("晚上8点到") == "20:00"
        assert extract_arrival_time("下午4点半到") == "16:30"
        assert extract_arrival_time("9:00抵达") == "09:00"

    def test_arrival_vague_period(self):
        """无数字的模糊时段按约定钟点承接（notes 回显可纠正）。"""
        assert extract_arrival_time("晚上到福州") == "20:00"
        assert extract_arrival_time("下午到") == "15:00"

    def test_departure_explicit_and_vague(self):
        assert extract_departure_time("10点走") == "10:00"
        assert extract_departure_time("晚上10点出发") == "22:00"
        assert extract_departure_time("上午走") == "09:00"

    def test_time_range_not_misparsed(self):
        """「8点到10点营业」是时间区间，不是到达时刻。"""
        assert extract_arrival_time("8点到10点营业") is None

    def test_unsaid_is_none(self):
        assert extract_arrival_time("福州玩3天") is None
        assert extract_departure_time("福州玩3天") is None

    def test_fresh_brief_carries_time(self):
        """「下周五」相对日期解析（日期断言动态计算，修 10-05 跨周界腐坏）。"""
        brief = extract_fresh_brief("下周五16点到泉州玩三天，10点走")
        assert brief.arrival_time == "16:00"
        assert brief.departure_time == "10:00"
        # 下周五 = 今天 + 7 - today.weekday() + 4（ISO 周一为首日），与
        # requirement_agent 的相对日期推导同口径动态断言（原写死
        # 2026-10-09，跨周后自然漂移为假失败）
        expected = date.today() + timedelta(days=7 - date.today().weekday() + 4)
        assert brief.start_date == expected

    def test_merge_keeps_previous_when_unmentioned(self):
        from backend.travel.services.requirement_service import merge_brief

        prev = TravelBrief(destination="泉州", days=3,
                           arrival_time="16:00", departure_time="10:00")
        merged = merge_brief(prev, extract_fresh_brief("第二天别太满"))
        assert merged.arrival_time == "16:00"
        assert merged.departure_time == "10:00"
        updated = merge_brief(prev, extract_fresh_brief("改为14点到"))
        assert updated.arrival_time == "14:00"

    def test_fingerprint_changes_with_time_window(self):
        base = TravelBrief(destination="泉州", days=3)
        early = base.model_copy(update={"arrival_time": "10:00"})
        late = base.model_copy(update={"arrival_time": "16:00"})
        assert brief_fingerprint(early) != brief_fingerprint(late)
        # 空串与未设置必须同指纹（否则每轮假重排）
        assert brief_fingerprint(base) == brief_fingerprint(
            base.model_copy(update={"arrival_time": ""}))


class TestScheduleTimeWindow:
    # 开门 07:00 早于默认光标 08:30——首条目起点完全由光标决定（排除
    # 「等开门」干扰，光标效果可被强断言）
    POIS = [
        make_poi(poi_id="a", name="景点A", open_time="07:00"),
        make_poi(poi_id="b", name="景点B", open_time="07:00", lat=26.1),
    ]

    def test_first_day_starts_at_arrival(self):
        brief = TravelBrief(destination="测试城", days=2, arrival_time="16:00")
        day1 = schedule_day(list(self.POIS), 1, None, brief)
        assert day1.items[0].start == "16:00"
        day2 = schedule_day(list(self.POIS), 2, None, brief)
        assert day2.items[0].start == "08:30"  # 非首日不受影响

    def test_arrival_earlier_than_default_does_not_advance(self):
        brief = TravelBrief(destination="测试城", days=1, arrival_time="06:00")
        day = schedule_day(list(self.POIS), 1, None, brief)
        assert day.items[0].start == "08:30"

    def test_last_day_ends_before_departure(self):
        brief = TravelBrief(destination="测试城", days=2, departure_time="10:00")
        day1 = schedule_day(list(self.POIS), 1, None, brief)
        assert day1.items[-1].end > "10:00"  # 非末日不受影响
        day2 = schedule_day(list(self.POIS), 2, None, brief)
        for item in day2.items:
            assert item.end <= "10:00"
        assert any("10:00 离开" in i.note for i in day2.items)

    def test_no_times_keeps_default_window(self):
        """无到达/离开时间时维持 08:30 起步 / 23:59 截止（回归）。"""
        brief = TravelBrief(destination="测试城", days=2)
        day1 = schedule_day(list(self.POIS), 1, None, brief)
        assert day1.items[0].start == "08:30"

    def test_late_arrival_does_not_schedule_lunch(self):
        """16:00 到达补插「午餐」是假排程——午后到达不再补午餐。"""
        brief = TravelBrief(destination="测试城", days=1, arrival_time="16:00")
        day = schedule_day(list(self.POIS), 1, None, brief)
        assert all(i.title != "午餐" for i in day.items)


# ============================================================
# #73 多目的地检测
# ============================================================
class TestMultiCityDetection:
    def test_arrow_pattern(self):
        assert detect_multi_city("上海2天→杭州2天→苏州1天") == ["杭州", "苏州"]

    def test_route_pair_is_not_multi_city(self):
        """「从北京出发去西安」是出发/目的语义，不误报。"""
        assert detect_multi_city("从北京出发去西安玩5天") == []
        assert detect_multi_city("带爸妈从北京出发去西安玩5天") == []

    def test_negated_city_not_counted(self):
        assert detect_multi_city("不去厦门了，重新规划杭州两天") == []

    def test_origin_without_destination_not_counted(self):
        assert detect_multi_city("福州，从厦门出发") == []

    def test_single_city_empty(self):
        assert detect_multi_city("去泉州玩三天") == []


# ============================================================
# #83 同城校验
# ============================================================
class TestSameCityGuard:
    def test_same_city_matrix(self):
        assert _same_city("福州市", "福州") is True
        assert _same_city("泉州市辖区", "泉州") is True
        assert _same_city("福州", "福州市") is True
        assert _same_city("厦门市", "福州") is False
        assert _same_city("", "福州") is None
        assert _same_city("福州市", "") is None

    def test_resolve_place_drops_foreign_hit(self, monkeypatch):
        """异地同名 POI 不入池：腾讯 region 检索是偏好限定不是硬过滤。"""
        import backend.tools.travel.live_map as lm

        monkeypatch.setattr(lm, "is_enabled", lambda: True)
        monkeypatch.setattr(
            lm.api, "place_search",
            lambda *_a, **_k: [{
                "id": "tx1", "name": "中山公园", "city": "厦门市",
                "district": "思明区", "adcode": "350203",
                "lat": 24.46, "lng": 118.09, "category": "公园",
            }],
        )
        monkeypatch.setattr(lm.api, "resolve_district", lambda *_a: None)
        assert resolve_place("中山公园", "福州") is None

    def test_resolve_place_keeps_same_city_hit(self, monkeypatch):
        import backend.tools.travel.live_map as lm

        monkeypatch.setattr(lm, "is_enabled", lambda: True)
        monkeypatch.setattr(
            lm.api, "place_search",
            lambda *_a, **_k: [{
                "id": "tx2", "name": "中山公园", "city": "福州市",
                "district": "鼓楼区", "adcode": "350102",
                "lat": 26.08, "lng": 119.30, "category": "公园",
            }],
        )
        monkeypatch.setattr(lm.api, "resolve_district", lambda *_a: None)
        poi = resolve_place("中山公园", "福州")
        assert poi is not None and poi.name == "中山公园"

    def test_resolve_place_missing_city_info_passes(self, monkeypatch):
        """hit 未带归属信息时不误杀（数据缺失 ≠ 异地）。"""
        import backend.tools.travel.live_map as lm

        monkeypatch.setattr(lm, "is_enabled", lambda: True)
        monkeypatch.setattr(
            lm.api, "place_search",
            lambda *_a, **_k: [{
                "id": "tx3", "name": "无名公园", "city": "",
                "lat": 26.08, "lng": 119.30, "category": "公园",
            }],
        )
        monkeypatch.setattr(lm.api, "resolve_district", lambda *_a: None)
        assert resolve_place("无名公园", "福州") is not None


# ============================================================
# #72 天数上限
# ============================================================
class TestDaysUpperBound:
    def test_contract_accepts_large_days(self):
        """契约层不设上限：存量 checkpoint 里 >15 天的 brief 必须还能反序列化。"""
        assert TravelBrief(destination="福州", days=30).days == 30

    def test_slot_filler_clamps_and_discloses(self):
        update = slot_filler_update("去福州玩30天")
        assert update["brief"]["days"] == T.TRAVEL_MAX_DAYS
        assert any(str(T.TRAVEL_MAX_DAYS) in n and "最多支持" in n
                   for n in update["notes"])

    def test_within_bound_not_clamped(self):
        update = slot_filler_update("去福州玩3天")
        assert update["brief"]["days"] == 3
        assert not any("上限" in n for n in update["notes"])


# ============================================================
# #66 avoid 通道贯穿
# ============================================================
class TestAvoidThreading:
    def test_retrieve_candidates_filters_avoid(self, monkeypatch):
        """live/攻略两路此前不消费 avoid——候选池唯一出口统一过滤。"""
        monkeypatch.setattr(poi_service_module.T, "TRAVEL_POI_SOURCE", "live")
        monkeypatch.setattr(
            poi_service_module, "_build_live_candidates",
            lambda brief: (
                [make_poi(poi_id="p_avoid", name="鼓山", source="tencent:lbs"),
                 make_poi(poi_id="p_ok", name="西湖公园", source="tencent:lbs")],
                [],
            ),
        )
        brief = TravelBrief(destination="福州", days=2, avoid=["鼓山"])
        candidates, notes = poi_service_module.retrieve_candidates(brief)
        names = [p.name for p in candidates]
        assert "鼓山" not in names
        assert "西湖公园" in names
        assert any("鼓山" in n for n in notes)

    def test_required_point_name_survives_avoid(self, monkeypatch):
        """kept_required 纪律：与 avoid 冲突的 required 点名不被候选过滤删除，
        由 validator warning 披露冲突（点名永不被静默丢弃）。"""
        monkeypatch.setattr(poi_service_module.T, "TRAVEL_POI_SOURCE", "live")
        monkeypatch.setattr(
            poi_service_module, "_build_live_candidates",
            lambda brief: (
                [make_poi(poi_id="p_point", name="鼓山", required=True,
                          source="tencent:lbs"),
                 make_poi(poi_id="p_avoid", name="鼓山", source="tencent:lbs")],
                [],
            ),
        )
        brief = TravelBrief(destination="福州", days=2, avoid=["鼓山"])
        candidates, _notes = poi_service_module.retrieve_candidates(brief)
        assert [p.required for p in candidates if p.name == "鼓山"] == [True]

    def test_repair_never_introduces_new_pois(self):
        """repair 只删不加：重排后的到访点集必须是原点集的子集——
        avoid 点一旦不在池/不在行程，修复不可能让它复活。"""
        from backend.travel.repair import repair_itinerary
        from backend.travel.validator import check_itinerary

        avoid_poi = make_poi(poi_id="late", name="深夜馆", open_time="09:00",
                             close_time="17:00")
        keep_poi = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                            close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, avoid=["深夜馆"]),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=keep_poi),
                make_item(title="深夜馆", start="18:00", end="19:30", poi=avoid_poi),
            ])],
        )
        repaired, _actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        before = {i.poi.poi_id for d in it.days for i in d.items if i.poi}
        after = {i.poi.poi_id for d in repaired.days for i in d.items if i.poi}
        assert after <= before
        assert "late" not in after


# ============================================================
# #115 ICS 时区
# ============================================================
class TestIcsTimezone:
    def test_vtimezone_block_and_tzid_reference(self):
        from backend.app.api.routes.travel import itinerary_to_ics

        ics = itinerary_to_ics(_ics_itinerary_dict())
        assert "BEGIN:VTIMEZONE" in ics
        assert "TZID:Asia/Shanghai" in ics
        assert "TZOFFSETFROM:+0800" in ics
        assert "TZOFFSETTO:+0800" in ics
        assert "DTSTART;TZID=Asia/Shanghai:" in ics
        assert "DTEND;TZID=Asia/Shanghai:" in ics
        # VTIMEZONE 块必须先于引用它的 VEVENT（RFC 5545 消费方按序解析）
        assert ics.index("END:VTIMEZONE") < ics.index("BEGIN:VEVENT")
        # VEVENT 的 DTSTART/DTEND 不允许 floating（VTIMEZONE STANDARD 段的
        # DTSTART:19700101T000000 是 RFC 规范固定写法，不在检查范围）
        in_vevent = ics[ics.index("BEGIN:VEVENT"):].splitlines()
        for line in in_vevent:
            if line.startswith(("DTSTART", "DTEND")):
                assert line.startswith(("DTSTART;TZID=", "DTEND;TZID="))


def _ics_itinerary_dict() -> dict:
    from backend.travel.experts.transit import build_itinerary

    itinerary, _ = build_itinerary(
        TravelBrief(destination="福州", days=1, party_size=2,
                    start_date=date(2026, 10, 20)),
        [[make_poi(poi_id="p1", name="三坊七巷")]],
    )
    return itinerary.model_dump()


# ============================================================
# 测试辅助
# ============================================================
def slot_filler_update(message: str) -> dict:
    from backend.travel.slot_filler import slot_filler_node

    return slot_filler_node({"user_message": message})

"""tests/travel/test_phase4_p1_batch.py — 四期 P1 批验收测试

覆盖：#64 模糊时间词 / #65 地名歧义口径 / #67 软必去通道（#62 前端墓碑、
#93 前端展示不在本文件）。
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.travel.agents.requirement_agent import (
    extract_optional_go,
    extract_vague_time_expr,
    extract_fresh_brief,
)
from backend.travel.graph_state import brief_fingerprint
from backend.travel.models.brief import TravelBrief
from backend.travel.services.requirement_service import merge_brief
from backend.tests.travel.conftest import make_poi
from backend.travel.services import poi_service


TODAY = date(2026, 10, 4)


class TestVagueTime:
    def test_month_end_is_vague(self):
        assert extract_vague_time_expr("月底去上海玩两天") == "月底"

    def test_explicit_date_not_vague(self):
        assert extract_vague_time_expr("10月20日去上海") == ""

    def test_relative_date_beats_vague(self):
        """「下周五」有确定日，不再报模糊。"""
        assert extract_vague_time_expr("下周五去上海") == ""

    def test_no_time_word_empty(self):
        assert extract_vague_time_expr("去上海玩两天") == ""


class TestPlaceAmbiguityInvariants:
    def test_alias_table_is_single_valued(self):
        """#65 口径：别名表天然单义（一个别名只映射一个城市），
        目的地歧义在数据层不存在；跨城语义由 #73 多城市说明与
        destination 消歧（否定剔除/最左）承接。"""
        from collections import defaultdict

        from backend.tools.travel import poi_seed
        from backend.travel.data import cities as city_directory

        rev: dict[str, list[str]] = defaultdict(list)
        for alias, city in {**poi_seed.CITY_ALIASES,
                            **city_directory.directory_aliases()}.items():
            rev[alias].append(city)
        dup = {a: cs for a, cs in rev.items() if len(cs) > 1}
        assert not dup, f"别名表出现一对多映射：{dup}"


class TestOptionalGo:
    def test_extract_trigger_capture(self):
        got = extract_optional_go("福州两天，有空再去鼓山")
        assert got == ["鼓山"]

    def test_must_go_not_absorbed_into_optional(self):
        brief = extract_fresh_brief("福州2天必去三坊七巷，有空再去鼓山")
        assert "三坊七巷" in brief.must_go
        assert "三坊七巷" not in brief.optional_go
        assert "鼓山" in brief.optional_go

    def test_avoid_beats_optional(self):
        got = extract_optional_go("有空再去鼓山，别去西禅寺")
        assert "西禅寺" not in got

    def test_merge_union_and_promotion(self):
        prev = TravelBrief(destination="福州", days=2, optional_go=["鼓山"])
        fresh = extract_fresh_brief("必去鼓山")
        merged = merge_brief(prev, fresh)
        # 必去升级后不留在软清单（分级唯一）
        assert "鼓山" in merged.must_go
        assert "鼓山" not in merged.optional_go

    def test_fingerprint_changes_with_optional(self):
        base = TravelBrief(destination="福州", days=2)
        with_opt = base.model_copy(update={"optional_go": ["鼓山"]})
        assert brief_fingerprint(base) != brief_fingerprint(with_opt)


class TestOptionalScheduling:
    def _skeleton(self, brief, candidates):
        return poi_service.build_skeleton(brief, candidates)

    def test_optional_dropped_first_under_capacity(self):
        """容量不足时软必去最先被挤掉（且进 dropped 披露）。"""
        from backend.config import travel as T

        brief = TravelBrief(destination="测试城", days=1, pace="relaxed",
                            optional_go=["软点"])
        candidates = [make_poi(poi_id="m", name="必去景点", required=True),
                      make_poi(poi_id="a", name="普通景点A", rating=4.5),
                      make_poi(poi_id="b", name="普通景点B", rating=4.0),
                      make_poi(poi_id="s", name="软点", rating=4.8)]
        skeleton = self._skeleton(brief, candidates)
        flat = [p.name for day in skeleton.days for p in day]
        assert "必去景点" in flat
        if len(flat) < 4:
            assert "软点" not in flat, "软必去不应挤占普通候选"
            assert "软点" in skeleton.dropped

    def test_repair_drops_optional_first(self):
        """时长超限修复：软必去先于普通候选被移除。"""
        from backend.travel.repair import repair_itinerary
        from backend.travel.validator import check_itinerary
        from backend.tests.travel.conftest import make_day, make_item, make_itinerary

        # 时长超限（PACE_TOO_INTENSE）走「选择删谁」路径：两点各 200 分钟
        # 超 relaxed 上限，删除顺序应先软必去后普通点。开场 09:00 后避开
        # TIME_CLOSED（那是定点删除，测不到排序语义）。
        optional_poi = make_poi(poi_id="s", name="软点", suggested_minutes=200,
                                rating=4.8, open_time="07:00")
        normal_poi = make_poi(poi_id="n", name="普通点", suggested_minutes=200,
                              rating=4.0, open_time="07:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1,
                              optional_go=["软点"]),
            days=[make_day(items=[
                make_item(title="普通点", start="09:00", end="12:20",
                          poi=normal_poi),
                make_item(title="软点", start="12:20", end="15:40",
                          poi=optional_poi),
            ])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        names = [i.title for d in repaired.days for i in d.items]
        assert "软点" not in names, "软必去应先于普通点被修复移除"
        assert "普通点" in names


class TestOpenStatus:
    """验收 #77：高德检索时刻营业状态——标注披露+必去警告，不冒充停业。"""

    def _merged(self, monkeypatch, open_status: str):
        import backend.travel.services.poi_service as m

        normal = make_poi(poi_id="tx1", name="鼓山", source="tencent:lbs")
        captured = {}

        def fake_merge_amap(brief, queries, pois, observed_at):
            captured["pois"] = pois
            rec = {"id": "am1", "name": "鼓山", "category": "景点",
                   "lat": 26.05, "lng": 119.39, "rating": 4.7,
                   "open_time_today": "08:00-18:00",
                   "open_status": open_status}
            out = [normal, dict(rec)]
            return out, []

        monkeypatch.setattr(m, "_merge_amap_candidates", fake_merge_amap)
        return m

    def test_closed_status_tagged_and_warned_for_must_go(self, monkeypatch):
        import backend.travel.services.poi_service as m

        monkeypatch.setattr(m.T, "TRAVEL_POI_SOURCE", "live")
        monkeypatch.setattr(m.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", True)
        # 腾讯路 stub 底层检索（真实 _build_live_candidates 跑通合并链路）
        monkeypatch.setattr(
            m.live_search_service, "search_places",
            lambda *, keyword, city, page_size=8: {"pois": [{
                "id": "tx1", "name": "鼓山", "category": "景点",
                "lat": 26.05, "lng": 119.39,
            }]})
        real_merge = m._merge_amap_candidates
        # 直接走真实合并逻辑：stub 掉高德检索，喂 open_status=已打烊
        from backend.travel.services import live_search_service

        class _FakeEnv:
            def search_attractions(self, *, keyword, city, page_size=10):
                return {"merchants": [{
                    "id": "am1", "name": "鼓山", "category": "景点",
                    "lat": 26.05, "lng": 119.39, "rating": 4.7,
                    "open_time_today": "08:00-18:00",
                    "open_status": "已打烊",
                }]}

        monkeypatch.setattr(m.live_search_service, "search_attractions",
                            _FakeEnv().search_attractions)
        brief = TravelBrief(destination="福州", days=2, must_go=["鼓山"],
                            preferences=["人文"])
        candidates, _notes = m.retrieve_candidates(brief)
        target = next(p for p in candidates if p.name == "鼓山")
        assert "高德状态:已打烊" in target.tags
        assert "检索时已打烊" in (target.reason or "")
        skeleton = m.build_skeleton(brief, candidates)
        assert any("已打烊" in n and "鼓山" in n for n in skeleton.notes)

    def test_open_status_not_misread_as_closed(self, monkeypatch):
        import backend.travel.services.poi_service as m

        monkeypatch.setattr(m.T, "TRAVEL_POI_SOURCE", "live")
        monkeypatch.setattr(m.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", True)
        # 腾讯路 stub 底层检索（真实 _build_live_candidates 跑通合并链路）
        monkeypatch.setattr(
            m.live_search_service, "search_places",
            lambda *, keyword, city, page_size=8: {"pois": [{
                "id": "tx1", "name": "鼓山", "category": "景点",
                "lat": 26.05, "lng": 119.39,
            }]})

        class _FakeEnv:
            def search_attractions(self, *, keyword, city, page_size=10):
                return {"merchants": [{
                    "id": "am1", "name": "鼓山", "category": "景点",
                    "lat": 26.05, "lng": 119.39, "rating": 4.7,
                    "open_time_today": "08:00-18:00",
                    "open_status": "营业中",
                }]}

        monkeypatch.setattr(m.live_search_service, "search_attractions",
                            _FakeEnv().search_attractions)
        brief = TravelBrief(destination="福州", days=2, must_go=["鼓山"],
                            preferences=["人文"])
        candidates, _ = m.retrieve_candidates(brief)
        target = next(p for p in candidates if p.name == "鼓山")
        assert "高德状态:已打烊" not in target.tags
        skeleton = m.build_skeleton(brief, candidates)
        assert not any("已打烊" in n for n in skeleton.notes)


class TestSourceConflict:
    """验收 #90：来源冲突检测（裁决语义先以单测固化，知识路径接入时消费）。"""

    def test_provider_beats_rag_on_same_field(self):
        from backend.travel.core.evidence_utils import detect_conflicts

        evidences = {
            "poi1#hours_provider": {
                "value": {"open_time": "17:30"}, "source": "provider",
                "source_type": "LIVE", "poi_id": "poi1",
            },
            "poi1#hours_rag": {
                "value": {"open_time": "18:00"}, "source": "rag",
                "source_type": "SEED", "poi_id": "poi1",
            },
        }
        conflicts = detect_conflicts(evidences)
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c["field"] == "open_time"
        assert c["winner"] == "17:30"
        assert c["rule"] == "source_priority"
        assert c["values"][0]["value"] == "17:30"

    def test_same_value_no_conflict(self):
        from backend.travel.core.evidence_utils import detect_conflicts

        evidences = {
            "poi1#a": {"value": {"open_time": "17:30"}, "source": "provider",
                       "poi_id": "poi1"},
            "poi1#b": {"value": {"open_time": "17:30"}, "source": "rag",
                       "poi_id": "poi1"},
        }
        assert detect_conflicts(evidences) == []

    def test_single_source_no_conflict(self):
        from backend.travel.core.evidence_utils import detect_conflicts

        evidences = {"poi1#a": {"value": {"open_time": "17:30"},
                                "source": "provider", "poi_id": "poi1"}}
        assert detect_conflicts(evidences) == []

    def test_amap_placeholder_hours_disclosed(self, monkeypatch):
        """高德替换但营业时段解析失败沿用占位 → reason 必须标注混搭。"""
        import backend.travel.services.poi_service as m

        monkeypatch.setattr(m.T, "TRAVEL_POI_SOURCE", "live")
        monkeypatch.setattr(m.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", True)
        monkeypatch.setattr(
            m.live_search_service, "search_places",
            lambda *, keyword, city, page_size=8: {"pois": [{
                "id": "tx9", "name": "西湖公园", "category": "景点",
                "lat": 26.08, "lng": 119.29,
            }]})

        class _FakeAmap:
            def search_attractions(self, *, keyword, city, page_size=10):
                # open_time_today 无法解析成时段 → hours=None → 占位混搭
                return {"merchants": [{
                    "id": "am9", "name": "西湖公园", "category": "景点",
                    "lat": 26.08, "lng": 119.29, "rating": 4.6,
                    "open_time_today": "全天（文案非时段格式）",
                    "open_status": "营业中",
                }]}

        monkeypatch.setattr(m.live_search_service, "search_attractions",
                            _FakeAmap().search_attractions)
        brief = TravelBrief(destination="福州", days=2, preferences=["人文"])
        candidates, _ = m.retrieve_candidates(brief)
        target = next(p for p in candidates if p.poi_id.startswith("amap:"))
        assert "营业时间为占位" in (target.reason or "")


class TestRagFreshness:
    """验收 #94：知识摘录文内年份陈旧标注（#93 快照时间在 SOURCE_STALE）。"""

    def test_old_year_annotated(self):
        from backend.tools.travel.knowledge import annotate_stale_years

        out = annotate_stale_years(
            ["鼓山缆车自2019年运营"], today=date(2026, 10, 4))
        assert out == ["鼓山缆车自2019年运营（文中提及 2019 年，可能较旧）"]

    def test_recent_year_not_annotated(self):
        from backend.tools.travel.knowledge import annotate_stale_years

        chunks = ["2026年新开通的地铁2号线"]
        assert annotate_stale_years(chunks, today=date(2026, 10, 4)) == chunks

    def test_no_year_untouched(self):
        from backend.tools.travel.knowledge import annotate_stale_years

        chunks = ["三坊七巷全天开放"]
        assert annotate_stale_years(chunks, today=date(2026, 10, 4)) == chunks


class TestLogMasking:
    """验收 #124：旅游 trace 落库 question 掩码（复用 C11 口径）。"""

    def test_masked_trace_question(self):
        from backend.app.api.routes.travel import _masked_trace_question

        masked = _masked_trace_question("我是张三，13812345678，帮我规划福州")
        assert "13812345678" not in masked
        assert "张三" not in masked
        assert "福州" in masked  # 非 PII 内容不动

    def test_plain_message_untouched(self):
        from backend.app.api.routes.travel import _masked_trace_question

        assert _masked_trace_question("福州两天") == "福州两天"


class TestCrossTurnBaseSeed:
    """#53/#140 地基自愈：checkpointer 丢失后改单轮从版本账本重建基底。

    实测缺陷链（2026-10-04 发布 Smoke 暴露）：强杀 app（#135）清空
    MemorySaver 后，改单轮 brief_fingerprint 无基线 → 变化检测恒 False
    （旧行程被当草案输出）、transit 版本 parent 丢失（v1 回落与旧版同号）。
    修法：API 层从 plan_store 账本预置 reconstruct_brief + parent 版本。
    """

    def test_seed_injects_latest_brief_and_parent(self, monkeypatch):
        import backend.app.api.routes.travel as route_mod

        class _FakeService:
            def latest_version(self, cid, uid):
                return {"plan_version": 7,
                        "itinerary": {"brief": {"destination": "福州",
                                                "days": 2}}}

        monkeypatch.setattr(
            "backend.travel.core.plan_service.plan_version_service",
            _FakeService())
        graph_input = {"user_message": "改成3天", "conversation_id": "c1",
                       "user_id": "u1"}
        route_mod._seed_cross_turn_base(graph_input, "c1", "u1")
        assert graph_input["plan_parent_version"] == 7
        assert graph_input["reconstruct_brief"]["destination"] == "福州"
        # 指纹基线派生：与对账本 brief 直算一致（变化检测的判定输入）
        from backend.travel.graph_state import brief_fingerprint
        from backend.travel.models.brief import TravelBrief

        assert graph_input["brief_fingerprint"] == brief_fingerprint(
            TravelBrief(destination="福州", days=2))

    def test_no_ledger_no_seed(self, monkeypatch):
        import backend.app.api.routes.travel as route_mod

        class _FakeService:
            def latest_version(self, cid, uid):
                return None

        monkeypatch.setattr(
            "backend.travel.core.plan_service.plan_version_service",
            _FakeService())
        graph_input = {"user_message": "福州2天", "conversation_id": "c2",
                       "user_id": "u1"}
        route_mod._seed_cross_turn_base(graph_input, "c2", "u1")
        assert "reconstruct_brief" not in graph_input
        assert "plan_parent_version" not in graph_input

    def test_ledger_failure_is_soft(self, monkeypatch):
        import backend.app.api.routes.travel as route_mod

        class _Boom:
            def latest_version(self, cid, uid):
                raise RuntimeError("db down")

        monkeypatch.setattr(
            "backend.travel.core.plan_service.plan_version_service", _Boom())
        graph_input = {"user_message": "改成3天", "conversation_id": "c3",
                       "user_id": "u1"}
        route_mod._seed_cross_turn_base(graph_input, "c3", "u1")  # 不抛
        assert "plan_parent_version" not in graph_input

    def test_slot_filler_merges_reconstruct_base_and_bumps_version(self):
        """端到端语义：无 checkpoint 基底时，reconstruct_brief 作为上一轮，
        「改成3天」必须判变化并出 v(parent+1)。"""
        import backend.travel.services.poi_service as _  # noqa: F401
        from backend.travel.slot_filler import slot_filler_node

        update = slot_filler_node({
            "user_message": "改成3天",
            "reconstruct_brief": {"destination": "福州", "days": 2,
                                  "party_size": 2},
            "brief_fingerprint": "",
            "itinerary": {"plan_version": 7, "days": []},
        })
        brief = update["brief"]
        assert brief["destination"] == "福州"  # 基底被合并
        assert brief["days"] == 3             # 本轮输入覆盖
        assert update["brief_fingerprint"]    # 有指纹可作下一轮基线


class TestOldCheckpointCompat:
    """验收 #140：旧 schema checkpoint 恢复复验——后期新增字段在旧数据里
    缺失时，恢复/改单必须按缺省推进，不允许 KeyError/ValidationError 炸图。"""

    def test_load_brief_with_legacy_brief_dict(self):
        """旧 brief（无 arrival_time/optional_go/adults 等后加字段）可加载。"""
        from backend.travel.graph_state import load_brief

        legacy = {"destination": "福州", "days": 2, "party_size": 2,
                  "start_date": "2026-10-20", "preferences": ["人文"],
                  "must_go": ["三坊七巷"], "avoid": [], "pace": "moderate",
                  "tier": "economy", "version": 1}
        brief = load_brief({"brief": legacy})
        assert brief.destination == "福州"
        assert brief.arrival_time == ""      # 后加字段缺省
        assert brief.optional_go == []
        assert brief.adults is None

    def test_slot_filler_on_legacy_state(self):
        """旧 checkpoint 状态（无 fingerprint/无新字段）改单轮：按无基底推进
        （自愈预置通道的 API 层职责，此处验证节点层不炸）。"""
        from backend.travel.slot_filler import slot_filler_node

        legacy_state = {
            "user_message": "改成4天",
            "brief": {"destination": "福州", "days": 2, "version": 1},
            "itinerary": {"plan_version": 3, "days": []},
            # 无 brief_fingerprint / 无 reconstruct_brief（旧 checkpoint）
        }
        update = slot_filler_node(legacy_state)
        assert update["brief"]["days"] == 4
        assert update["brief"]["destination"] == "福州"

    def test_seed_rebuilds_base_for_legacy_checkpoint(self, monkeypatch):
        """端到端口径：旧 checkpoint 的 state 无指纹基线时，API 层
        _seed_cross_turn_base 从账本重建（#140 与 #53 的交汇点）。"""
        import backend.app.api.routes.travel as route_mod

        class _FakeService:
            def latest_version(self, cid, uid):
                return {"plan_version": 3,
                        "itinerary": {"brief": {"destination": "福州",
                                                "days": 2}}}

        monkeypatch.setattr(
            "backend.travel.core.plan_service.plan_version_service",
            _FakeService())
        graph_input = {"user_message": "改成4天", "conversation_id": "c9",
                       "user_id": "u1"}
        route_mod._seed_cross_turn_base(graph_input, "c9", "u1")
        assert graph_input["plan_parent_version"] == 3
        assert graph_input["brief_fingerprint"]


class TestRunDeadline:
    """验收 #129：总执行 deadline——RunControl 超时到期即 cancel("timeout")，
    专家在下一个协作点收到 RunStopped（显式总 deadline 即
    TRAVEL_REQUEST_TIMEOUT_S，经 RequestExecutor 注入每一轮）。"""

    def test_expired_deadline_raises_run_stopped(self):
        import time as _time

        from backend.travel.request_runtime import RunControl, RunStopped

        control = RunControl(timeout_s=0.05)
        _time.sleep(0.06)
        with pytest.raises(RunStopped) as excinfo:
            control.check()
        assert excinfo.value.reason == "timeout"

    def test_live_deadline_does_not_stop(self):
        from backend.travel.request_runtime import RunControl

        control = RunControl(timeout_s=30)
        control.check()  # 不抛
        assert not control.stopped.is_set()

    def test_executor_carries_configured_timeout(self, monkeypatch):
        """executor 的 timeout_s 必须来自 TRAVEL_REQUEST_TIMEOUT_S（显式
        总 deadline 的单一来源），不散落硬编码。"""
        from backend.config import travel as travel_config
        import backend.travel.request_runtime as rr

        executor = rr.RequestExecutor(
            workers=1, timeout_s=travel_config.TRAVEL_REQUEST_TIMEOUT_S)
        assert executor.timeout_s == travel_config.TRAVEL_REQUEST_TIMEOUT_S
        assert executor.timeout_s >= 1.0  # 配置链活着（max(1.0, ...) 下限）


class TestGroupPace:
    """验收 #80：同行人群 → 默认节奏派生（显式 pace 优先）。"""

    def test_elderly_party_derives_relaxed(self):
        from backend.travel.agents.requirement_agent import (
            extract_group_pace,
            extract_pace,
        )

        message = "带爸妈去福州玩3天"
        assert extract_pace(message) is None      # 无显式节奏词
        assert extract_group_pace(message) == "relaxed"
        brief = extract_fresh_brief(message)
        assert brief.pace == "relaxed"

    def test_kids_party_derives_relaxed(self):
        brief = extract_fresh_brief("带娃去厦门玩2天")
        assert brief.pace == "relaxed"

    def test_explicit_pace_beats_group_hint(self):
        """用户显式说「特种兵」时显式词优先（同为派生也取直接表达）。"""
        brief = extract_fresh_brief("带爸妈去福州玩3天，节奏紧凑一点")
        assert brief.pace == "intense"

    def test_no_group_word_defaults_moderate(self):
        brief = extract_fresh_brief("去福州玩3天")
        assert brief.pace == "moderate"

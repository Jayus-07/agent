"""tests/travel/test_scenarios.py — 验收场景自动化（2026-09-22）

对应 docs/travel-test-scenarios-2026-09-22.md 的 T/W/B/C 四组：
  T 多轮会话（memory checkpointer，跨轮行为）
  W 外部调用失败软降级（mock 外部依赖，验证不阻塞主链）
  B 输入边界（纯函数层，离线）
  C 并发（进程内线程池）

纪律：域图级用例一律 monkeypatch 关掉 RAG/偏好（外部 IO），天气保持开启但
用无日期消息走跳过路径 —— 保证测试离线、确定、快。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest

import backend.config.travel as T
import backend.travel.graph_builder as gb
from backend.travel.graph_builder import build_travel_graph
from backend.travel.graph_state import new_travel_graph_input


# ============================================================
# 公共 fixture：memory checkpointer 域图（跨轮）+ 干净外设
# ============================================================
@pytest.fixture
def memory_graph(monkeypatch):
    """带 MemorySaver 的域图（每例 fresh），外设全关。"""
    monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", True)
    monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_BACKEND", "memory")
    monkeypatch.setattr(T, "TRAVEL_RAG_ENABLED", False)
    monkeypatch.setattr(T, "TRAVEL_PREFS_ENABLED", False)
    monkeypatch.setattr(gb, "_travel_graph", None)
    graph = gb.get_travel_graph()
    yield graph
    monkeypatch.setattr(gb, "_travel_graph", None)


def _tid() -> str:
    return f"t-sc-{uuid4().hex[:8]}"


def _ask(graph, message: str, tid: str, **kw) -> dict:
    return graph.invoke(
        new_travel_graph_input(message, user_id=kw.get("user_id", "u-sc"),
                               session_id=tid, conversation_id=tid),
        config={"recursion_limit": 40, "configurable": {"thread_id": tid}},
    )


# ============================================================
# T 组：多轮会话
# ============================================================
class TestMultiturn:
    def test_t1_no_slots_asks_with_recommendation(self, memory_graph):
        tid = _tid()
        final = _ask(memory_graph, "帮我规划个行程", tid)
        ans = final.get("final_answer", "")
        assert "还需要确认" in ans
        assert "去哪个城市" in ans and "玩几天" in ans
        # P1-3：追问附目的地推荐，用户可直接选
        assert "先看看" in ans

    def test_t2_followup_answer_not_reasked(self, memory_graph):
        tid = _tid()
        _ask(memory_graph, "帮我规划个行程", tid)
        second = _ask(memory_graph, "杭州", tid)
        ans = second.get("final_answer", "")
        # 城市已答：不再追问城市，只追问天数
        assert "玩几天" in ans
        assert "去哪个城市" not in ans
        third = _ask(memory_graph, "2天", tid)
        assert third.get("itinerary") is not None
        assert (third["itinerary"]["brief"]["destination"] == "杭州")

    def test_t3_cross_turn_days_change_replans(self, memory_graph):
        tid = _tid()
        first = _ask(memory_graph, "杭州2天行程，2个人", tid)
        assert first.get("itinerary") is not None
        second = _ask(memory_graph, "改成3天", tid)
        it = second.get("itinerary") or {}
        assert len(it.get("days", [])) == 3
        assert it.get("change_reason") == "brief_changed"
        assert "days" in (second.get("brief_changed_fields") or [])
        assert any("需求已变化" in n for n in second.get("notes", []))

    def test_t4_vague_pace_feedback_replans(self, memory_graph):
        tid = _tid()
        _ask(memory_graph, "厦门2天行程，节奏紧凑", tid)
        second = _ask(memory_graph, "太赶了", tid)
        it = second.get("itinerary") or {}
        assert (it.get("brief") or {}).get("pace") == "relaxed"
        assert "pace" in (second.get("brief_changed_fields") or [])

    def test_t5_avoid_removes_from_must_go(self, memory_graph):
        tid = _tid()
        _ask(memory_graph, "福州2天，一定要去鼓山", tid)
        second = _ask(memory_graph, "不想去鼓山了", tid)
        brief = second.get("brief") or {}
        assert "鼓山" not in (brief.get("must_go") or [])
        assert any("鼓山" in a for a in (brief.get("avoid") or []))
        names = [i["title"] for d in (second.get("itinerary") or {}).get("days", [])
                 for i in d.get("items", [])]
        assert "鼓山" not in names

    def test_t6_degraded_persistence_refuses_cross_turn(self, memory_graph, monkeypatch):
        """强持久化策略：降级后端里跨轮改单必须明说不可用，而不是静默假成功。"""
        monkeypatch.setattr(T, "TRAVEL_REQUIRE_PERSISTENCE", True)
        tid = _tid()
        _ask(memory_graph, "杭州2天行程，2个人", tid)
        second = _ask(memory_graph, "改成3天", tid)
        notes = second.get("notes", [])
        assert any("跨轮修改行程暂不可用" in n for n in notes)
        # 变化当轮仍按新需求排（3 天）—— 「按全新规划处理」不等于「忽略新需求」
        it = second.get("itinerary") or {}
        assert len(it.get("days", [])) == 3

    def test_t7_state_survives_graph_rebuild_with_same_saver(self, memory_graph):
        """模拟「新 worker + 共享持久后端」：同 checkpointer 重建图后可续。"""
        saver = memory_graph.checkpointer
        tid = _tid()
        _ask(memory_graph, "杭州2天行程，2个人", tid)
        rebuilt = build_travel_graph(checkpointer=saver)
        second = _ask(rebuilt, "改成3天", tid)
        it = second.get("itinerary") or {}
        assert len(it.get("days", [])) == 3
        assert it.get("change_reason") == "brief_changed"


# ============================================================
# W 组：外部调用失败软降级
# ============================================================
class TestDegradation:
    def test_w1_weather_api_raises_degrades(self, monkeypatch):
        """真实故障点：LBS 层抛错 → fetch_forecast 吞掉返回 None → 节点降级提示。"""
        from backend.travel.experts import weather as W
        from backend.tests.travel.conftest import make_itinerary
        from backend.travel.models.brief import TravelBrief

        itinerary = make_itinerary(brief=TravelBrief(
            destination="测试城", days=1, start_date=date(2026, 9, 25)))
        import backend.infra.lbs.api as lbs_api

        def _boom(*a, **kw):
            raise RuntimeError("lbs down")

        monkeypatch.setattr(lbs_api, "weather_for_city", _boom)
        state = {"brief": itinerary.brief.model_dump(),
                 "itinerary": itinerary.model_dump(), "candidates": []}
        update = W.weather_expert_node(state)
        assert any("天气" in n and "不可用" in n for n in update["notes"])
        assert "itinerary" not in update  # 行程不被天气故障改动

    def test_w2_route_provider_failure_falls_back_to_estimate(self, monkeypatch):
        from backend.evaluation.runners.travel import _ProviderFailure
        from backend.travel.graph_builder import get_travel_graph

        monkeypatch.setattr(T, "TRAVEL_RAG_ENABLED", False)
        monkeypatch.setattr(T, "TRAVEL_PREFS_ENABLED", False)
        with _ProviderFailure(fail_transit=True, fail_poi=False):
            final = get_travel_graph().invoke(
                new_travel_graph_input("厦门2天行程，2个人",
                                       session_id="s-w2", conversation_id="s-w2"),
                config={"recursion_limit": 40, "configurable": {"thread_id": _tid()}})
        legs = [leg for d in final["itinerary"]["days"] for leg in d.get("legs", [])]
        assert legs, "厦门 2 天应有通勤段"
        assert all(leg["source"] == "estimate:local" for leg in legs)

    def test_w3_rag_failure_keeps_plan_alive_graph_level(self, monkeypatch):
        monkeypatch.setattr(T, "TRAVEL_PREFS_ENABLED", False)
        monkeypatch.setattr(T, "TRAVEL_RAG_ENABLED", True)

        class _Boom:
            @staticmethod
            def retrieve_knowledge(*a, **kw):
                raise RuntimeError("pg down")

        import backend.rag.pipeline as P
        monkeypatch.setattr(P, "get_rag_pipeline", lambda: _Boom())
        final = gb.get_travel_graph().invoke(
            new_travel_graph_input("杭州2天行程，1个人",
                                   session_id="s-w3", conversation_id="s-w3"),
            config={"recursion_limit": 40, "configurable": {"thread_id": _tid()}})
        assert final.get("itinerary") is not None
        assert not any(s.startswith("rag:") for s in final["itinerary"]["sources"])
        assert not final.get("knowledge_refs")

    def test_w4_prefs_store_failure_does_not_block_plan(self, memory_graph, monkeypatch):
        from backend.tools.travel import preferences as prefs

        def _boom(*a, **kw):
            raise RuntimeError("pg down")

        monkeypatch.setattr(prefs, "get_preferences", _boom)
        monkeypatch.setattr(prefs, "upsert_preferences", _boom)
        monkeypatch.setattr(T, "TRAVEL_PREFS_ENABLED", True)
        final = _ask(memory_graph, "杭州2天行程，2个人", _tid())
        assert final.get("itinerary") is not None

    def test_w5_far_trip_no_weather_action(self, monkeypatch):
        from backend.travel.experts import weather as W
        from backend.tests.travel.conftest import make_itinerary
        from backend.travel.models.brief import TravelBrief

        calls = {"n": 0}

        def fake_forecast(city):
            calls["n"] += 1
            return ({"days": [{"date": date.today().isoformat(),
                               "day": {"weather": "暴雨"}, "night": {"weather": "晴"}}]}, "")

        monkeypatch.setattr(W, "fetch_forecast", fake_forecast)
        far = date.today() + __import__("datetime").timedelta(days=60)
        itinerary = make_itinerary(brief=TravelBrief(
            destination="测试城", days=1, start_date=far))
        state = {"brief": itinerary.brief.model_dump(),
                 "itinerary": itinerary.model_dump(), "candidates": []}
        update = W.weather_expert_node(state)
        assert calls["n"] == 1  # 查了预报
        assert "itinerary" not in update  # 但远期日期不在预报窗口 → 无替换
        # STOP J5 §43：远期日期与预报窗口零交集 = OUT_OF_HORIZON，必须显式
        # 披露（不再静默），且绝不拿今天的天气伪装 60 天后
        joined = "\n".join(update.get("notes") or [])
        assert "超出天气预报的可信范围" in joined

    def test_w6_bad_weather_without_indoor_candidates(self, monkeypatch):
        from backend.travel.experts import weather as W
        from backend.tests.travel.conftest import make_itinerary, make_poi
        from backend.travel.models.brief import TravelBrief

        today_iso = date.today().isoformat()
        monkeypatch.setattr(W, "fetch_forecast", lambda city: ({
            "days": [{"date": today_iso, "day": {"weather": "大雨"},
                      "night": {"weather": "晴"}}]}, ""))
        outdoor = make_poi(poi_id="p_out", name="登山步道", tags=["自然"])
        itinerary = make_itinerary(brief=TravelBrief(
            destination="测试城", days=1, start_date=date.today()))
        from backend.tests.travel.conftest import make_day, make_item

        from backend.travel.experts.transit import rebuild_days

        itinerary = rebuild_days(itinerary.brief,
                                 [(1, date.today(), [outdoor])])
        state = {"brief": itinerary.brief.model_dump(),
                 "itinerary": itinerary.model_dump(), "candidates": []}
        update = W.weather_expert_node(state)
        assert any("没有可替换的室内地点" in n for n in update["notes"])


# ============================================================
# B 组：输入边界
# ============================================================
class TestBoundary:
    def test_b1_unsupported_city_named(self):
        from backend.travel.slot_filler import build_clarification, extract_brief

        brief = extract_brief("去纽约玩2天")
        ans = build_clarification(brief, "去纽约玩2天")
        assert "纽约" in ans and "暂时无法规划" in ans

    def test_b2_zero_days_asks_not_plans(self):
        from backend.travel.slot_filler import extract_brief

        brief = extract_brief("福州玩0天")
        assert brief.missing_slots()  # 0 天 → 必填缺失 → 走追问

    def test_b3_zero_or_negative_budget_treated_as_absent(self):
        from backend.travel.experts.budget import budget_expert_node
        from backend.tests.travel.conftest import make_itinerary
        from backend.travel.models.brief import TravelBrief

        brief = TravelBrief(destination="测试城", days=1, party_size=1,
                            budget_cny=0)
        it = make_itinerary(brief=brief, days=[])
        state = {"brief": brief.model_dump(), "itinerary": it.model_dump()}
        update = budget_expert_node(state)
        # 预算 0 = 没给预算 → 如实提示未做预算校验，而不是触发删除逻辑
        assert any("未提供预算" in n for n in update["notes"])

    def test_b4_long_and_weird_input_no_crash(self):
        from backend.travel.slot_filler import build_clarification, extract_brief

        weird = "😀" * 200 + "帮我规划行程" + "x" * 1800
        brief = extract_brief(weird)
        assert isinstance(brief.destination, str)
        assert isinstance(build_clarification(brief, weird), str)

    def test_b5_past_date_rolls_to_future(self):
        from backend.travel.slot_filler import extract_start_date

        got = extract_start_date("9月21日福州一日游")
        assert got is not None
        assert got >= date.today()
        assert (got.month, got.day) == (9, 21)

    def test_b6_date_range_becomes_days_not_date_digits(self):
        from backend.travel.slot_filler import extract_brief

        brief = extract_brief("9月21到25日福州玩")
        assert brief.days == 5  # 区间天数，不是把「21日」读成 21 天


# ============================================================
# C 组：并发
# ============================================================
class TestConcurrency:
    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch):
        monkeypatch.setattr(T, "TRAVEL_RAG_ENABLED", False)
        monkeypatch.setattr(T, "TRAVEL_PREFS_ENABLED", False)

    def test_c1_concurrent_sessions_isolated(self):
        """两会话并发规划：各自 thread 隔离，行程互不污染。"""
        from backend.travel.graph_builder import get_travel_graph

        graph = get_travel_graph()

        def run(city: str):
            tid = _tid()
            final = graph.invoke(
                new_travel_graph_input(f"{city}2天行程，2个人",
                                       session_id=tid, conversation_id=tid),
                config={"recursion_limit": 40, "configurable": {"thread_id": tid}})
            return (final["itinerary"]["brief"]["destination"],
                    len(final["itinerary"]["days"]))

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1, f2 = pool.submit(run, "福州"), pool.submit(run, "厦门")
            d1, d2 = f1.result(), f2.result()
        assert d1[0] == "福州" and d2[0] == "厦门"
        assert d1[1] == 2 and d2[1] == 2

    def test_c2_concurrent_pref_upsert_no_deadlock(self, monkeypatch):
        """同 user_id 并发 upsert 偏好：ON CONFLICT 单行写，不抛错不死锁。"""
        from backend.tools.travel import preferences as prefs

        monkeypatch.setattr(T, "TRAVEL_PREFS_ENABLED", True)  # 覆盖 _offline 关闭
        if not prefs._ensure_table():
            pytest.skip("偏好存储不可用（PG 不可达）")
        uid = f"test-conc-{uuid4().hex[:8]}"

        def write(i: int):
            return prefs.upsert_preferences(uid, diet=f"忌口{i}",
                                            preferences=[f"标签{i}"])

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [f.result() for f in
                       [pool.submit(write, i) for i in range(8)]]
        assert all(results)
        saved = prefs.get_preferences(uid)
        assert saved["diet"].startswith("忌口")  # 最后写者胜，无部分写

    def test_c3_concurrent_plans_no_cascade_failure(self):
        """30 并发规划：LBS 未配置走本地估算，全部完成，无异常冒泡。"""
        from backend.travel.graph_builder import get_travel_graph

        graph = get_travel_graph()

        def run(i: int):
            tid = _tid()
            final = graph.invoke(
                new_travel_graph_input("福州2天行程，2个人",
                                       session_id=tid, conversation_id=tid),
                config={"recursion_limit": 40, "configurable": {"thread_id": tid}})
            return final.get("itinerary") is not None

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = [f.result() for f in
                       [pool.submit(run, i) for i in range(30)]]
        assert all(results)

    def test_c4_concurrent_weather_failure_path_stable(self, monkeypatch):
        """天气并发失败路径：LBS 不可用时全部优雅返回 None，不抛错。"""
        from backend.travel.experts import weather as W

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = [f.result() for f in
                       [pool.submit(W.fetch_forecast, "杭州") for _ in range(10)]]
        # 未配置 Key → None（失败软降级）；配置了 Key → 预报 dict。两者都合法，
        # 关键是**不抛异常**且 10 次结果形态一致
        assert len({type(r).__name__ for r in results}) == 1

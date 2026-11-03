"""tests/travel/test_clarification_plan.py — 追问计划层契约（2026-10-08 STOP 3/5）

覆盖验收门禁：
  - P0-09 missing_slots 仍由确定性代码产生（brief 单一事实源）
  - P0-10 ask_slots 由 ClarificationService 规则产生
  - P0-11 一次最多追问 1 个核心槽位
  - P0-12 点击 options 全部可重新路由（文案即话术，重发必回旅游域）
  - P0-16 Reporter 不再二次生成 clarification（消费 state）
"""
from __future__ import annotations

from backend.travel.agents.requirement_agent import (
    extract_days,
    extract_destination,
    extract_fresh_brief,
)
from backend.travel.core.intent import TravelIntent, classify_intent
from backend.travel.models.brief import TravelBrief
from backend.travel.services.clarification_service import (
    CLARIFICATION_PRIORITY,
    ClarificationPlan,
    build_clarification_plan,
    frontend_options,
    render_template,
)


def test_priority_covers_required_slots():
    """优先级表必须覆盖 REQUIRED_SLOTS（新增 required 槽必须同步扩表）。"""
    from backend.travel.models.brief import REQUIRED_SLOTS

    for slot in REQUIRED_SLOTS:
        assert slot in CLARIFICATION_PRIORITY


def test_plan_none_when_ready():
    assert build_clarification_plan(
        TravelBrief(destination="福州", days=3, start_date="2026-11-08")) is None
    # 日期弹性：明确「先不定」后同样就绪（不再追问日期）
    assert build_clarification_plan(
        TravelBrief(destination="福州", days=3, start_date_flexible=True)) is None


def test_plan_asks_single_highest_priority_slot():
    """P0-10/11：missing=[destination,days,start_date] 只问 destination。"""
    plan = build_clarification_plan(TravelBrief())
    assert plan is not None
    assert plan.missing_slots == ["destination", "days", "start_date"]
    assert plan.ask_slots == ["destination"]
    assert plan.allow_free_text is True


def test_plan_asks_date_after_days_resolved():
    """日期为第三必填槽：城市与天数齐备后追问日期，附快捷选项。"""
    plan = build_clarification_plan(TravelBrief(destination="成都", days=2))
    assert plan is not None
    assert plan.ask_slots == ["start_date"]
    labels = [option.label for option in plan.options]
    assert labels == ["今天出发", "明天出发", "这周末出发", "先不定日期"]


def test_date_options_reroute_and_flex():
    """日期 chips 重发可解析（可路由性）、「先不定」可置弹性收口追问。"""
    from backend.travel.agents.requirement_agent import (
        extract_date_flexible,
        extract_fresh_brief,
        extract_start_date,
    )

    plan = build_clarification_plan(TravelBrief(destination="成都", days=2))
    today = extract_start_date("今天出发")
    tomorrow = extract_start_date("明天出发")
    weekend = extract_start_date("这周末出发")
    assert today is not None and tomorrow is not None and weekend is not None
    assert tomorrow > today
    # 「先不定日期」→ 弹性标志置位，追问链收口
    flex_brief = extract_fresh_brief("先不定日期")
    assert flex_brief.start_date_flexible is True
    assert extract_date_flexible("日期没定，随便排") is True
    # 普通无日期消息不误触弹性（那是该追问的正常场景）
    assert extract_date_flexible("帮我规划个行程") is False


def test_plan_asks_days_when_destination_known():
    plan = build_clarification_plan(TravelBrief(destination="福州"))
    assert plan.ask_slots == ["days"]
    assert plan.known_facts.get("destination") == "福州"


def test_plan_missing_matches_brief_single_source():
    """P0-09：计划的 missing 与 brief.missing_slots() 完全一致。"""
    brief = extract_fresh_brief("我想出去玩")
    plan = build_clarification_plan(brief, "我想出去玩")
    assert plan.missing_slots == brief.missing_slots()


def test_days_options_keep_legacy_frontend_contract():
    """days 选项保持存量前端契约（label/days/message 逐字段）。"""
    plan = build_clarification_plan(TravelBrief(destination="丽江"))
    options = frontend_options(plan)
    assert options == [
        {"label": "按 3 天参考规划", "days": 3, "message": "规划丽江3天行程"},
        {"label": "自己填天数", "days": None, "message": ""},
    ]


def test_destination_options_clickable_reroute():
    """destination 选项点击即重发且可回域填槽（P0-12/Golden G）。"""
    plan = build_clarification_plan(TravelBrief())
    options = frontend_options(plan)
    assert options, "destination 追问应有城市 chips"
    for option in options:
        # 前端契约：不带 days 键（undefined ≠ null → 走重发通道）
        assert "days" not in option
        message = option["message"]
        # 重发后必须：命中旅游域规划意图 + 规则能抽出该城市
        intent = classify_intent(message, has_itinerary=False,
                                 has_destination=False)
        fresh = extract_fresh_brief(message)
        if intent is None:
            intent = classify_intent(message, has_itinerary=False,
                                     has_destination=bool(fresh.destination))
        assert intent is TravelIntent.PLAN, message
        assert fresh.destination == option["label"].replace("规划", ""), (
            f"{message} 应抽出城市")


def test_destination_options_cover_nonseed_hot_cities():
    """推荐池迁常量清单（2026-11-03）：chips 含种子外热门城市。

    回归锚点：此前推荐池 = 种子 3 城，欢迎语暗示「只能规划这三个」；
    迁移后 chips 应覆盖种子外城市，且逐个可被 extract_destination 回填
    （可路由性由 test_destination_options_clickable_reroute 逐项锁定）。
    """
    from backend.tools.travel import poi_seed

    plan = build_clarification_plan(TravelBrief())
    options = frontend_options(plan)
    assert len(options) == 6
    seed_cities = set(poi_seed.all_cities())
    chip_cities = {option["label"].replace("规划", "") for option in options}
    assert chip_cities - seed_cities, "chips 应含种子外热门城市"


def test_days_option_message_routes_and_fills_days():
    """days 选项文案重发后 days=3（既有验收路径不回归）。"""
    plan = build_clarification_plan(TravelBrief(destination="丽江"))
    accept = frontend_options(plan)[0]["message"]
    assert "规划丽江3天行程" == accept
    assert extract_days(accept) == 3
    assert extract_destination(accept) == "丽江"


def test_template_render_deterministic_and_single_question():
    plan1 = build_clarification_plan(TravelBrief(), "我想出去玩")
    plan2 = build_clarification_plan(TravelBrief(), "我想出去玩")
    assert render_template(plan1) == render_template(plan2)
    text = render_template(plan1)
    assert "去哪个城市" in text
    assert "玩几天" not in text


def test_template_keeps_unsupported_city_note():
    plan = build_clarification_plan(
        TravelBrief(days=3), "我想去纽约玩",
        unsupported_city="纽约")
    text = render_template(plan)
    assert "纽约" in text and "暂时无法规划" in text


def test_plan_serializable_into_state():
    """Plan 必须可 JSON 序列化（进 checkpoint/state 的前提）。"""
    import json

    plan = build_clarification_plan(
        TravelBrief(preferences=["美食"]), "帮我规划个行程")
    dump = plan.model_dump()
    assert isinstance(json.dumps(dump, ensure_ascii=False), str)
    restored = ClarificationPlan.model_validate(json.loads(
        json.dumps(dump, ensure_ascii=False)))
    assert restored.ask_slots == plan.ask_slots


# ── P0-16 / Golden N：Reporter 单一生成点 ─────────────────────────

def test_reporter_consumes_state_without_regeneration(monkeypatch):
    """同一请求 renderer invocation = 1：reporter 只消费 state。"""
    from backend.travel import reporter
    from backend.travel.services import clarification_renderer as cr
    from backend.travel.slot_filler import slot_filler_node

    render_calls = [0]
    real = cr.render_clarification

    def counting(plan, message):
        render_calls[0] += 1
        return real(plan, message)

    monkeypatch.setattr(cr, "render_clarification", counting)
    result = slot_filler_node({"user_message": "帮我规划福州"})
    assert render_calls[0] == 1
    assert result["clarifications"], "缺槽轮必须有追问产物"

    state = {**result, "user_message": "帮我规划福州"}
    answer = reporter._assemble(state)
    assert answer == result["clarifications"][0]
    # reporter 消费后渲染次数不增长（不二次生成）
    assert render_calls[0] == 1


def test_reporter_template_fallback_without_state(monkeypatch):
    """历史状态缺 clarifications 时纯模板兜底，零 LLM、不崩。"""
    from backend.travel import reporter

    def _boom(*a, **k):
        raise AssertionError("兜底路径不得触碰 LLM")

    from backend.travel.services import clarification_renderer as cr

    monkeypatch.setattr(cr, "_llm_question", _boom)
    answer = reporter._assemble({
        "brief_missing": ["days"],
        "brief": {"destination": "丽江"},
        "user_message": "帮我规划丽江",
    })
    assert "玩几天" in answer


def test_trace_semantics_carries_llm_layer_fields():
    """P0-21/22：trace 语义投影含来源/版本/耗时/候选计数。"""
    from backend.travel.trace_semantics import build_trace_semantics

    semantics = build_trace_semantics({
        "intent": "",
        "slot_parse_source": "rule+llm",
        "slot_llm_meta": {
            "used": True, "status": "ok", "model": "test-model",
            "prompt_version": "travel.slot_enrichment@1",
            "latency_ms": 120, "candidate_count": 2,
            "accepted_count": 1, "rejected_count": 1,
            "fallback_reason": "",
        },
        "clarification_source": "llm",
        "clarification_meta": {
            "source": "llm", "slot": "days",
            "prompt_version": "travel.clarification_renderer@1",
            "latency_ms": 80, "fallback_reason": "",
        },
    }, {"status": "needs_clarification"})
    assert semantics["slot_parse_source"] == "rule+llm"
    assert semantics["slot_llm_used"] == "true"
    assert semantics["slot_llm_model"] == "test-model"
    assert semantics["slot_llm_accepted_count"] == 1
    assert semantics["slot_llm_rejected_count"] == 1
    assert semantics["clarification_source"] == "llm"
    assert semantics["clarification_slot"] == "days"
    # 纯规则轮的缺省投影
    base = build_trace_semantics({"intent": ""}, {})
    assert base["slot_parse_source"] == "rule"
    assert base["slot_llm_used"] == "false"
    assert base["clarification_source"] == ""


def test_plan_can_ask_for_an_unresolved_selected_poi_reference():
    # start_date_flexible=True 对应「用户已声明先不定日期、继续对话改行程」
    # 的真实形态——日期不再追问，选点澄清正常触发。
    plan = build_clarification_plan(
        TravelBrief(destination="福州", days=2, start_date_flexible=True),
        "把这个景点换掉",
        additional_missing=["selected_poi_id"],
    )

    assert plan.missing_slots == ["selected_poi_id"]
    assert plan.ask_slots == ["selected_poi_id"]
    assert "哪个景点" in render_template(plan)

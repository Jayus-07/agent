"""tests/travel/test_requirement_agent_contract.py — Requirement Agent 契约测试（Phase 2）

验证四条冻结边界（用户 Phase 2 校准）：
  1. 自然语言 → TripBrief（与旧路径逐字段 parity）；
  2. 缺失槽检测与追问；
  3. Memory 边界（长期偏好只存稳定字段，一次性 TripBrief 字段绝不泄漏）；
  4. 禁止 LLM（静态扫描 + 无代码接口 + 无配置开关）。
外加 Agent/Service 边界（合并/指纹/变更追踪单一事实源）与意图信号迁移等价。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import backend.travel.slot_filler as slot_filler
from backend.travel.agents import requirement_agent as ra
from backend.travel.agents.requirement_agent import RequirementAgent
from backend.travel.core import intent_signals
from backend.travel.models.brief import TravelBrief
from backend.travel.services import requirement_service as rs
from backend.travel.services.requirement_service import RequirementService

TRAVEL_DIR = Path(ra.__file__).resolve().parent.parent

# parity 抽取词料：覆盖人数/成人儿童/预算/日期区间/必去避雷/住宿/忌口/节奏
_PARITY_MESSAGES = [
    "帮我排杭州2天行程，2个大人1个小孩，预算3000元，必去西湖",
    "9月21到25日去福州玩，带爸妈",
    "厦门3天，不要去鼓浪屿，住在曾厝垵，不吃辣",
    "10月1日出发去福州2天，慢节奏，想去三坊七巷打卡",
    "两个人去杭州，预算两万",
]


def _dump(brief: TravelBrief) -> dict:
    return brief.model_dump()


class TestNLToTripBriefParity:
    """新路径（Agent+Service 组合）与旧路径（slot_filler.extract_brief）逐字段相等。"""

    @pytest.mark.parametrize("message", _PARITY_MESSAGES)
    def test_parity_no_previous(self, message: str):
        legacy = slot_filler.extract_brief(message)
        fresh = RequirementAgent().extract_fresh_brief(message)
        assert _dump(fresh) == _dump(legacy)

    def test_parity_with_previous_merge(self):
        previous = slot_filler.extract_brief("杭州2天，2个大人")
        message = "改成3天，预算5000元"
        legacy = slot_filler.extract_brief(message, previous)
        fresh = RequirementAgent().extract_fresh_brief(
            message, previous.destination)
        merged = RequirementService().merge(previous, fresh)
        assert _dump(merged) == _dump(legacy)

    def test_deterministic(self):
        message = _PARITY_MESSAGES[0]
        assert _dump(RequirementAgent().extract_fresh_brief(message)) == \
            _dump(RequirementAgent().extract_fresh_brief(message))


class TestAdultsChildren:
    """adults/children 拆分（Phase 2 契约，D4 修复），指纹零改动经 party_size 传导。"""

    def test_d4_two_adults(self):
        brief = RequirementAgent().extract_fresh_brief("2个大人去福州玩3天")
        assert brief.adults == 2 and brief.children is None
        assert brief.party_size == 2  # 迁移前这里是 1（D4 缺陷）

    def test_adults_plus_children(self):
        brief = RequirementAgent().extract_fresh_brief("2个大人1个小孩去厦门3天")
        assert (brief.adults, brief.children, brief.party_size) == (2, 1, 3)

    def test_reversed_wording(self):
        brief = RequirementAgent().extract_fresh_brief("大人两位带孩子一个，杭州2天")
        assert brief.adults == 2 and brief.children == 1 and brief.party_size == 3

    def test_explicit_total_wins(self):
        brief = RequirementAgent().extract_fresh_brief("我们5个人，2个大人1个小孩")
        assert (brief.adults, brief.children) == (2, 1)
        assert brief.party_size == 5  # 显式总数优先，成人儿童如实记录

    def test_companion_guess_unaffected(self):
        # 带爸妈（无数字）仍是同伴 guess，不进 adults/children（防重复计数）
        brief = RequirementAgent().extract_fresh_brief("带爸妈去福州玩2天")
        assert brief.adults is None and brief.children is None
        assert brief.party_size == 3
        assert ra.party_size_source("带爸妈去福州玩2天") == "guess"

    def test_children_only_does_not_touch_party_size(self):
        # 仅儿童显式、无成人数：party_size 回落默认（派生规则只挂 adults）
        brief = RequirementAgent().extract_fresh_brief("带1个小孩去杭州")
        assert brief.children == 1 and brief.adults is None
        assert brief.party_size == 1

    def test_merge_across_turns(self):
        previous = RequirementAgent().extract_fresh_brief("2个大人去福州玩3天")
        fresh = RequirementAgent().extract_fresh_brief(
            "再加1个小孩，改4天", previous.destination)
        merged = RequirementService().merge(previous, fresh)
        assert (merged.adults, merged.children, merged.party_size) == (2, 1, 3)
        assert merged.days == 4

    def test_fingerprint_uses_party_size_only(self):
        # 指纹零改动：adults/children 变化必须经 party_size 传导才影响指纹
        from backend.travel.graph_state import brief_fingerprint

        b1 = RequirementAgent().extract_fresh_brief("2个大人去福州玩3天")
        b2 = RequirementAgent().extract_fresh_brief("2个大人去福州玩3天")
        b2.children = 0  # children 显式归零但 adults 不变 → party_size 不变
        assert brief_fingerprint(b1) == brief_fingerprint(b2)
        assert RequirementService().fingerprint(b1) == brief_fingerprint(b1)


class TestMissingSlotDetection:
    def test_missing_slots_and_clarification(self):
        agent = RequirementAgent()
        brief = agent.extract_fresh_brief("我想出去玩")
        missing = agent.detect_missing(brief)
        assert "destination" in missing and "days" in missing
        text = agent.build_clarification(brief, "我想出去玩")
        assert text.startswith("为了把行程排准，还需要确认：")
        assert "1." in text and "2." in text

    def test_unsupported_city_named(self):
        agent = RequirementAgent()
        brief = agent.extract_fresh_brief("想去东京玩3天")
        text = agent.build_clarification(brief, "想去东京玩3天")
        assert "东京" in text and "暂时无法规划" in text

    def test_no_clarification_when_complete(self):
        agent = RequirementAgent()
        brief = agent.extract_fresh_brief("福州3天")
        assert agent.detect_missing(brief) == []
        assert agent.build_clarification(brief, "福州3天") == ""


class TestMemoryBoundary:
    """长期 memory 只存稳定偏好；一次性 TripBrief 字段绝不进入 upsert。"""

    def _run_node(self, monkeypatch, message: str,
                  saved: dict | None = None) -> dict:
        calls: dict = {"get": 0, "upsert": []}

        def fake_get(user_id: str) -> dict:
            calls["get"] += 1
            return saved or {}

        def fake_upsert(user_id: str, **kwargs):
            calls["upsert"].append(kwargs)

        monkeypatch.setattr(
            "backend.tools.travel.preferences.get_preferences", fake_get)
        monkeypatch.setattr(
            "backend.tools.travel.preferences.upsert_preferences", fake_upsert)
        state = {"user_message": message, "user_id": "user_boundary"}
        update = slot_filler.slot_filler_node(state)
        return {"update": update, "calls": calls}

    def test_trip_input_never_writes_long_term_preferences(self, monkeypatch):
        result = self._run_node(
            monkeypatch, "杭州2天，预算3000元，必去西湖，住西湖边")
        upserts = result["calls"]["upsert"]
        assert upserts == []

    def test_prefill_only_on_first_turn(self, monkeypatch):
        result = self._run_node(
            monkeypatch, "再规划一次杭州",
            saved={"preferences": ["美食"], "pace": "relaxed"})
        assert result["calls"]["get"] == 1  # 首轮预填发生
        brief = result["update"]["brief"]
        assert brief["preferences"] == ["美食"]
        assert brief["pace"] == "relaxed"

        # 第二轮（带上一轮 brief）：不再读取历史偏好
        second_state = {
            "user_message": "改成3天",
            "user_id": "user_boundary",
            "brief": result["update"]["brief"],
        }
        calls2 = {"get": 0, "upsert": []}
        monkeypatch.setattr(
            "backend.tools.travel.preferences.get_preferences",
            lambda uid: calls2.update({"get": calls2["get"] + 1}) or {})
        monkeypatch.setattr(
            "backend.tools.travel.preferences.upsert_preferences",
            lambda uid, **kw: calls2["upsert"].append(kw))
        slot_filler.slot_filler_node(second_state)
        assert calls2["get"] == 0  # 非首轮不预填


class TestLLMForbidden:
    """Phase 2 冻结：零 LLM。静态扫描 + 无代码接口 + 无配置开关。"""

    _LLM_CLIENT_PATTERN = re.compile(
        r"^\s*(?:from|import)\s+\S*(langchain|openai|deepseek|anthropic|"
        r"dashscope|httpx|openai_batch)\S*", re.M)

    def test_no_llm_client_imports(self):
        guarded = [
            TRAVEL_DIR / "agents" / "requirement_agent.py",
            TRAVEL_DIR / "services" / "requirement_service.py",
            TRAVEL_DIR / "core" / "intent_signals.py",
            TRAVEL_DIR / "slot_filler.py",
        ]
        for path in guarded:
            source = path.read_text(encoding="utf-8")
            assert not self._LLM_CLIENT_PATTERN.search(source), (
                f"{path.name} 出现 LLM/HTTP 客户端 import——违反 Phase 2 零 LLM 冻结"
            )

    def test_no_llm_code_interface(self):
        # 用户明令：删除 llm_supplement 空方法，不造成误导
        agent = RequirementAgent()
        assert not hasattr(agent, "llm_supplement")
        source = (TRAVEL_DIR / "agents" / "requirement_agent.py").read_text(
            encoding="utf-8")
        assert "def llm_" not in source

    def test_no_llm_config_flag(self):
        from backend.config import travel as T

        assert not hasattr(T, "TRAVEL_REQUIREMENT_LLM_ENABLED")

    def test_extension_note_documented(self):
        # 扩展位只允许以文档形式存在（模块 docstring 说明接入硬约束）
        source = (TRAVEL_DIR / "agents" / "requirement_agent.py").read_text(
            encoding="utf-8")
        assert "LLM 结构化补全的扩展位" in source
        assert "单 run ≤1 次" in source


class TestAgentServiceBoundary:
    def test_fingerprint_single_source(self):
        from backend.travel.graph_state import brief_fingerprint

        brief = RequirementAgent().extract_fresh_brief("杭州2天")
        assert RequirementService().fingerprint(brief) == brief_fingerprint(brief)

    def test_detect_change_first_turn_not_changed(self):
        brief = RequirementAgent().extract_fresh_brief("杭州2天")
        detection = RequirementService().detect_brief_change(None, brief, "")
        assert detection["changed"] is False
        assert detection["new_version"] is None

    def test_detect_change_version_and_fields(self):
        previous = RequirementAgent().extract_fresh_brief("杭州2天")
        changed = RequirementAgent().extract_fresh_brief("杭州3天")
        detection = RequirementService().detect_brief_change(
            previous, changed, RequirementService().fingerprint(previous))
        assert detection["changed"] is True
        assert detection["new_version"] == previous.version + 1
        assert detection["change_reason"] == "brief_changed"
        assert detection["changed_fields"] == ["days"]
        # service 不改 brief——version 递增由调用方执行
        assert changed.version == previous.version

    def test_merge_avoid_beats_must_go(self):
        previous = RequirementAgent().extract_fresh_brief("必去三坊七巷，福州2天")
        fresh = RequirementAgent().extract_fresh_brief("不想去三坊七巷了")
        merged = RequirementService().merge(previous, fresh)
        assert "三坊七巷" not in merged.must_go
        assert any("三坊七巷" in a for a in merged.avoid)

    def test_merge_preserves_unexpressed_fields(self):
        previous = RequirementAgent().extract_fresh_brief("杭州2天，预算3000元")
        fresh = RequirementAgent().extract_fresh_brief("改成3天")
        merged = RequirementService().merge(previous, fresh)
        assert merged.days == 3
        assert merged.budget_cny == 3000  # 未表达的预算保留
        assert merged.destination == "杭州"


class TestIntentSignalsMigration:
    def test_core_path_equivalent_to_legacy(self):
        assert slot_filler.is_cancel_run_query is intent_signals.is_cancel_run_query
        assert slot_filler.is_new_run_query is intent_signals.is_new_run_query

    def test_avoid_patch_stays_in_slot_filler_transitional(self):
        # 过渡归属：is_avoid_patch_query 真身只在 slot_filler（Phase 5 重估）
        assert slot_filler.is_avoid_patch_query("不去鼓浪屿了") is True
        assert not hasattr(intent_signals, "is_avoid_patch_query")

    def test_cancel_precedence_unchanged(self):
        assert not slot_filler.is_avoid_patch_query("取消这次行程规划")
        assert not slot_filler.is_avoid_patch_query("不去厦门了，重新规划杭州两天")


class TestLegacySurfaceComplete:
    """re-export 兼容面完整性：迁移前全部公开符号必须仍可从旧路径解析。"""

    _LEGACY_SYMBOLS = [
        "slot_filler_node", "extract_brief", "merge_brief",
        "build_clarification", "extract_fresh_brief",
        "extract_destination", "extract_days", "extract_days_range",
        "extract_party_size", "party_size_source", "extract_budget",
        "extract_lodging", "extract_start_date", "extract_preferences",
        "extract_diet", "extract_pace", "extract_must_go", "extract_avoid",
        "extract_unsupported_city", "extract_date_range_days",
        "is_new_run_query", "is_cancel_run_query", "is_avoid_patch_query",
        "_RE_DATE_ISO",
    ]

    @pytest.mark.parametrize("name", _LEGACY_SYMBOLS)
    def test_symbol_resolvable(self, name: str):
        assert hasattr(slot_filler, name), f"兼容面缺符号 {name}"

    def test_all_matches_surface(self):
        for name in slot_filler.__all__:
            assert hasattr(slot_filler, name)

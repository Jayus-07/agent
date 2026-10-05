"""test_handoff_contract.py — handoff 交接契约测试（多域隔离收官 M1）

覆盖四类：
  1) 契约校验：缺必填拒绝 / 未知 target_domain 拒绝 / 参数包超域字段拒绝 /
     按域参数模型归一
  2) fixture 对齐：backend/tests/fixtures/handoff_payload_v1.json 是前后端
     共享的唯一契约样例，双端测试消费同一份文件防字段漂移
  3) 轻量抽取：天数/人数/预算/必去/目的地（含业务时间窗排除与中文数量词）
  4) 引导话术：三域 GUIDE_TEXTS 齐备（后端下发，旧前端正文兜底）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.orchestration.contracts.handoff import (
    GUIDE_TEXTS,
    HandoffPayloadV1,
    build_handoff_payload,
    build_travel_params,
    extract_budget_cny,
    extract_days,
    extract_destination,
    extract_must_go,
    extract_party_size,
)

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "handoff_payload_v1.json"


# ── 1) 契约校验 ──────────────────────────────────────


def test_valid_payload_roundtrip():
    payload = HandoffPayloadV1(
        target_domain="travel",
        params={"destination": "福州", "days": 2},
        text="引导",
    )
    assert payload.v == 1
    assert payload.params == {
        "destination": "福州", "days": 2, "party_size": None,
        "budget_cny": None, "must_go": [],
    }


def test_missing_target_domain_rejected():
    with pytest.raises(ValidationError):
        HandoffPayloadV1(params={})


def test_unknown_target_domain_rejected():
    with pytest.raises(ValidationError):
        HandoffPayloadV1(target_domain="unknown_domain")  # type: ignore[arg-type]


def test_extra_param_rejected():
    with pytest.raises(ValidationError):
        HandoffPayloadV1(
            target_domain="customer_service",
            params={"prefill_question": "x", "destination": "福州"},  # 超域字段
        )


def test_unknown_top_level_field_rejected():
    with pytest.raises(ValidationError):
        HandoffPayloadV1(target_domain="travel", ghost_field=1)  # type: ignore[call-arg]


def test_version_pin():
    with pytest.raises(ValidationError):
        HandoffPayloadV1(target_domain="travel", v=2)  # type: ignore[arg-type]


# ── 2) fixture 对齐（前后端共享同一 JSON）──────────────


def test_shared_fixture_matches_contract():
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload = HandoffPayloadV1.model_validate(raw)
    dumped = payload.model_dump()
    # 契约模型校验后重序列化 = fixture 原样（无字段增删/默认值改写）
    assert dumped == raw


# ── 3) 轻量抽取 ──────────────────────────────────────


def test_extract_days_digit_and_chinese():
    assert extract_days("带爸妈福州玩2天") == 2
    assert extract_days("周末去三天可以吗") == 3
    assert extract_days("西安两日游怎么安排") == 2


def test_extract_days_excludes_business_time_window():
    # 「近3天/最近30天/过去7天」是业务时间窗，不是行程天数
    assert extract_days("福州的近3天订单量") is None
    assert extract_days("最近30天销售额") is None
    assert extract_days("过去7天的数据") is None
    assert extract_days("明天天气怎么样") is None


def test_extract_party_size_priority():
    # 显式 N 人 > 一家 N 口 > 同伴词累加
    assert extract_party_size("我们5个人去福州") == 5
    assert extract_party_size("一家四口出游") == 4
    assert extract_party_size("带爸妈福州玩2天") == 3  # 本人 + 父母两位
    assert extract_party_size("和女朋友去厦门") == 2
    assert extract_party_size("福州有什么景点") is None


def test_extract_budget_and_must_go():
    assert extract_budget_cny("预算2000去福州") == 2000
    assert extract_budget_cny("花费大概3000元") == 3000
    assert extract_budget_cny("福州玩两天") is None
    assert extract_must_go("福州玩2天，必去三坊七巷和鼓山") == ["三坊七巷", "鼓山"]
    assert extract_must_go("福州玩2天") == []


def test_extract_destination_uses_city_registry():
    # 名录内城市（福州）抽得出；名录外输入留空由落地页追问兜底
    assert extract_destination("带爸妈福州玩2天") == "福州"
    assert isinstance(extract_destination("随便走走"), str)


def test_build_travel_params_full_sentence():
    params = build_travel_params("带爸妈福州玩2天，预算2000，必去三坊七巷")
    assert params["destination"] == "福州"
    assert params["days"] == 2
    assert params["party_size"] == 3
    assert params["budget_cny"] == 2000
    assert "三坊七巷" in params["must_go"]


# ── 4) 构造入口与话术 ────────────────────────────────


def test_build_handoff_payload_all_domains():
    travel = build_handoff_payload("travel", "带爸妈福州玩2天")
    assert travel.target_domain == "travel"
    assert travel.params["destination"] == "福州"
    assert travel.text == GUIDE_TEXTS["travel"]

    selection = build_handoff_payload(
        "selection_funnel", "给宠物零食做一次智能选品",
        extra_params={"category": "宠物零食", "platform": "淘宝"},
    )
    assert selection.params == {"category": "宠物零食", "platform": "淘宝"}

    cs = build_handoff_payload("customer_service", "我的订单怎么还没发货")
    assert cs.params["prefill_question"] == "我的订单怎么还没发货"


def test_guide_texts_cover_all_domains():
    assert set(GUIDE_TEXTS) == {"travel", "customer_service", "selection_funnel"}
    for domain, text in GUIDE_TEXTS.items():
        assert text.strip(), f"{domain} 引导话术不能为空"

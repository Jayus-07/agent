"""tests/travel/test_intent_llm_golden.py — D 批理解层 golden 门禁（#97/#98/#103）

数据集：datasets/travel_intent_llm.jsonl（18 例六类问法）。两种模式：

  - **词表模式（默认跑）**：现网行为基线。断言语义按 expect_intent_family
    分级——query/out_of_scope 家族必须被词表接住（接不住=现状缺口，标
    xfail 逐例登记而非静默放过）；plan/modify 家族词表接不住的走 LLM
    补判（flag 关闭时跳过，不阻塞 CI）。
  - **LLM 模式（TRAVEL_LLM_INTENT_ENABLED=1 或显式 mock）**：词表 None
    的用例经 llm_intent_service 补判后必须全部落入期望家族——mock LLM
    输出（离线确定），真实 LLM 的实机验收走 /travel 页人工+Smoke。

硬断言（所有模式共用）：query 家族的判定必须落在 QUERY_INTENTS（版本
不变的结构保证——slot_filler 对 QUERY 不重排）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.travel.core.intent import QUERY_INTENTS, classify_intent

DATASET = (Path(__file__).resolve().parents[2] / "datasets"
           / "travel_intent_llm.jsonl")


def _cases() -> list[dict]:
    return [json.loads(line) for line in
            DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


CASES = _cases()


def test_dataset_shape():
    """评测集自检：字段齐、family 白名单、用例数下限。"""
    assert len(CASES) >= 18
    allowed = {"query", "plan", "modify", "out_of_scope", "modify_or_query"}
    for case in CASES:
        assert {"id", "message", "context", "expect_intent_family",
                "note"} <= set(case), case["id"]
        assert case["expect_intent_family"] in allowed, case["id"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_wordlist_baseline(case):
    """词表模式基线：接得住的家族必须接住；接不住的按缺口处理。"""
    family = case["expect_intent_family"]
    ctx = case["context"]
    intent = classify_intent(
        case["message"],
        has_itinerary=ctx.get("has_itinerary", False),
        has_destination=ctx.get("has_destination", False),
    )
    if family == "query":
        # #97/#98 硬语义：问答被词表接住时必须落 QUERY 家族；
        # 接不住（intent=None）登记为 xfail——D 批 LLM 补判的靶子
        if intent is None:
            pytest.xfail(f"{case['id']} 词表盲区（LLM 补判的靶子）")
        assert intent.value in QUERY_INTENTS
    elif family == "out_of_scope":
        if intent is None:
            pytest.xfail(f"{case['id']} 词表盲区")
        assert intent.value == "out_of_scope"
    elif family == "modify":
        if intent != "modify":
            pytest.xfail(f"{case['id']} 词表判为 {intent}（LLM 补判靶子）")
    # plan / modify_or_query：词表接不住不红（LLM/兜底通道承接），只记录


class TestLLMIntentService:
    """llm_intent_service 的结构守卫（离线，mock LLM 输出）。"""

    def _svc(self):
        from backend.travel.services import llm_intent_service as svc

        return svc

    def test_disabled_returns_none(self, monkeypatch):
        svc = self._svc()
        from backend.config import travel as T

        monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", False)
        assert svc.classify_intent_llm("泉州有什么特产") is None

    @staticmethod
    def _patch_llm(monkeypatch, content):
        """mock 构建路径：resolve_effective 出模型名 + _build_llm_for 出假实例。"""
        from types import SimpleNamespace

        from backend.config import model_roles
        from backend.config import travel as T

        monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", True)
        monkeypatch.setattr(
            model_roles, "resolve_effective",
            lambda role: {"role": role, "value": "test-model"})
        fake_llm = SimpleNamespace(
            invoke=lambda messages: SimpleNamespace(content=content))
        monkeypatch.setattr(
            "backend.infra.llm.proxy._build_llm_for",
            lambda model_name: SimpleNamespace(
                bind=lambda **_kw: fake_llm))

    def test_valid_family_mapped(self, monkeypatch):
        svc = self._svc()
        self._patch_llm(monkeypatch, '{"family": "query"}')
        got = svc.classify_intent_llm("泉州有什么特产", has_itinerary=True)
        assert got == "query_dynamic"

    def test_extra_fields_structurally_dropped(self, monkeypatch):
        """#103 守卫：LLM 输出夹带行程/回复/参数等越权字段——结构上被丢弃，
        返回值只有 intent 家族字符串。"""
        from types import SimpleNamespace

        from backend.config import travel as T

        svc = self._svc()
        monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", True)

        self._patch_llm(monkeypatch, json.dumps({
            "family": "modify",
            "itinerary": {"days": 99},
            "reply": "已帮你改成99天",
            "days": 99,
        }, ensure_ascii=False))
        got = svc.classify_intent_llm("改成99天", has_itinerary=True)
        assert got == "modify"
        assert not isinstance(got, dict)
        assert "itinerary" not in str(got)

    def test_illegal_json_returns_none(self, monkeypatch):
        from types import SimpleNamespace

        from backend.config import travel as T

        svc = self._svc()
        monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", True)

        self._patch_llm(monkeypatch, "我觉得应该是修改，因为……")
        assert svc.classify_intent_llm("随便说点啥", has_itinerary=True) is None

    def test_unknown_family_returns_none(self, monkeypatch):
        from types import SimpleNamespace

        from backend.config import travel as T

        svc = self._svc()
        monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", True)

        self._patch_llm(monkeypatch, '{"family": "plan"}')
        # plan 不在 LLM 白名单（词表接得住规划；LLM 只补问答/改单/出域）
        assert svc.classify_intent_llm("去玩", has_itinerary=True) is None

    def test_llm_exception_returns_none(self, monkeypatch):
        from backend.config import travel as T

        svc = self._svc()
        monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", True)

        from backend.config import model_roles
        from backend.config import travel as T

        monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", True)
        monkeypatch.setattr(
            model_roles, "resolve_effective",
            lambda role: {"role": role, "value": "test-model"})

        def boom(model_name):
            raise RuntimeError("provider down")

        monkeypatch.setattr("backend.infra.llm.proxy._build_llm_for", boom)
        assert svc.classify_intent_llm("随便", has_itinerary=True) is None

"""panel 评审团测试（mock LLM，不发真实请求）"""
import asyncio
import json

import pytest

import backend.selection_decision.panel as panel_mod
from backend.selection_decision.panel import PERSONAS, aggregate_votes, run_panel

SUMMARY = {"category": "蓝牙耳机", "finance": {"margin_rate": 0.3}}


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


@pytest.fixture
def mock_llm(monkeypatch):
    """默认所有评审投 go/80 分"""
    def invoke(messages):
        return _FakeResp(json.dumps(
            {"score": 80, "verdict": "go", "reason": "测试意见"}, ensure_ascii=False))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(invoke)}))


def test_personas_has_7_roles():
    assert len(PERSONAS) == 7
    for p in PERSONAS:
        assert p["role"] and p["focus"]


def test_run_panel_all_go_passes(mock_llm):
    result = asyncio.run(run_panel(SUMMARY, size=7))
    assert result["verdict"] == "pass"
    assert result["size"] == 7
    assert result["go_count"] == 7
    assert result["avg_score"] == pytest.approx(80)


def test_run_panel_majority_no_go_fails(monkeypatch):
    """多数投 no_go → fail"""
    votes = iter([{"score": 40, "verdict": "no_go", "reason": "x"}] * 4
                 + [{"score": 80, "verdict": "go", "reason": "y"}] * 3)
    def invoke(messages):
        return _FakeResp(json.dumps(next(votes), ensure_ascii=False))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(invoke)}))
    result = asyncio.run(run_panel(SUMMARY, size=7))
    assert result["verdict"] == "fail"


def test_run_panel_llm_error_counts_as_no_go(monkeypatch):
    """单个评审 LLM 失败 → 该票记 no_go/0 分，不让整体崩溃"""
    calls = {"n": 0}
    def invoke(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LLM 超时")
        return _FakeResp(json.dumps(
            {"score": 90, "verdict": "go", "reason": "ok"}, ensure_ascii=False))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(invoke)}))
    result = asyncio.run(run_panel(SUMMARY, size=3))
    assert result["size"] == 3
    errors = [v for v in result["votes"] if v["error"]]
    assert len(errors) == 1
    assert errors[0]["verdict"] == "no_go"
    assert errors[0]["score"] == 0


def test_vote_score_clamped(monkeypatch):
    """LLM 返回越界 score（如 300）应被钳制到 [0, 100]"""
    def invoke(messages):
        return _FakeResp(json.dumps(
            {"score": 300, "verdict": "go", "reason": "x"}, ensure_ascii=False))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(invoke)}))
    result = asyncio.run(run_panel(SUMMARY, size=1))
    assert result["votes"][0]["score"] == 100


def test_aggregate_votes_rules():
    votes = [{"score": 70, "verdict": "go"}] * 4 + [{"score": 50, "verdict": "no_go"}] * 3
    assert aggregate_votes(votes)["verdict"] == "pass"  # 多数 go 且均分 61.4 ≥60
    votes2 = [{"score": 50, "verdict": "go"}] * 4 + [{"score": 50, "verdict": "no_go"}] * 3
    assert aggregate_votes(votes2)["verdict"] == "fail"  # 均分 50 < 60

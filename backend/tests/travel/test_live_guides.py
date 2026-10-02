"""tests/travel/test_live_guides.py — 旅游攻略检索（知乎官方 MCP）离线单测

**全部不联网**：外部 Tool 调用被替换为固定封套，验证的是
service 解码 / Agent 两路降级 / 触发词三件事。真实链路由实机验收覆盖。

重点：
  1. 攻略是增强信息非规划硬依赖：单路（知乎/全网）失败必须独立降级为
     error 键，绝不上抛拖垮 poi 专家节点——这是与商户/车次「硬失败」
     语义的本质区别
  2. 触发词只在用户明确要攻略内容时消耗配额（5000 次/期）
  3. service 层失败封套必须转 LiveSearchError，不能被当成空结果
"""
from __future__ import annotations

import json

import pytest

from backend.travel.agents.research_agent import ResearchAgent
from backend.travel.experts.poi import _live_queries
from backend.travel.services import live_search_service as LIVE
from backend.travel.services.live_search_service import LiveSearchError


def _success_envelope(results: list[dict]) -> str:
    return json.dumps({"status": "success", "data": {"results": results}},
                      ensure_ascii=False)


def _guide(title: str) -> dict:
    return {"title": title, "url": f"https://zhihu.com/{title}",
            "summary": f"{title}的摘要"}


@pytest.fixture
def fake_tools(monkeypatch):
    """拦截 service 层的 Tool 直调：记录参数并返回预置封套。"""
    calls: list[tuple[str, dict]] = []
    responses: dict[str, list] = {"zhihu": [], "web": []}

    def _make(name):
        class _FakeTool:  # 只需满足 _invoke 的 .func 访问
            pass

        def _func(**kwargs):
            calls.append((name, dict(kwargs)))
            if not responses[name]:
                raise AssertionError(f"{name} 未预置响应")
            item = responses[name].pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        tool = _FakeTool()
        tool.func = _func
        return tool

    monkeypatch.setattr(LIVE, "zhihu_search_tool", _make("zhihu"))
    monkeypatch.setattr(LIVE, "global_search_tool", _make("web"))
    return calls, responses


class TestServiceLayer:
    def test_query_template_and_limit_passthrough(self, fake_tools):
        calls, responses = fake_tools
        responses["zhihu"].append(_success_envelope([_guide("攻略一")]))
        data = LIVE.search_zhihu_guides(destination="泉州", limit=4)
        assert calls[0][1] == {"query": "泉州 旅游 美食 攻略", "count": 4}
        assert data["results"][0]["title"] == "攻略一"

    def test_failure_envelope_raises_live_search_error(self, fake_tools):
        _, responses = fake_tools
        responses["zhihu"].append(json.dumps(
            {"status": "failed", "error": "知乎搜索未启用"}, ensure_ascii=False))
        with pytest.raises(LiveSearchError, match="未启用"):
            LIVE.search_zhihu_guides(destination="泉州")

    def test_unparseable_envelope_raises(self, fake_tools):
        _, responses = fake_tools
        responses["web"].append("不是 JSON")
        with pytest.raises(LiveSearchError, match="无法解析"):
            LIVE.search_web_guides(destination="泉州")

    def test_preview_shape(self):
        preview = LIVE.guides_preview(
            {"results": [_guide("a"), _guide("b")]}, source="zhihu")
        assert preview["category"] == "guide"
        assert preview["result_count"] == 2
        assert preview["provider"] == "zhihu_mcp"
        assert len(preview["preview"]) == 2


class TestAgentDegradation:
    def test_single_lane_failure_degrades_independently(self, monkeypatch):
        """一路挂不能拖垮另一路：失败路降级为 error 键（增强信息语义）。"""
        monkeypatch.setattr(
            LIVE, "search_zhihu_guides",
            lambda *, destination, limit=4: {"results": [_guide("知乎攻略")]})
        monkeypatch.setattr(
            LIVE, "search_web_guides",
            lambda *, destination, limit=4: (_ for _ in ()).throw(
                LiveSearchError("全网搜索未启用")))
        data = ResearchAgent().search_guides("泉州")
        assert data["zhihu"]["results"][0]["title"] == "知乎攻略"
        assert "未启用" in data["web"]["error"]

    def test_both_lanes_fail_still_returns_dict(self, monkeypatch):
        monkeypatch.setattr(
            LIVE, "search_zhihu_guides",
            lambda *, destination, limit=4: (_ for _ in ()).throw(
                LiveSearchError("a")))
        monkeypatch.setattr(
            LIVE, "search_web_guides",
            lambda *, destination, limit=4: (_ for _ in ()).throw(
                LiveSearchError("b")))
        data = ResearchAgent().search_guides("泉州")
        assert "a" in data["zhihu"]["error"] and "b" in data["web"]["error"]

    def test_event_summary_merges_and_marks_unavailable(self):
        agent = ResearchAgent()
        ok = agent.guide_event_summary({
            "zhihu": {"results": [_guide("a"), _guide("b")]},
            "web": {"error": "未启用"},
        })
        assert ok["result_count"] == 2
        assert ok["zhihu_status"] == "available"
        assert ok["web_status"] == "unavailable"
        assert [p["title"] for p in ok["preview"]] == ["a", "b"]
        assert ok["data_status"] == "available"


class TestTriggerWords:
    @pytest.mark.parametrize("text", [
        "泉州有什么必吃的", "帮我做一份厦门美食攻略", "当地特色美食有哪些",
        "推荐几家小吃", "泉州美食指南",
    ])
    def test_guide_triggered(self, text):
        _, _, guide = _live_queries(text)
        assert guide is True

    @pytest.mark.parametrize("text", ["规划一个两天的行程", "查美食", "查酒店"])
    def test_guide_not_triggered_without_intent(self, text):
        _, _, guide = _live_queries(text)
        assert guide is False

    def test_food_and_hotel_triggers_unchanged(self):
        food, hotel, guide = _live_queries("查美食和查酒店，顺便看看攻略")
        assert food and hotel and guide

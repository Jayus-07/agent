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

    def test_topic_query_templates(self, fake_tools):
        """2026-10-03 多主题：topic → 专属检索词；空 topic 走旧模板。"""
        calls, responses = fake_tools
        responses["zhihu"].extend([
            _success_envelope([_guide("景点帖")]),
            _success_envelope([_guide("美食帖")]),
        ])
        LIVE.search_zhihu_guides(destination="泉州", topic="attraction")
        LIVE.search_zhihu_guides(destination="泉州", topic="food")
        assert calls[0][1]["query"] == "泉州 旅游 景点 攻略"
        assert calls[1][1]["query"] == "泉州 美食 特色 必吃"

    def test_preview_tags_topic(self):
        preview = LIVE.guides_preview(
            {"results": [_guide("a")]}, source="zhihu", topic="city")
        assert preview["preview"][0]["topic"] == "city"

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
            lambda *, destination, limit=4, topic="": {"results": [_guide("知乎攻略")]})
        monkeypatch.setattr(
            LIVE, "search_web_guides",
            lambda *, destination, limit=4, topic="": (_ for _ in ()).throw(
                LiveSearchError("全网搜索未启用")))
        data = ResearchAgent().search_guides("泉州")
        assert data["zhihu"]["results"][0]["title"] == "知乎攻略"
        assert "未启用" in data["web"]["error"]

    def test_both_lanes_fail_still_returns_dict(self, monkeypatch):
        monkeypatch.setattr(
            LIVE, "search_zhihu_guides",
            lambda *, destination, limit=4, topic="": (_ for _ in ()).throw(
                LiveSearchError("a")))
        monkeypatch.setattr(
            LIVE, "search_web_guides",
            lambda *, destination, limit=4, topic="": (_ for _ in ()).throw(
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

    def test_topics_all_queried_and_interleaved(self, monkeypatch):
        """2026-10-03 规划自动检索：景点/美食/城市特色三主题各查一次，
        结果轮转交错（preview 截断后三主题均衡可见），条目带 topic 标记。"""
        seen_topics: list[str] = []

        def _fake_zhihu(*, destination, limit=4, topic=""):
            seen_topics.append(topic)
            return {"results": [_guide(f"{topic}-攻略")]}

        monkeypatch.setattr(LIVE, "search_zhihu_guides", _fake_zhihu)
        monkeypatch.setattr(
            LIVE, "search_web_guides",
            lambda *, destination, limit=4, topic="": {"results": []})
        data = ResearchAgent().search_guides("泉州")
        assert sorted(seen_topics) == sorted(LIVE.GUIDE_TOPIC_QUERIES)
        topics = [item["topic"] for item in data["zhihu"]["results"]]
        assert topics[0] != topics[1]  # 交错：开头不是同一主题连排
        assert set(topics) == set(LIVE.GUIDE_TOPIC_QUERIES)

    def test_match_guide_mentions(self):
        """知乎攻略提及匹配：POI 全名出现在标题/摘要 → 理由；未提及不编造。"""
        from backend.travel.models.poi import Poi

        def _poi(pid: str, name: str) -> Poi:
            return Poi(poi_id=pid, name=name, city="泉州", lat=24.9, lng=118.6)

        guides = {
            "zhihu": {"results": [
                {**_guide("开元寺深度游"), "topic": "attraction"},
                {**_guide("西街小吃清单"), "topic": "food"},
            ]},
            "web": {"error": "未启用"},
        }
        hits = ResearchAgent.match_guide_mentions(
            [_poi("a", "开元寺"), _poi("b", "清净寺"), _poi("c", "西街")], guides)
        assert "知乎攻略《开元寺深度游》提及" == hits["a"]
        assert "b" not in hits  # 攻略没提就是没提，不补造
        # 括号后缀不影响匹配
        hits2 = ResearchAgent.match_guide_mentions(
            [_poi("d", "西街（泉州老城区）")], guides)
        assert "d" in hits2


class TestLiveQueries:
    @pytest.mark.parametrize("text", ["查美食", "搜餐厅", "附近美食"])
    def test_food_triggered(self, text):
        food, hotel = _live_queries(text)
        assert food and not hotel

    def test_food_triggered_by_preference(self):
        """2026-10-03：美食偏好自动触发商户检索（吃走推荐卡，不进行程）。"""
        food, hotel = _live_queries("做个两天的行程", ["美食"])
        assert food and not hotel

    def test_hotel_triggered(self):
        food, hotel = _live_queries("查酒店", [])
        assert hotel and not food

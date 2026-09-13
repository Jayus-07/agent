"""tests/tools/test_competitor_tool.py — Competitor Tools 测试套件

覆盖:
1. Registry 注册验证（4 个单职责 Tool）
2. URL 提取逻辑
3. 各 Tool 的参数验证与响应格式
4. Skill 层 _select_tool 分发
"""
import pytest
from unittest.mock import patch


class TestCompetitorToolRegistry:
    """四个单职责 Tool 的注册验证"""

    def test_all_tools_registered(self):
        from backend.tools.tool_registry import tool_registry

        for name in ("competitor_analyze_tool", "competitor_watch_tool",
                     "competitor_history_tool", "competitor_watchlist_tool"):
            assert name in tool_registry.tool_names, f"{name} 未注册"

    def test_no_duplicate_definition(self):
        from backend.tools.tool_registry import tool_registry

        duplicates = tool_registry.check_duplicates()
        competitor_dups = [d for d in duplicates if d.startswith("competitor_")]
        assert not competitor_dups, f"检测到重复定义：{competitor_dups}"

    def test_tools_registered_once(self):
        from backend.tools.tool_registry import tool_registry

        for name in ("competitor_analyze_tool", "competitor_watch_tool",
                     "competitor_history_tool", "competitor_watchlist_tool"):
            sources = tool_registry._tool_sources.get(name, [])
            assert len(sources) == 1, f"{name} 定义了 {len(sources)} 次"


class TestUrlExtraction:
    """URL 提取逻辑测试"""

    def test_extract_url_from_question(self):
        """从自然语言中提取 URL"""
        from backend.tools.competitor import _extract_url

        question = "请分析这个产品：https://item.jd.com/12345.html"
        url = _extract_url(question)

        assert "jd.com" in url or "item.jd.com" in url

    def test_extract_url_from_simple_string(self):
        """从纯字符串中提取 URL"""
        from backend.tools.competitor import _extract_url

        url = _extract_url("https://www.example.com/product")

        assert url == "https://www.example.com/product"

    def test_no_url_returns_empty(self):
        """无 URL 时返回空字符串"""
        from backend.tools.competitor import _extract_url

        result = _extract_url("这是一个没有 URL 的问题")

        assert result == ""


class TestAnalyzeTool:
    """competitor_analyze_tool（单职责：分析单个 URL）"""

    def test_missing_url_returns_hint(self):
        """无 URL 时返回可读提示"""
        from backend.tools.competitor import competitor_analyze_tool

        result = competitor_analyze_tool.invoke({"url": ""})

        assert isinstance(result, str)
        assert "URL" in result or "请提供" in result


class TestWatchTool:
    """competitor_watch_tool（单职责：巡检监控列表）"""

    def test_watch_invokes_scan(self):
        with patch('backend.tools.competitor.scan_watchlist',
                   return_value="监控列表为空"):
            from backend.tools.competitor import competitor_watch_tool

            result = competitor_watch_tool.invoke({})
            assert result == "监控列表为空"


class TestWatchlistTool:
    """competitor_watchlist_tool（单职责：监控列表管理）"""

    def test_list_action_without_url(self):
        """list action 不需要 URL"""
        from backend.tools.competitor import competitor_watchlist_tool

        result = competitor_watchlist_tool.invoke({"action": "list"})

        assert isinstance(result, str)

    def test_unknown_action_returns_hint(self):
        """未知 action 应返回提示"""
        from backend.tools.competitor import competitor_watchlist_tool

        result = competitor_watchlist_tool.invoke({"action": "unknown_action"})

        assert isinstance(result, str)
        assert "未知 action" in result


class TestSelectToolDispatch:
    """Skill 层 _select_tool：capability/action → Tool 分发"""

    def _dispatch(self, capability, params):
        from backend.skills.competitor_analysis.skill import CompetitorAnalysisSkill

        tool, filtered = CompetitorAnalysisSkill()._select_tool(capability, params)
        return tool.name, filtered

    def test_capability_watch(self):
        name, filtered = self._dispatch("competitor.watch", {"question": "巡检"})
        assert name == "competitor_watch_tool"

    def test_capability_history(self):
        name, _ = self._dispatch("competitor.history", {"url": "https://x.com"})
        assert name == "competitor_history_tool"

    def test_action_add_routes_to_watchlist(self):
        """Planner 用 analyze+action=add 表达写操作 → 分发到 watchlist tool"""
        name, filtered = self._dispatch(
            "competitor.analyze",
            {"action": "add", "url": "https://item.jd.com/1.html", "name": "竞品A"},
        )
        assert name == "competitor_watchlist_tool"
        # 参数过滤到目标 Tool 签名内（question 等无关键被剔除）
        assert set(filtered) == {"action", "url", "name"}

    def test_default_analyze_filters_params(self):
        name, filtered = self._dispatch(
            "competitor.analyze",
            {"question": "分析这个", "action": "analyze"},
        )
        assert name == "competitor_analyze_tool"
        assert "action" not in filtered


# ==================== 测试套件入口 ====================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

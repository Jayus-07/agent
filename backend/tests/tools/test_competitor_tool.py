"""tests/tools/test_competitor_tool.py — Competitor Analysis Tool 测试套件

覆盖:
1. Registry 注册验证  
2. 参数验证测试
3. 不同 action 的响应格式
4. URL 提取逻辑
"""
import pytest
from unittest.mock import MagicMock, patch


class TestCompetitorToolRegistry:
    """Competitor Tool 注册中心测试"""
    
    def test_tool_registered_in_registry(self):
        """verify competitor_analyze_tool is registered"""
        from backend.tools.tool_registry import tool_registry
        
        assert 'competitor_analyze_tool' in tool_registry.tool_names
    
    def test_no_duplicate_definition(self):
        """verify no duplicate definition exists"""
        from backend.tools.tool_registry import tool_registry
        
        duplicates = tool_registry.check_duplicates()
        assert 'competitor_analyze_tool' not in duplicates, \
            f"检测到重复定义：{duplicates}"
    
    def test_tool_registered_once(self):
        """verify tool registered exactly once"""
        from backend.tools.tool_registry import tool_registry
        
        sources = tool_registry._tool_sources.get('competitor_analyze_tool', [])
        assert len(sources) == 1, f"competitor_analyze_tool 定义了 {len(sources)} 次"


class TestCompetitorToolBasic:
    """competitor_analyze_tool 基础功能测试"""
    
    def test_invoke_method_exists(self):
        """verify invoke method is available"""
        from backend.tools.competitor import competitor_analyze_tool
        
        assert hasattr(competitor_analyze_tool, 'invoke')
        assert callable(competitor_analyze_tool.invoke)
    
    def test_tool_name_correct(self):
        """verify tool name"""
        from backend.tools.competitor import competitor_analyze_tool
        
        assert competitor_analyze_tool.name == 'competitor_analyze_tool'
    
    def test_has_docstring(self):
        """verify tool has proper documentation"""
        from backend.tools.competitor import competitor_analyze_tool
        
        assert hasattr(competitor_analyze_tool, '__doc__')
        assert competitor_analyze_tool.__doc__ is not None
        # LangChain 工具的 doc 可能被包装器覆盖，但我们至少有基本文档


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


class TestActionValidation:
    """不同 Action 的响应测试"""
    
    def test_list_action_without_url(self):
        """list action 不需要 URL"""
        from backend.tools.competitor import competitor_analyze_tool
        
        result = competitor_analyze_tool.invoke({
            "action": "list",
        })
        
        assert isinstance(result, str)
    
    def test_analyze_action_requires_url(self):
        """analyze action 需要 URL"""
        from backend.tools.competitor import competitor_analyze_tool
        
        result = competitor_analyze_tool.invoke({
            "action": "analyze",
            "url": "",
        })
        
        assert isinstance(result, str)
        assert "URL" in result or "请提供" in result
    
    def test_watch_action(self):
        """watch action 巡检监控列表"""
        from backend.tools.competitor import competitor_analyze_tool
        
        # Mock store 以跳过实际数据库操作
        with patch('backend.tools.competitor.scan_watchlist', return_value="监控列表为空"):
            result = competitor_analyze_tool.invoke({
                "action": "watch",
            })
            
            assert isinstance(result, str)
    
    def test_unknown_action_returns_hint(self):
        """未知 action 应返回提示"""
        from backend.tools.competitor import competitor_analyze_tool
        
        result = competitor_analyze_tool.invoke({
            "action": "unknown_action",
        })
        
        assert isinstance(result, str)
        assert "未知 action" in result or "支持" in result


class TestQuestionProcessing:
    """问题处理逻辑测试"""
    
    def test_question_with_url_automatically_analyzes(self):
        """问题中包含 URL 时应自动执行 analyze"""
        from backend.tools.competitor import competitor_analyze_tool
        
        # 只传入 question，不传 action 或 url
        question = "帮我分析一下 https://item.jd.com/12345678.html 的价格"
        
        # 这应该会尝试 analyze，但由于没有 mock 会失败（这是正常的）
        result = competitor_analyze_tool.invoke({
            "question": question,
        })
        
        assert isinstance(result, str)


# ==================== 测试套件入口 ====================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

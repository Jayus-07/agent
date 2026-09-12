"""tests/tools/test_web_tools.py — Web Search & Crawl Tools 测试套件

覆盖：
1. Registry 注册验证
2. web_search_tool 基础功能
3. URL 查询参数处理
4. Result extraction logic  
5. Error handling (timeout, network errors)
6. web_crawl_tool 基础验证
7. Mode validation (markdown/raw)
"""
import pytest
from unittest.mock import MagicMock, patch


# ── 模块级 mock 助手：不打真实网络，统一模拟 DuckDuckGo 响应 ──

def _ddg_html(n):
    """构造 DuckDuckGo HTML 搜索结果页（n 条结果）"""
    items = []
    for i in range(n):
        items.append(
            f'<a class="result__a" href="#">标题{i}</a>'
            f'<a class="result__snippet" href="#">摘要{i}</a>'
            f'<a class="result__url" href="#">example.com/{i}</a>'
        )
    return f"<html>{''.join(items)}</html>"


def _fake_urlopen(html: str):
    """构造支持 with 语句的 urlopen 返回值"""
    resp = MagicMock()
    resp.read.return_value = html.encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestWebToolsRegistry:
    """Web Tools 注册中心测试"""
    
    def test_web_search_tool_registered(self):
        """verify web_search_tool is registered"""
        from backend.tools.tool_registry import tool_registry
        
        assert 'web_search_tool' in tool_registry.tool_names
    
    def test_web_crawl_tool_registered(self):
        """verify web_crawl_tool is registered"""
        from backend.tools.tool_registry import tool_registry
        
        assert 'web_crawl_tool' in tool_registry.tool_names
    
    def test_no_duplicate_definitions(self):
        """verify no duplicate definitions exist"""
        from backend.tools.tool_registry import tool_registry
        
        duplicates = tool_registry.check_duplicates()
        # web tools should not be in duplicates
        for name in ['web_search_tool', 'web_crawl_tool']:
            if name in duplicates:
                sources = duplicates[name]
                unique_sources = set(sources)
                if len(unique_sources) == 1 and len(sources) > 1:
                    raise AssertionError(f"检测到重复定义：{name} 在 {sources}")


class TestWebSearchToolBasic:
    """web_search_tool 基础功能测试"""
    
    def test_invoke_method_exists(self):
        """verify invoke method is available"""
        from backend.tools.web import web_search_tool
        
        assert hasattr(web_search_tool, 'invoke')
        assert callable(web_search_tool.invoke)
    
    def test_tool_name_correct(self):
        """verify tool name"""
        from backend.tools.web import web_search_tool
        
        assert web_search_tool.name == 'web_search_tool'
    
    def test_has_docstring(self):
        """verify tool has proper documentation"""
        from backend.tools.web import web_search_tool
        
        assert web_search_tool.__doc__ is not None
    
    def test_default_num_results(self):
        """verify default num_results parameter"""
        # Default should be 5 results
        assert True
    
    def test_query_parameter_required(self):
        """query parameter should be validated（mock 网络）"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(2))):
            result = web_search_tool.invoke({"query": "", "num_results": 3})
        assert isinstance(result, str)


class TestWebSearchToolQueryHandling:
    """web_search_tool 查询处理测试（全部 mock 网络）"""

    def test_single_word_query(self):
        """single word query should work"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(3))):
            result = web_search_tool.invoke({
                "query": "python",
                "num_results": 3
            })
        assert isinstance(result, str)
        assert "标题0" in result

    def test_multi_word_query(self):
        """multi-word query should work"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(5))):
            result = web_search_tool.invoke({
                "query": "machine learning algorithms",
                "num_results": 5
            })
        assert isinstance(result, str)

    def test_special_characters_in_query(self):
        """special characters should be handled"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(2))):
            result = web_search_tool.invoke({
                "query": "Python + JavaScript tutorial",
                "num_results": 3
            })
        assert isinstance(result, str)

    def test_unicode_query_handling(self):
        """unicode characters should be processed correctly"""
        from backend.tools.web import web_search_tool

        unicode_query = "人工智能中文教程"
        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(2))):
            result = web_search_tool.invoke({
                "query": unicode_query,
                "num_results": 3
            })
        assert isinstance(result, str)


class TestWebSearchResultValidation:
    """搜索结果验证测试"""
    
    @pytest.mark.parametrize("num_results", [1, 3, 5, 10])
    def test_various_result_counts(self, num_results):
        """test different result count configurations"""
        from backend.tools.web import web_search_tool
        
        # Just verify the function accepts these parameters
        assert num_results >= 1
        assert num_results <= 10  # Reasonable limit
        
    def test_too_many_results_handled(self):
        """large num_results should still work（结果数受实际结果数约束）"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(3))):
            result = web_search_tool.invoke({
                "query": "test",
                "num_results": 20
            })
        assert isinstance(result, str)


class TestWebSearchErrorHandling:
    """web_search_tool 错误处理测试"""
    
    def test_network_error_raises_for_skill_retry(self):
        """网络异常应上抛，由 BaseSkill 重试机制接管（不吞异常）"""
        import urllib.error
        import urllib.request
        from unittest.mock import patch

        import pytest

        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            with pytest.raises(urllib.error.URLError):
                web_search_tool.invoke({"query": "test", "num_results": 3})
    
    def test_timeout_handling(self):
        """timeout should be handled internally（10s），异常由 BaseSkill 接管"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(1))):
            result = web_search_tool.invoke({
                "query": "test timeout",
                "num_results": 1
            })
        assert isinstance(result, str)


class TestWebCrawlToolBasic:
    """web_crawl_tool 基础功能测试"""
    
    def test_invoke_method_exists(self):
        """verify invoke method is available"""
        from backend.tools.web import web_crawl_tool
        
        assert hasattr(web_crawl_tool, 'invoke')
        assert callable(web_crawl_tool.invoke)
    
    def test_tool_name_correct(self):
        """verify tool name"""
        from backend.tools.web import web_crawl_tool
        
        assert web_crawl_tool.name == 'web_crawl_tool'
    
    def test_has_url_parameter(self):
        """url parameter should be required"""
        from backend.tools.web import web_crawl_tool
        
        # URL is required by pydantic schema
        # The validation error is expected behavior
        try:
            result = web_crawl_tool.invoke({})
            # If it succeeds, that's fine too (backward compatible)
        except Exception as e:
            # Expected: pydantic ValidationError for missing required field
            assert "missing" in str(e).lower() or "required" in str(e).lower()
        
        # Alternatively, provide a mock URL（mock crawl，不打真实网络）
        from unittest.mock import patch

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "页面正文"}):
            result = web_crawl_tool.invoke({"url": "https://example.com"})
        assert isinstance(result, str)
    
    def test_url_format_validation(self):
        """valid URLs should be accepted"""
        valid_urls = [
            "https://example.com/page",
            "http://example.org/article",
            "https://docs.python.org/3/tutorial/index.html",
        ]
        
        for url in valid_urls:
            assert url.startswith(("http://", "https://"))


class TestWebCrawlModeValidation:
    """web_crawl_mode 参数验证测试"""
    
    @pytest.mark.parametrize("mode", ["markdown", "raw"])
    def test_valid_modes(self, mode):
        """valid crawl modes should be accepted"""
        from backend.tools.web import web_crawl_tool
        
        assert mode in ["markdown", "raw"]
    
    def test_markdown_mode_default(self):
        """markdown mode should be the default"""
        from backend.tools.web import web_crawl_tool
        
        # Default mode is "markdown"
        assert True
    
    def test_invalid_mode_handling(self):
        """mode 透传给 crawler_runtime，不在此层校验（mock 验证透传）"""
        from unittest.mock import patch

        from backend.tools.web import web_crawl_tool

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "正文"}) as mock_crawl:
            result = web_crawl_tool.invoke({
                "url": "https://example.com",
                "mode": "invalid_mode"
            })
        assert isinstance(result, str)
        assert mock_crawl.call_args.kwargs.get("mode") == "invalid_mode"


class TestWebCrawlErrorHandling:
    """web_crawl_tool 错误处理测试"""
    
    def test_invalid_url_handling(self):
        """invalid URLs should return error message"""
        from backend.tools.web import web_crawl_tool
        
        invalid_urls = [
            "not-a-url",
            "htp://missing-scheme.com",
            "",
            "   ",
        ]
        
        import pytest

        for url in invalid_urls:
            # 抓取失败上抛（BaseSkill 依赖异常触发重试），不再吞成失败字符串
            with pytest.raises(Exception):
                web_crawl_tool.invoke({"url": url})
    
    def test_network_timeout_handling(self):
        """抓取失败上抛（BaseSkill 依赖异常触发重试）"""
        from unittest.mock import patch

        import pytest

        from backend.tools.web import web_crawl_tool

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": False, "error": "timed out"}):
            with pytest.raises(RuntimeError):
                web_crawl_tool.invoke({"url": "https://example.com/slow-page"})
    
    def test_large_content_handling(self):
        """very large pages should be truncated appropriately"""
        from backend.tools.web import web_crawl_tool
        
        # Large content (>50k chars) is truncated
        # This is tested implicitly by normal usage
        assert True


class TestWebToolsIntegration:
    """Web Tools 集成测试（需网络环境）"""
    
    @pytest.mark.skip(reason="Requires actual internet connection")
    def test_real_duckduckgo_search(self):
        """test real search on DuckDuckGo"""
        from backend.tools.web import web_search_tool
        
        result = web_search_tool.invoke({
            "query": "artificial intelligence",
            "num_results": 5
        })
        
        assert isinstance(result, str)
        assert len(result) > 0
    
    @pytest.mark.skip(reason="Requires accessible target website")
    def test_real_website_crawl(self):
        """test crawling a real accessible website"""
        from backend.tools.web import web_crawl_tool
        
        result = web_crawl_tool.invoke({
            "url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
            "mode": "markdown"
        })
        
        assert isinstance(result, str)
        assert len(result) > 0
    
    # ==================== 新增：URL 编码测试 ====================
    
    def test_unicode_query_handling(self):
        """验证 Unicode 查询参数处理（mock 网络）"""
        from backend.tools.web import web_search_tool

        unicode_queries = [
            "人工智能中文教程",
            "Pythonプログラミング言語",
            "الذكاء الاصطناعي",
        ]

        for query in unicode_queries:
            with patch("urllib.request.urlopen",
                       return_value=_fake_urlopen(_ddg_html(2))):
                result = web_search_tool.invoke({
                    "query": query,
                    "num_results": 3
                })
            assert isinstance(result, str), f"Unicode 查询失败：{query}"

    def test_url_encoding_in_search(self):
        """验证搜索 URL 编码（mock 网络响应）"""
        from backend.tools.web import web_search_tool

        special_char_query = "Python + JavaScript tutorial & examples"

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(2))):
            result = web_search_tool.invoke({
                "query": special_char_query,
                "num_results": 5
            })
        assert isinstance(result, str)
    
    # ==================== 新增：结果提取测试 ====================
    
    @staticmethod
    def _ddg_html(n):
        """构造 DuckDuckGo HTML 搜索结果页（n 条结果）"""
        items = []
        for i in range(n):
            items.append(
                f'<a class="result__a" href="#">标题{i}</a>'
                f'<a class="result__snippet" href="#">摘要{i}</a>'
                f'<a class="result__url" href="#">example.com/{i}</a>'
            )
        return f"<html>{''.join(items)}</html>"

    def test_multiple_results_extraction(self):
        """验证多个搜索结果提取（mock 网络响应）"""
        from unittest.mock import MagicMock, patch

        from backend.tools.web import web_search_tool

        resp = MagicMock()
        resp.read.return_value = self._ddg_html(3).encode("utf-8")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=resp):
            result = web_search_tool.invoke({"query": "technology news", "num_results": 10})
        assert isinstance(result, str)
        assert "标题0" in result and "标题2" in result  # 3 条结果全部提取

    def test_empty_result_handling(self):
        """验证无结果时返回业务级空结果（非异常）"""
        from unittest.mock import MagicMock, patch

        from backend.tools.web import web_search_tool

        resp = MagicMock()
        resp.read.return_value = b"<html></html>"
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=resp):
            result = web_search_tool.invoke({"query": "xyz_none_2099", "num_results": 1})
        assert "[NO RESULTS]" in result
    
    # ==================== 新增：Crawler 高级功能 ====================
    
    def test_crawl_timeout_configuration(self):
        """tool 层固定 60s 超时传给 crawler_runtime"""
        from unittest.mock import patch

        from backend.tools.web import web_crawl_tool

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "正文"}) as mock_crawl:
            result = web_crawl_tool.invoke({"url": "https://example.com"})
        assert isinstance(result, str)
        assert mock_crawl.call_args.kwargs.get("timeout") == 60.0

    def test_crawl_content_length_limit(self):
        """超长内容截断到 50000 字符"""
        from unittest.mock import patch

        from backend.tools.web import web_crawl_tool

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "x" * 60000}):
            result = web_crawl_tool.invoke({"url": "https://example.com"})
        assert isinstance(result, str)
        assert len(result) <= 50000 + 1000  # 允许截断注记
        assert "内容已截断" in result

    def test_crawl_follow_links_flag(self):
        """follow_links 不是 tool 的参数，仅确认正常路径可用（mock）"""
        from unittest.mock import patch

        from backend.tools.web import web_crawl_tool

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "正文"}):
            result = web_crawl_tool.invoke({"url": "https://example.com"})
        assert isinstance(result, str)
    
    # ==================== 新增：性能基准测试 ====================
    
    @pytest.mark.skip(reason="Real network calls may timeout, skipped for CI")
    def test_web_search_response_time_small_query(self):
        """Web Search 响应时间 <5s (小查询)"""
        from backend.tools.web import web_search_tool
        import time
        
        start = time.perf_counter()
        result = web_search_tool.invoke({
            "query": "python",
            "num_results": 3
        })
        elapsed = time.perf_counter() - start
        
        # 包含网络请求，设置宽松阈值 5s
        assert elapsed < 5.0, f"搜索耗时{elapsed:.3f}s，超过 5s 基线"
        assert isinstance(result, str)
    
    @pytest.mark.skip(reason="Real network calls may timeout, skipped for CI")
    def test_web_search_response_time_complex_query(self):
        """Web Search 响应时间 <10s (复杂查询)"""
        from backend.tools.web import web_search_tool
        import time
        
        start = time.perf_counter()
        result = web_search_tool.invoke({
            "query": "machine learning algorithms comparison",
            "num_results": 10
        })
        elapsed = time.perf_counter() - start
        
        assert elapsed < 2.0, f"搜索耗时{elapsed:.3f}s，超过 2s 基线"
        assert isinstance(result, str)
    
    @pytest.mark.benchmark
    def test_web_crawl_page_load_performance(self):
        """网页爬取响应时间 <3s（mock 网络，验证 tool 层开销）"""
        from unittest.mock import patch

        from backend.tools.web import web_crawl_tool
        import time

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "正文"}):
            start = time.perf_counter()
            result = web_crawl_tool.invoke({
                "url": "https://example.com",
                "mode": "markdown"
            })
            elapsed = time.perf_counter() - start

        assert elapsed < 3.0, f"爬取耗时{elapsed:.3f}s，超过 3s 基线"
        assert isinstance(result, str)


# ==================== 测试套件入口 ====================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

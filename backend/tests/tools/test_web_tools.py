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
        """缺省 num_results 时按默认 5 条截断（mock 网络供 10 条）"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(10))):
            result = web_search_tool.invoke({"query": "default"})
        assert "标题4" in result
        assert "标题5" not in result
    
    def test_empty_query_passed_through_to_search_url(self):
        """空 query 当前无校验、原样透传进搜索 URL（q= 为空）——固化真实契约"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(2))) as mock_urlopen:
            result = web_search_tool.invoke({"query": "", "num_results": 3})
        assert isinstance(result, str)
        req = mock_urlopen.call_args.args[0]
        assert str(req.full_url).endswith("q="), \
            f"空 query 应原样透传进 URL，实际: {req.full_url}"


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
        """special characters should be percent-encoded into the search URL"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(2))) as mock_urlopen:
            result = web_search_tool.invoke({
                "query": "Python + JavaScript tutorial",
                "num_results": 3
            })
        assert isinstance(result, str)
        url = str(mock_urlopen.call_args.args[0].full_url)
        assert "%2B" in url, f"+ 必须被编码为 %2B，实际: {url}"
        assert " " not in url, f"URL 中不允许出现裸空格，实际: {url}"

    def test_unicode_query_handling(self):
        """unicode characters should be percent-encoded into the search URL"""
        from backend.tools.web import web_search_tool

        unicode_query = "人工智能中文教程"
        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(2))) as mock_urlopen:
            result = web_search_tool.invoke({
                "query": unicode_query,
                "num_results": 3
            })
        assert isinstance(result, str)
        url = str(mock_urlopen.call_args.args[0].full_url)
        assert "%" in url, f"中文必须被 percent-encode，实际: {url}"
        assert unicode_query not in url, f"URL 中不应出现未编码中文，实际: {url}"


class TestWebSearchResultValidation:
    """搜索结果验证测试"""
    
    @pytest.mark.parametrize("num_results", [1, 3, 5, 10])
    def test_various_result_counts(self, num_results):
        """不同 num_results 真实截断结果数（mock 网络固定供 10 条）"""
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(10))):
            result = web_search_tool.invoke({
                "query": "test", "num_results": num_results})
        assert f"标题{num_results - 1}" in result
        if num_results < 10:
            assert f"标题{num_results}" not in result
        
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
        """两路搜索都 socket.timeout → 上抛保持 BaseSkill 重试语义（同 URLError 契约）"""
        import socket
        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   side_effect=socket.timeout("timed out")):
            with pytest.raises(socket.timeout):
                web_search_tool.invoke({
                    "query": "test timeout",
                    "num_results": 1
                })


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
        """url 为必填参数：缺参必须被 pydantic schema 拒绝；合法路径 mock 透传"""
        from pydantic import ValidationError

        from backend.tools.web import web_crawl_tool

        with pytest.raises(ValidationError):
            web_crawl_tool.invoke({})

        from unittest.mock import patch

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "页面正文"}):
            result = web_crawl_tool.invoke({"url": "https://example.com"})
        assert isinstance(result, str)
    
    def test_url_format_validation(self):
        """合法 URL 应透传给 crawler 并返回正文（mock 抓取）"""
        from backend.tools.web import web_crawl_tool

        valid_urls = [
            "https://example.com/page",
            "http://example.org/article",
            "https://docs.python.org/3/tutorial/index.html",
        ]

        for url in valid_urls:
            with patch("backend.tools.crawler_runtime.crawl",
                       return_value={"ok": True, "content": "页面正文"}) as mock_crawl:
                result = web_crawl_tool.invoke({"url": url})
            assert mock_crawl.call_args.args[0] == url
            assert result == "页面正文"


class TestWebCrawlModeValidation:
    """web_crawl_mode 参数验证测试"""
    
    @pytest.mark.parametrize("mode", ["markdown", "raw"])
    def test_valid_modes(self, mode):
        """合法 mode 应透传给 crawler_runtime（mock 抓取）"""
        from backend.tools.web import web_crawl_tool

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "正文"}) as mock_crawl:
            result = web_crawl_tool.invoke({
                "url": "https://example.com", "mode": mode})
        assert result == "正文"
        assert mock_crawl.call_args.kwargs.get("mode") == mode
    
    def test_markdown_mode_default(self):
        """缺省 mode 应默认 markdown 透传给 crawler_runtime"""
        from backend.tools.web import web_crawl_tool

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": True, "content": "正文"}) as mock_crawl:
            web_crawl_tool.invoke({"url": "https://example.com"})
        assert mock_crawl.call_args.kwargs.get("mode") == "markdown"
    
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
    
class TestWebToolsIntegration:
    """Web Tools 集成测试（全部 mock 网络；真实网络用例已删除——等价 mock 覆盖见上文各类）"""

    # ==================== 新增：URL 编码测试 ====================

    def test_unicode_query_handling(self):
        """多语言 query 均被 percent-encode 后进入搜索 URL（mock 网络）"""
        from backend.tools.web import web_search_tool

        unicode_queries = [
            "人工智能中文教程",
            "Pythonプログラミング言語",
            "الذكاء الاصطناعي",
        ]

        for query in unicode_queries:
            with patch("urllib.request.urlopen",
                       return_value=_fake_urlopen(_ddg_html(2))) as mock_urlopen:
                result = web_search_tool.invoke({
                    "query": query,
                    "num_results": 3
                })
            assert isinstance(result, str), f"Unicode 查询失败：{query}"
            url = str(mock_urlopen.call_args.args[0].full_url)
            assert "%" in url, f"{query} 必须被 percent-encode，实际: {url}"
            assert query not in url, f"URL 中不应出现未编码原文，实际: {url}"

    def test_url_encoding_in_search(self):
        """query 中的 & 等保留字符被编码，不破坏 URL 参数结构"""
        from backend.tools.web import web_search_tool

        special_char_query = "Python + JavaScript tutorial & examples"

        with patch("urllib.request.urlopen",
                   return_value=_fake_urlopen(_ddg_html(2))) as mock_urlopen:
            result = web_search_tool.invoke({
                "query": special_char_query,
                "num_results": 5
            })
        assert isinstance(result, str)
        url = str(mock_urlopen.call_args.args[0].full_url)
        assert "%26" in url, f"& 必须被编码为 %26，实际: {url}"
        assert "examples" in url, "普通词不应被误编码"
    
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

    def test_bing_fallback_when_ddg_empty(self):
        """DDG 返回空/bot-challenge 页时自动兜底 Bing（2026-09-15 实测 DDG 202 挑战）"""
        from unittest.mock import MagicMock, patch

        from backend.tools.web import web_search_tool

        ddg_challenge = MagicMock()  # 202 bot-challenge：200 状态但无可解析结果
        ddg_challenge.read.return_value = b'<html><body>challenge</body></html>'
        ddg_challenge.__enter__ = MagicMock(return_value=ddg_challenge)
        ddg_challenge.__exit__ = MagicMock(return_value=False)
        bing_html = MagicMock()
        bing_html.read.return_value = (
            '<html><li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;&amp;'
            'u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9hcnRpY2xl">市场规模报告</a></h2>'
            '<p>2025年市场规模1200亿元</p></li></html>').encode()
        bing_html.__enter__ = MagicMock(return_value=bing_html)
        bing_html.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen",
                   side_effect=[ddg_challenge, bing_html]):
            result = web_search_tool.invoke({"query": "耳机市场", "num_results": 3})
        assert "市场规模报告" in result
        assert "https://example.com/article" in result  # ck/a 跳转已解码 + &amp; 实体已处理
        assert "1200亿元" in result

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
    
    # 真实网络的搜索响应时间基准已删除：恒 skip 死代码（且原 docstring 阈值
    # 与断言自相矛盾），mock 版基准见 test_web_crawl_page_load_performance。

    
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

"""Web 工具 — 搜索 + 网页抓取。"""
from langchain_core.tools import tool
from backend.shared.logger import logger

def _extract_table_from_markdown(text: str) -> tuple:
    """从 Markdown 表格文本中提取 rows 和 columns。"""
    import re
    lines = text.strip().split("\n")
    header = []
    data = []
    in_table = False
    for line in lines:
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if all(c.startswith("---") or c.startswith(":--") for c in cells if c):
                continue  # 分隔行
            if not in_table:
                header = cells
                in_table = True
            else:
                data.append(cells)
        else:
            if in_table and data:
                break  # 表格结束
    return data, header


@tool
def web_search_tool(query: str, num_results: int = 5) -> str:
    """
    搜索外部网页，补充知识库未覆盖的信息。
    query: 搜索关键词
    num_results: 返回结果数（默认 5）
    返回: Markdown 格式的搜索结果摘要
    """
    import urllib.request, urllib.parse, json
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
            self.skip = False
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self.skip = True
        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self.skip = False
        def handle_data(self, data):
            if not self.skip:
                t = data.strip()
                if t:
                    self.text.append(t)

    results = []
    try:
        # DuckDuckGo HTML search (no API key needed)
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # 简单正则提取结果标题+摘要
        import re
        titles = re.findall(r'<a[^>]*class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
        snippets = re.findall(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        links = re.findall(r'<a[^>]*class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)

        for i in range(min(num_results, len(titles))):
            title = re.sub(r'<[^>]+>', '', titles[i]).strip()
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
            link = links[i].strip() if i < len(links) else ""
            results.append(f"{i+1}. **{title}**\n   {snippet}\n   {link}")

    except Exception as e:
        logger.warning(f"[Tool:web_search] 搜索失败: {e}")
        return f"[SEARCH FAILED] 无法搜索 '{query}': {e}"

    if not results:
        return f"[NO RESULTS] 未找到 '{query}' 的相关结果"

    return "\n\n".join(results)


@tool
def web_crawl_tool(url: str, mode: str = "markdown") -> str:
    """
    抓取指定网页的正文内容，返回干净的 Markdown 格式文本。
    适用场景：竞品页面分析、行业资讯摘要、平台政策原文获取。
    建议先通过 web.search 发现目标链接，再用本 tool 抓取正文。

    url: 要抓取的网页地址（完整 URL，如 https://www.amazon.com/dp/B0EXAMPLE）
    mode: "markdown" (默认，干净 Markdown) | "raw" (原始 HTML)
    返回: Markdown 格式的网页正文
    """
    from backend.tools.crawler_runtime import crawl
    from backend.shared.logger import logger

    try:
        result = crawl(url, mode=mode, timeout=60.0)
        if not result["ok"]:
            logger.warning(f"[Tool:web_crawl] 抓取失败: {result['error']}")
            return f"[CRAWL FAILED] 无法抓取 '{url}': {result['error']}"
        text = result["content"]
        # 50000 字符上限: 电商商品页（亚马逊等）正文通常 50-300KB，
        # 前段是导航/面包屑，商品数据（价格/评价/规格）在中后段。
        # 8000 字符截断会导致 pipeline 无法提取到价格等关键字段。
        if len(text) > 50000:
            text = text[:50000] + f"\n\n... (内容已截断，原文共 {len(text)} 字符)"
        logger.info(f"[Tool:web_crawl] 成功抓取 {url} ({len(text)} 字符, mode={mode})")
        return text
    except Exception as e:
        logger.warning(f"[Tool:web_crawl] 抓取失败：{e}")
        return f"[CRAWL FAILED] 无法抓取 '{url}': {e}"


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(web_search_tool, __file__)
tool_registry.register(web_crawl_tool, __file__)


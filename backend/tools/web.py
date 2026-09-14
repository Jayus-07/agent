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
    搜索源策略: DuckDuckGo 优先，无结果或不可达时自动兜底 Bing
    （2026-09-15 实测 DDG 对本机返回 202 bot-challenge，Bing 可用）
    """
    errors: list[str] = []
    results: list[str] = []

    try:
        results = _search_duckduckgo(query, num_results)
    except Exception as e:
        errors.append(f"duckduckgo: {e}")
        logger.warning(f"[Tool:web_search] DuckDuckGo 失败: {e}")

    if not results:
        try:
            results = _search_bing(query, num_results)
        except Exception as e:
            errors.append(f"bing: {e}")
            logger.warning(f"[Tool:web_search] Bing 兜底失败: {e}")
            if not results:
                # 两路搜索都网络失败 → 上抛保持 BaseSkill 重试语义
                raise

    if not results:
        return f"[NO RESULTS] 未找到 '{query}' 的相关结果"

    return "\n\n".join(results)


def _strip_tags(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html).strip()


def _search_duckduckgo(query: str, num_results: int) -> list[str]:
    """DuckDuckGo HTML 搜索（原实现抽出的函数，逻辑不变）。"""
    import re
    import urllib.parse
    import urllib.request

    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    titles = re.findall(r'<a[^>]*class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    links = re.findall(r'<a[^>]*class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)

    results = []
    for i in range(min(num_results, len(titles))):
        title = _strip_tags(titles[i]).strip()
        snippet = _strip_tags(snippets[i]).strip() if i < len(snippets) else ""
        link = links[i].strip() if i < len(links) else ""
        results.append(f"{i+1}. **{title}**\n   {snippet}\n   {link}")
    return results


def _decode_bing_redirect(href: str) -> str:
    """解码 Bing 的 /ck/a?u=a1<base64url> 跳转链接；非跳转链接原样返回。"""
    import base64
    import html as _html
    from urllib.parse import parse_qs, urlparse

    href = _html.unescape(href)  # Bing href 里的 &amp; 实体会截断 query 参数
    if "bing.com/ck/a" not in href:
        return href
    try:
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            u = u[2:]
        u += "=" * (-len(u) % 4)
        return base64.urlsafe_b64decode(u).decode("utf-8", errors="replace") or href
    except Exception:
        return href


def _search_bing(query: str, num_results: int) -> list[str]:
    """Bing 网页搜索兜底（解析 b_algo 结果块，返回与 DDG 相同的 Markdown 格式）。"""
    import re
    import urllib.parse
    import urllib.request

    url = (f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
           f"&count={max(num_results, 10)}&setlang=zh-hans")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    results = []
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.DOTALL):
        if len(results) >= num_results:
            break
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not m:
            continue
        link = _decode_bing_redirect(m.group(1))
        title = _strip_tags(m.group(2))
        if not title or not link.startswith(("http://", "https://")):
            continue
        ps = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        snippet = _strip_tags(ps.group(1)) if ps else ""
        results.append(f"{len(results)+1}. **{title}**\n   {snippet}\n   {link}")
    return results


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
            # 抓取失败上抛给 BaseSkill 重试；业务级空内容不属于此路径
            logger.warning(f"[Tool:web_crawl] 抓取失败: {result['error']}")
            raise RuntimeError(f"无法抓取 '{url}': {result['error']}")
        text = result["content"]
        # 50000 字符上限: 电商商品页（亚马逊等）正文通常 50-300KB，
        # 前段是导航/面包屑，商品数据（价格/评价/规格）在中后段。
        # 8000 字符截断会导致 pipeline 无法提取到价格等关键字段。
        if len(text) > 50000:
            text = text[:50000] + f"\n\n... (内容已截断，原文共 {len(text)} 字符)"
        logger.info(f"[Tool:web_crawl] 成功抓取 {url} ({len(text)} 字符, mode={mode})")
        return text
    except Exception as e:
        # 上抛给 BaseSkill：网络失败可重试，吞掉会绕过 Skill 层重试机制
        logger.warning(f"[Tool:web_crawl] 抓取失败：{e}")
        raise


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(web_search_tool, __file__)
tool_registry.register(web_crawl_tool, __file__)


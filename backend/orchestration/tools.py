"""
tools.py — LangChain Tool 封装（零侵入接入已有子系统）

将 sql_agent / retrieval / report_agent 包装为标准 Tool 对象。
Skill 通过 Tool.invoke() 调用，不直接依赖子系统的内部实现。

Skill → Tool → Infrastructure (RAG / SQL / Report)
"""

from contextvars import ContextVar

from langchain_core.tools import tool

# 当前会话 ID，由 LangGraph workflow 在每次请求时设置
_current_session_id: ContextVar[str] = ContextVar("session_id", default="multi-agent-default")


def set_session_id(sid: str):
    _current_session_id.set(sid)


def _get_session_id() -> str:
    return _current_session_id.get()

from backend.shared.logger import logger

# =====================================================
# 懒加载单例（首次调用时初始化，避免启动时全部加载）
# =====================================================

_sql_agent = None


def _get_sql_agent():
    global _sql_agent
    if _sql_agent is None:
        from backend.config import DB_CONFIG
        from backend.sql.sql_agent import init_sql_agent
        _sql_agent = init_sql_agent(dict(DB_CONFIG), max_retries=2)
    return _sql_agent


def _get_rag_pipeline():
    """获取 RAG Pipeline 单例（统一入口，避免双重初始化）"""
    from backend.app.api.deps import get_rag_pipeline
    return get_rag_pipeline()


# =====================================================
# Tool 定义
# =====================================================

@tool
def execute_sql_tool(query: str) -> str:
    """
    直接执行原始 SQL 查询 PostgreSQL。
    输入 SQL SELECT 语句，返回 JSON 格式的查询结果。
    适用场景：Workflow step 中的确定性数据拉取（不经过 NL→SQL Agent）。
    """
    import json as _json
    import psycopg2
    from backend.config import DB_CONFIG

    logger.info(f"[Tool:execute_sql] {query[:80]}...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description] if cur.description else []
        result = [dict(zip(cols, row)) for row in rows]
        cur.close()
        conn.close()
        logger.info(f"[Tool:execute_sql] 返回 {len(result)} 行")
        return _json.dumps({"rows": result, "total": len(result)}, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"[Tool:execute_sql] 失败: {e}")
        raise


@tool
def sql_query_tool(question: str) -> str:
    """
    查询 PostgreSQL 数据库中的结构化数据。
    输入自然语言问题，返回 Markdown 格式的查询结果表格。
    适用场景：数据统计、排行、筛选、聚合、对比分析。
    """
    logger.info(f"[Tool:sql_query] 问题: {question[:80]}...")
    agent = _get_sql_agent()
    return agent.ask(question, current_user_id=None)


@tool
def search_knowledge_tool(question: str, kb_id: str = "default") -> str:
    """
    从指定知识库检索文档内容、经验、最佳实践等。
    输入检索问题和知识库ID，返回基于相关文档生成的回答。
    适用场景：概念解释、经验查询、流程规范、技术方案参考。
    """
    logger.info(f"[Tool:search_knowledge] 检索: {question[:80]}... (kb={kb_id})")
    pipeline = _get_rag_pipeline()
    # 尝试从 LangGraph state 取 session_id，fallback 到默认值
    sid = _get_session_id() if callable(_get_session_id) else "multi-agent-default"
    return pipeline.ask(question, session_id=sid, kb_id=kb_id)


# =====================================================
# 报告生成（Tool + 公共函数）
# =====================================================

def run_report(report_type: str, filters: dict = None, *,
               user_id: str = "default", polish: bool = True) -> str:
    """生成业务报告的统一入口（API route 和 Agent tool 共用）。

    Args:
        report_type: 报告类型，如 monthly_sales / inventory_health
        filters: 筛选条件
        user_id: 用户标识（用于偏好学习）
        polish: 是否启用 LLM 语言润色

    Returns:
        Markdown 格式报告
    """
    from backend.business_report.report_generator import generate_report
    return generate_report(report_type, filters or {}, user_id=user_id, polish=polish)


@tool
def generate_report_tool(report_type: str, filters: dict = None) -> str:
    """
    生成结构化 Markdown 报告（含图表）。
    报告类型需是已注册的类型。
    适用场景：需要输出的正式报告、数据分析汇总。
    """
    filters = filters or {}
    logger.info(f"[Tool:generate_report] 类型={report_type}, 筛选={filters}")
    return run_report(report_type, filters, user_id="multi-agent", polish=False)


@tool
def export_csv_tool(question: str, filename: str = "") -> str:
    """
    查询数据库并导出结果为 CSV 文件（UTF-8 BOM，Excel 兼容打开）。
    question: 自然语言查询问题（如 "上周各渠道销售额"）
    filename: 导出文件名（不含扩展名），默认自动生成
    返回: 导出文件路径和行数
    """
    import csv
    from pathlib import Path
    from datetime import datetime
    from backend.config import STORAGE_DOCS_DIR

    # 委托 SQL agent 生成并执行 SQL
    agent = _get_sql_agent()
    result = agent.ask(question, current_user_id=None)

    # 从 SQL agent 结果中提取表格数据
    rows, columns = _extract_table_from_markdown(result)
    if not rows:
        return f"[EXPORT FAILED] 查询无结果或无法解析: {question[:80]}"

    if not filename:
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    export_dir = Path(STORAGE_DOCS_DIR) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    filepath = export_dir / f"{filename}.csv"

    with open(str(filepath), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    logger.info(f"[Tool:export_csv] {len(rows)} 行 → {filepath}")
    return f"已导出 {len(rows)} 行数据到 {filepath}"


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
    import asyncio
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    from backend.shared.logger import logger

    async def _crawl(url: str, mode: str) -> str:
        config = CrawlerRunConfig(
            page_timeout=30_000,
            magic=True,            # 反机器人检测
            override_navigator=True,
        )
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url, config=config)
            if result is None:
                return f"[CRAWL FAILED] 无法抓取 '{url}'：无响应"
            if result.error_message:
                return f"[CRAWL FAILED] 抓取 '{url}' 出错: {result.error_message}"
            content = result.markdown if mode == "markdown" else result.html
            if not content or len(str(content).strip()) == 0:
                return f"[EMPTY] 网页 '{url}' 无有效正文内容"
            text = str(content)
            if len(text) > 8000:
                text = text[:8000] + f"\n\n... (内容已截断，原文共 {len(text)} 字符)"
            logger.info(f"[Tool:web_crawl] 成功抓取 {url} ({len(text)} 字符, mode={mode})")
            return text

    try:
        return asyncio.run(_crawl(url, mode))
    except Exception as e:
        logger.warning(f"[Tool:web_crawl] 抓取失败: {e}")
        return f"[CRAWL FAILED] 无法抓取 '{url}': {e}"


@tool
def send_email_tool(to: str, subject: str, body: str, cc: str = "") -> str:
    """
    发送邮件。
    to: 收件人邮箱，多个用逗号分隔
    subject: 邮件主题
    body: 邮件正文（支持 Markdown）
    cc: 抄送（可选）
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from backend.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM

    if not SMTP_USER or not SMTP_PASSWORD:
        return f"[EMAIL DISABLED] 未配置 SMTP。收件人: {to}, 主题: {subject}, 正文长度: {len(body)} 字符"

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_FROM
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.attach(MIMEText(body, "html" if body.startswith("<") else "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            recipients = [a.strip() for a in to.split(",")]
            if cc:
                recipients += [a.strip() for a in cc.split(",")]
            server.sendmail(SMTP_FROM, recipients, msg.as_string())

        logger.info(f"[Tool:send_email] 已发送 → {to} ({subject})")
        return f"邮件已发送: 收件人 {to}, 主题 '{subject}'"
    except Exception as e:
        logger.error(f"[Tool:send_email] 发送失败: {e}")
        raise

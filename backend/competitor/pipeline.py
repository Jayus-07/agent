"""competitor/pipeline.py — 竞品分析管线

analyze_url:    抓取单个竞品 URL → 结构化抽取 → 快照入库 → 与上次快照对比
scan_watchlist: 扫描全部启用的监控项（定时任务 / "帮我巡检竞品" 用）

防封闸门（单账号无代理场景，见 anti_ban.py）:
  robots 合规检查 → 限流/熔断准入 → 拟人参数抓取 → 风控反馈（一票停）
"""
from datetime import datetime
from typing import Any

from backend.competitor import anti_ban
from backend.competitor.adapters import detect_platform
from backend.competitor.extractor import extract_fields
from backend.competitor.store import get_store
from backend.shared.logger import logger

_MARKDOWN_LIMIT = 50000  # 与 web_crawl_tool 一致的上限

# 登录/验证页特征关键词（至少命中 2 个才判定为登录重定向）
_LOGIN_INDICATORS = (
    "密码登录", "短信登录", "扫码登录", "忘记密码", "免费注册",
    "登录页面", "请登录", "login.taobao.com", "passport.taobao.com",
    "支付宝登录", "钉钉登录", "已阅读并同意",
)

# IP 限制/风控页特征关键词（至少命中 1 个 + 内容 <5KB 才判定）
_BLOCKED_INDICATORS = (
    "risk_handler", "privatedomain", "访问受限", "访问被拒绝",
    "too many requests", "rate limit", "forbidden",
    "请求过于频繁", "your ip", "ip address", "ip地址",
    "暂时限制", "稍后再试", "unusual traffic",
)


_CURRENCY_SYMBOLS = {"CNY": "¥", "USD": "$", "GBP": "£", "EUR": "€"}


def _fmt_price(v, currency: str = "CNY") -> str:
    if v is None:
        return "未知"
    symbol = _CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    return f"{symbol}{v:,.2f}"


def _is_login_page(markdown: str) -> bool:
    """检测抓取内容是否为登录/验证重定向页（非商品正文）。"""
    head = markdown[:3000]
    hits = sum(1 for kw in _LOGIN_INDICATORS if kw in head)
    return hits >= 2


def _is_blocked_page(markdown: str) -> bool:
    """检测抓取内容是否为 IP 限制/风控页（非商品正文）。

    启发式: 被拦截页通常很短（<5KB），内容 >5KB 基本是真实页面。
    """
    if len(markdown) > 5_000:
        return False
    head = markdown[:3000].lower()
    return any(kw in head for kw in _BLOCKED_INDICATORS)


def _compare_with_previous(snap_id: int, url: str, price, currency: str = "CNY") -> str:
    """与上一次快照对比价格，返回人类可读的变化描述"""
    if price is None:
        return "本次未识别到价格，无法对比"
    store = get_store()
    prev = store.latest_snapshot(url, before_id=snap_id)
    if not prev or prev.get("price") is None:
        return "首次抓取，暂无历史可对比"
    diff = price - prev["price"]
    pct = diff / prev["price"] * 100 if prev["price"] else 0
    if abs(diff) < 0.01:
        return f"价格持平（{_fmt_price(price, currency)}）"
    arrow = "降价" if diff < 0 else "涨价"
    return (f"⚠️ {arrow}: {_fmt_price(prev['price'], currency)} → "
            f"{_fmt_price(price, currency)}（{diff:+,.2f}，{pct:+.1f}%）")


def analyze_url(url: str, name: str = "", use_llm: bool = True) -> str:
    """完整分析一个竞品页面，返回 Markdown 结果

    防封闸门顺序: robots 合规 → 限流/熔断准入 → 拟人抓取 → 风控反馈。
    闸门拒绝时抛 anti_ban.AntiBanError（scan_watchlist 捕获后跳过并如实汇报）。
    """
    from backend.tools.crawler_runtime import crawl

    store = get_store()
    platform = detect_platform(url)

    # 0a. 合规闸门: robots.txt Disallow 的路径直接跳过
    if not anti_ban.robots_allowed(url):
        return (
            f"## 竞品分析跳过: robots.txt 禁止\n\n{url}\n\n"
            f"该路径被平台 robots.txt 禁止抓取，按合规策略已跳过（事件已记录）。"
        )

    # 0b. 限流闸门: 全局预算/平台配额/熔断检查 + 随机间隔等待（含指数退避）
    anti_ban.acquire(platform)

    # 1. 抓取（直连 crawler_runtime 以携带拟人参数；带 Cookie 注入）
    logger.info(f"[Competitor:pipeline] 抓取 {url} (platform={platform})")
    result = crawl(url, mode="markdown", timeout=90.0,
                   config_overrides=anti_ban.humanize())
    if not result["ok"]:
        anti_ban.report_failure(platform, result["error"])
        return f"## 竞品分析失败\n\n{url}\n\n抓取失败: {result['error']}"
    markdown = result["content"]
    if len(markdown) > _MARKDOWN_LIMIT:
        markdown = markdown[:_MARKDOWN_LIMIT]

    # 1b. 登录页检测 — 淘宝/天猫等平台会重定向未登录访客到登录页
    if _is_login_page(markdown):
        logger.warning(f"[Competitor:pipeline] {url} 被重定向到登录页，跳过抽取")
        anti_ban.report_login_redirect(platform, url)
        # 保存 login_blocked 标记快照，支撑前端状态展示
        watch = store.get_watch_by_url(url)
        store.save_snapshot({
            "watchlist_id": watch["id"] if watch else None,
            "url": url,
            "platform": platform,
            "extract_method": "login_blocked",
            "raw_excerpt": markdown[:2000],
            "in_stock": 0,
        })
        return (
            f"## 竞品分析失败: 登录拦截\n\n"
            f"{url}\n\n"
            f"⚠️ 该页面需要登录才能查看商品详情。\n\n"
            f"**原因**: {platform} 平台将未登录的爬虫请求重定向到登录页，"
            f"导致无法获取商品数据（价格/评价/库存等）。\n\n"
            f"**解决方案**:\n"
            f"1. 在「竞品监控」页面点击「Cookie 配置」按钮，粘贴浏览器 Cookie\n"
            f"2. 或设置环境变量 `CRAWLER_COOKIES` 后重启后端服务\n"
            f"3. 对于京东/亚马逊等无需登录的平台，可尝试使用对应的商品 URL\n"
        )

    # 1c. IP 限制/风控页检测 — 京东/亚马逊等平台可能返回风控页而非商品页
    if _is_blocked_page(markdown):
        logger.warning(f"[Competitor:pipeline] {url} 疑似 IP 限制/风控页，跳过抽取")
        anti_ban.report_blocked(platform, url)
        watch = store.get_watch_by_url(url)
        store.save_snapshot({
            "watchlist_id": watch["id"] if watch else None,
            "url": url,
            "platform": platform,
            "extract_method": "ip_blocked",
            "raw_excerpt": markdown[:2000],
            "in_stock": 0,
        })
        return (
            f"## 竞品分析失败: 风控拦截\n\n"
            f"{url}\n\n"
            f"⚠️ 该页面被平台风控拦截。**已触发一票停保护：{platform} 平台今日停止采集**，"
            f"避免继续尝试导致封禁升级（事件已记录，可用 /competitor/anti-ban/stats 查看）。\n\n"
            f"**建议**:\n"
            f"1. 等待明日自动恢复，期间不要手动重试该平台\n"
            f"2. 检查 Cookie 是否过期（Cookie 配置 → 扫码登录刷新）\n"
            f"3. 若 24h 内再次被封将触发全局停采 48h\n"
        )

    # 2. 结构化抽取（LLM 优先，正则兜底）
    fields = extract_fields(platform, markdown, use_llm=use_llm)
    anti_ban.report_success(platform)

    # 3. 快照入库（append-only）—— 抽取质量太低时跳过入库，避免污染历史数据
    has_price = fields.get("price") is not None
    has_title = bool(fields.get("title"))
    if not has_price and not has_title:
        logger.warning(f"[Competitor:pipeline] {url} 抽取质量过低（无价格无标题），跳过快照入库")
        snap_id = None
    else:
        watch = store.get_watch_by_url(url)
        snap_id = store.save_snapshot({
            "watchlist_id": watch["id"] if watch else None,
            "url": url,
            "platform": platform,
            "raw_excerpt": markdown[:2000],
            **fields,
        })

    # 3b. 市场语义索引（失败不影响主流程）
    if snap_id is not None:
        from backend.selection.market_index import index_snapshot_safe
        saved = store.latest_snapshot(url)
        if saved:
            index_snapshot_safe(saved)

    # 4. 与上次快照对比
    if snap_id is not None:
        change = _compare_with_previous(snap_id, url, fields.get("price"), fields.get("currency", "CNY"))
    else:
        change = "抽取质量不足，未存入快照"

    # 5. 汇总输出
    title = fields.get("title") or name or url
    currency = fields.get("currency", "CNY")
    lines = [
        f"## 竞品分析: {title}",
        "",
        f"- **平台**: {platform}",
        f"- **现价**: {_fmt_price(fields.get('price'), currency)}"
        + (f"（划线价 {_fmt_price(fields.get('original_price'), currency)}）" if fields.get("original_price") else ""),
        f"- **评分**: {fields.get('rating') if fields.get('rating') is not None else '未知'} / 5",
        f"- **促销**: {fields.get('promo_text') or '无'}",
        f"- **评价数**: {fields.get('review_count') if fields.get('review_count') is not None else '未知'}",
        f"- **库存**: {'有货' if fields.get('in_stock') else '无货/疑似下架'}",
        f"- **价格变化**: {change}",
    ]
    if fields.get("highlights"):
        lines.append(f"- **卖点**: {fields['highlights']}")
    snap_info = f"快照 #{snap_id} 已存档" if snap_id else "快照未存档（抽取质量低）"
    lines.append(f"\n*{snap_info} | 抽取方式: {fields.get('extract_method')} | "
                 f"{datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    return "\n".join(lines)


def scan_watchlist() -> str:
    """巡检全部启用的监控项（供定时任务或对话触发）

    防封策略:
      - Cookie 疑似失效的平台（已配置 Cookie 但近期撞登录页）跳过，
        避免用死 Cookie 反复撞（撞登录页本身积累风控分）
      - 单项被闸门拒绝（配额/平台停采）不影响其他项；全局停采则立即终止本轮
    """
    from backend.competitor import cookie_manager
    from backend.competitor.cookie_manager import normalize_platform

    store = get_store()
    items = store.list_watch(enabled_only=True)
    if not items:
        return "监控列表为空。可先通过 competitor.analyze 分析一个竞品 URL，或用 add 加入监控。"

    parts = [f"## 竞品巡检（{len(items)} 项）", ""]
    failed: list[str] = []
    skipped: list[str] = []
    for item in items:
        logger.info(f"[Competitor:pipeline] 巡检 {item['name']} → {item['url']}")
        # 死 Cookie 保护: 已配置但疑似失效 → 跳过，等重新扫码
        norm = normalize_platform(item.get("platform") or detect_platform(item["url"]))
        if (cookie_manager.get_cookies_for_platform(norm)
                and anti_ban.is_cookie_suspect(norm)):
            msg = f"{norm} Cookie 疑似失效（近期登录重定向），跳过等待重新扫码"
            logger.info(f"[Competitor:pipeline] 跳过 {item['name']}: {msg}")
            skipped.append(f"- {item['name']}: {msg}")
            parts.append(f"## ⏸️ {item['name']}")
            parts.append(msg)
            parts.append("")
            continue
        try:
            result = analyze_url(item["url"], name=item["name"])
            parts.append(result)
        except anti_ban.GlobalHaltError as e:
            logger.error(f"[Competitor:pipeline] 全局停采，本轮巡检终止: {e}")
            parts.append(f"## ⛔ 巡检终止\n\n{e}")
            break
        except anti_ban.AntiBanError as e:
            logger.warning(f"[Competitor:pipeline] 闸门拒绝 {item['name']}: {e}")
            skipped.append(f"- {item['name']}: {e}")
            parts.append(f"## ⏸️ {item['name']}")
            parts.append(f"防封闸门跳过: {e}")
        except Exception as e:
            logger.warning(f"[Competitor:pipeline] 巡检 {item['name']} 失败: {e}")
            failed.append(f"- {item['name']} ({item['url'][:60]}): {e}")
            parts.append(f"## ❌ {item['name']}")
            parts.append(f"抓取失败: {e}")
        parts.append("")

    if skipped:
        parts.append(f"---\n⏸️ {len(skipped)} 项被防封策略跳过:")
        parts.extend(skipped)
        parts.append("")
    if failed:
        parts.append(f"---\n⚠️ {len(failed)} 项巡检失败:")
        parts.extend(failed)
        parts.append("")
    return "\n".join(parts)


def history_report(url: str, limit: int = 10) -> str:
    """价格历史报告（含趋势摘要）"""
    store = get_store()
    snaps = store.history(url, limit=limit)
    if not snaps:
        return f"{url} 暂无抓取历史，请先执行一次分析。"

    watch = store.get_watch_by_url(url)
    title = (watch["name"] if watch else "") or snaps[0].get("title") or url
    lines = [f"## 价格历史: {title}", "",
             "| 时间 | 价格 | 划线价 | 评分 | 评价数 | 促销 |",
             "|---|---|---|---|---|---|"]
    for s in reversed(snaps):  # 旧→新
        cur = s.get("currency") or "CNY"
        rating = s.get("rating")
        rating_str = f"{rating}/5" if rating is not None else "-"
        rc = s.get("review_count")
        rc_str = f"{rc:,}" if rc is not None else "-"
        lines.append(
            f"| {s['crawled_at'][:16]} | {_fmt_price(s.get('price'), cur)} "
            f"| {_fmt_price(s.get('original_price'), cur)} | {rating_str} | {rc_str} | {(s.get('promo_text') or '无')[:30]} |"
        )
    # 卖点摘要（取最新一条非空）
    latest_hl = next((s.get("highlights") for s in snaps if s.get("highlights")), "")
    if latest_hl:
        lines.append(f"\n卖点: {latest_hl[:200]}")
    prices = [s["price"] for s in snaps if s.get("price") is not None]
    currency = snaps[0].get("currency") or "CNY"
    if len(prices) >= 2:
        # 趋势摘要：最新 vs 最旧
        latest_price = prices[0]   # snaps 是新→旧，prices[0] 是最新
        oldest_price = prices[-1]
        diff = latest_price - oldest_price
        pct = diff / oldest_price * 100 if oldest_price else 0
        if abs(diff) < 0.01:
            trend = "📊 趋势平稳"
        elif diff < 0:
            trend = f"📉 整体降价（{diff:+,.2f}，{pct:+.1f}%）"
        else:
            trend = f"📈 整体涨价（{diff:+,.2f}，{pct:+.1f}%）"
        lines.append(
            f"\n区间: {_fmt_price(min(prices), currency)} ~ "
            f"{_fmt_price(max(prices), currency)}，共 {len(prices)} 次有效价格记录"
        )
        lines.append(f"趋势: {trend}")
    return "\n".join(lines)

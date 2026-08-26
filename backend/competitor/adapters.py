"""competitor/adapters.py — 平台识别 + 规则抽取（正则兜底）

平台适配器职责:
  1. detect_platform(url)  — 从 URL 识别电商平台
  2. extract_by_rules(platform, markdown) — 正则抽取价格/标题/促销等关键字段

LLM 不可用或返回非法 JSON 时，规则抽取保证链路可用（降级策略）。
"""
import re
from typing import Any

# ── 平台识别 ────────────────────────────────────────
_PLATFORM_PATTERNS: list[tuple[str, str]] = [
    (r"item\.jd\.com|jd\.com", "jd"),
    (r"detail\.tmall\.com|tmall\.com", "tmall"),
    (r"item\.taobao\.com|taobao\.com", "taobao"),
    (r"amazon\.(com|cn|de|co\.jp)(?![a-z])", "amazon"),  # 负向前瞻，避免 amazon.community 等
    (r"yangkeduo|pinduoduo\.com", "pdd"),
    (r"suning\.com", "suning"),
    (r"douyin\.com|jinritemai\.com", "douyin"),
]


def detect_platform(url: str) -> str:
    for pattern, platform in _PLATFORM_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return platform
    return "generic"


# ── 价格抽取 ────────────────────────────────────────
# 覆盖: ¥199 / ￥1,299.00 / $4.99 / 199.00元 / "price":"129.00"
_PRICE_PATTERNS = [
    r"[¥￥]\s*([0-9]{1,6}(?:[,.][0-9]{3})*(?:\.[0-9]{1,2})?)",  # CNY
    r'\$\s*([0-9]{1,6}(?:[,.][0-9]{3})*(?:\.[0-9]{1,2})?)',  # USD: $4.99, $1,299.00
    r'"price"\s*:\s*"?([0-9]{1,6}(?:\.[0-9]{1,2})?)',  # JSON
    r"([0-9]{2,6}(?:\.[0-9]{1,2})?)\s*元",  # CNY 元
]


def _parse_price_tokens(markdown: str) -> list[float]:
    """按出现顺序抽取所有价格 token（符号价格优先：导航长文本中的杂讯更少）"""
    tokens: list[float] = []
    for pattern in _PRICE_PATTERNS:
        for m in re.finditer(pattern, markdown):
            # 导航文本中的“Under $10 / 9.9元起”类区间描述不是商品价格
            prefix = markdown[max(0, m.start() - 6):m.start()].lower()
            if prefix.endswith("under "):
                continue
            raw = m.group(1).replace(",", "").rstrip(".")
            try:
                value = float(raw)
                if 0.5 <= value <= 10_000_000:  # 合理价格区间，过滤订单号/编号
                    tokens.append(value)
            except ValueError:
                continue
        if tokens:
            break  # 第一组 pattern 命中即止，避免混入杂讯
    return tokens


# 常见导航/帮助文本，不应被当作商品标题
_NAV_NOISE_PHRASES = [
    "keyboard shortcuts", "skip to", "main content", "about this item",
    "skip to search", "skip to footer", "your account", "customer service",
    "hello", "select your address", "sign in", "returns", "orders",
    "try prime", "add to cart", "buy now", "sell on amazon",
    "best sellers", "today's deals", "gift cards",
]

# 键盘快捷键行（如 "shift + alt + K"），不应被当作标题
_SHORTCUT_LINE_RE = re.compile(r"^(?:shift|ctrl|alt|cmd|command|tab|esc|enter)[\s+\/a-z0-9\-]*$", re.IGNORECASE)

# 账号昵称行（纯 ASCII 无空格，如 tb50348234 / jd_user01），不是商品标题
_ACCOUNT_LINE_RE = re.compile(r"^[a-z0-9][a-z0-9_\-.]*$", re.IGNORECASE)

# taobao/tmall 销量锚点：标题通常位于“已售 xxx”行上方数行内
_SOLD_ANCHOR_RE = re.compile(r"^已售\s*[0-9]")
# 标题候选区内的促销文案行（非标题）
_TITLE_NOISE_KEYWORDS = ("促销", "优惠", "满减", "折扣", "券", "限时", "秒杀", "补贴")

# markdown 图片语法剥离：京东商品标题行常与缩略图 ![](...) 粘连在同一行
_IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# jd 价格锚点：新版详情页正文标题紧邻“¥115到手价 / ¥118”价格行上方
_JD_PRICE_ANCHOR_RE = re.compile(r"^[¥￥]\s*[0-9]")
# jd 标题行尾部常粘连操作按钮文案（收藏/加入购物车）
_JD_TITLE_TRAILING = ("收藏", "加入购物车", "立即购买")


def _clean_candidate(line: str) -> str:
    """剥离 markdown 图片/强调标记，得到纯文本标题候选"""
    cleaned = _IMAGE_MD_RE.sub("", line)
    cleaned = re.sub(r'[*_`]', '', cleaned).strip()
    cleaned = re.sub(r'^#+\s*', '', cleaned).strip()
    cleaned = re.sub(r'^[\*\-•]\s*', '', cleaned).strip()
    return cleaned


def _valid_title(cleaned: str) -> bool:
    """淘宝方案通用过滤：长度/纯价格行/账号昵称行"""
    if not (8 <= len(cleaned) <= 200):
        return False
    if re.match(r"^[¥￥$0-9\s.,]+$", cleaned):
        return False
    if _ACCOUNT_LINE_RE.match(cleaned):
        return False
    return True


def _guess_title_taobao(markdown: str) -> str:
    """taobao/tmall 专用：以“已售”行为锚点，取其上方第一条有效文本行作为标题。

    淘宝详情页正文前部是导航/搜索词噪声，通用“首行启发式”会误选；
    标题稳定出现在“已售 xxx”行紧邻上方。
    """
    lines = [ln.strip() for ln in markdown.splitlines()]
    for i, line in enumerate(lines):
        if not _SOLD_ANCHOR_RE.match(line):
            continue
        for back in reversed(lines[max(0, i - 8):i]):
            cleaned = re.sub(r'[*_`]', '', back).strip()
            if not (8 <= len(cleaned) <= 200):
                continue
            if re.match(r"^[¥￥$0-9\s.,]+$", cleaned):
                continue
            if _ACCOUNT_LINE_RE.match(cleaned):
                continue
            if any(k in cleaned for k in _TITLE_NOISE_KEYWORDS):
                continue
            return cleaned
    return ""


def _guess_title_jd(markdown: str) -> str:
    """jd 专用：以“¥价格”行为锚点，向上找第一条有效文本行（剥图片语法）。

    京东新版详情页正文前部是导航/面包屑/店铺噪声，标题行常与缩略图
    ![](...) 粘连且不在页面首行；标题稳定出现在价格行紧邻上方。
    """
    lines = [ln.strip() for ln in markdown.splitlines()]
    for i, line in enumerate(lines):
        if not _JD_PRICE_ANCHOR_RE.match(line):
            continue
        for back in reversed(lines[max(0, i - 10):i]):
            cleaned = _clean_candidate(back)
            if not _valid_title(cleaned):
                continue
            for tail in _JD_TITLE_TRAILING:
                if cleaned.endswith(tail):
                    cleaned = cleaned[:-len(tail)].strip()
            if any(k in cleaned for k in _TITLE_NOISE_KEYWORDS):
                continue
            return cleaned
    return ""


# amazon 标题锚点：商品评分行（标题紧邻其上方），不匹配导航链接内的 out of 5 文字
_AMZ_ANCHOR_RE = re.compile(r"out\s*of\s*5\s*stars|^[0-9][0-9,.]*\s*global\s*ratings", re.IGNORECASE)


def _guess_title_amazon(markdown: str) -> str:
    """amazon 专用：以评分行为锚点向上找标题，失败退回首行启发式。

    亚马逊正文前部是键盘快捷键/导航噪声，标题（h1 → `#` 行）出现在
    “4.7 out of 5 stars / 7,158 global ratings”行紧邻上方；链接行跳过。
    """
    lines = [ln.strip() for ln in markdown.splitlines()]
    for i, line in enumerate(lines):
        if not _AMZ_ANCHOR_RE.search(line):
            continue
        fallback = ""
        for back in reversed(lines[max(0, i - 40):i]):
            stripped = back.strip()
            # 链接行（面包屑/店铺/评分链接）不可能是标题
            if stripped.startswith("[") or stripped.startswith("!["):
                continue
            cleaned = _clean_candidate(stripped)
            if not _valid_title(cleaned):
                continue
            if _SHORTCUT_LINE_RE.match(cleaned.lower()):
                continue
            lower = cleaned.lower()
            if any(lower.startswith(p) or lower == p for p in _NAV_NOISE_PHRASES):
                continue
            if any(k in cleaned for k in _TITLE_NOISE_KEYWORDS):
                continue
            # `#` 标题行（h1）优先级最高，命中即返回
            if stripped.startswith("#"):
                return cleaned
            if not fallback:
                fallback = cleaned
        if fallback:
            return fallback
        break  # 首个锚点向上未命中即退回，避免锚到页脚/评论区评分行
    return ""


def _guess_title(markdown: str, platform: str) -> str:
    """取正文第一行较长的文本作为标题（跳过导航/帮助文本）"""
    if platform in ("taobao", "tmall"):
        anchored = _guess_title_taobao(markdown)
        if anchored:
            return anchored
    if platform == "jd":
        anchored = _guess_title_jd(markdown)
        if anchored:
            return anchored
    if platform == "amazon":
        anchored = _guess_title_amazon(markdown)
        if anchored:
            return anchored
    for line in markdown.splitlines()[:50]:
        line = line.strip().lstrip("# ").strip()
        # 剥离列表标记（* - •）
        line = re.sub(r'^[\*\-•]\s*', '', line).strip()
        # 剥离 markdown 强调标记（**加粗** / _斜体_ / `代码`），避免噪声过滤被穿透
        line = re.sub(r'[*_`]', '', line).strip()
        # 跳过纯链接/图片行和列表项链接
        if line.startswith("![") or line.startswith("[") or line.startswith("* [") or line.startswith("- ["):
            continue
        # 跳过账号昵称行（淘宝登录后页面首行是用户名）
        if _ACCOUNT_LINE_RE.match(line):
            continue
        if 8 <= len(line) <= 200 and not re.match(r"^[¥￥$0-9\s.,]+$", line):
            lower = line.lower()
            # 跳过导航噪声短语 / 键盘快捷键行
            if any(lower.startswith(p) or lower == p for p in _NAV_NOISE_PHRASES):
                continue
            if _SHORTCUT_LINE_RE.match(lower):
                continue
            return line
    return ""


# 卖点区块标题（中英文），其后的 bullet 行视为卖点
_HIGHLIGHT_SECTION_RE = re.compile(
    r"(?:about this item|item highlights|product features|features\b|"
    r"商品介绍|产品特点|产品卖点|卖点|核心参数|规格参数)", re.IGNORECASE)
_BULLET_RE = re.compile(r'^[\*\-•·]+\s*')


def _extract_highlights(markdown: str) -> str:
    """抽取卖点区块的 bullet 行（最多 5 条，逗号拼接）"""
    hits: list[str] = []
    in_section = False
    for line in markdown.splitlines():
        s = line.strip()
        if not s:
            continue
        cleaned = re.sub(r'^#+\s*', '', s)
        cleaned = re.sub(r'[*_`]', '', cleaned).strip()
        if _HIGHLIGHT_SECTION_RE.search(cleaned) and len(cleaned) <= 30:
            in_section = True
            continue
        if in_section:
            if _BULLET_RE.match(s) or (hits and len(hits) < 5 and 6 <= len(cleaned) <= 100):
                t = _BULLET_RE.sub('', cleaned).strip()
                if 6 <= len(t) <= 100 and not t.startswith('![') and not t.startswith('['):
                    hits.append(t)
                    if len(hits) >= 5:
                        break
                    continue
            # 非 bullet 且已收集到卖点 → 区块结束
            if hits:
                break
    return ", ".join(hits)


# 注: "off" 已移除，因会误匹配 office/offer 等词；"sale"/"coupon" 已覆盖英文促销场景
_PROMO_KEYWORDS = ["促销", "优惠", "满减", "折扣", "券", "限时", "秒杀", "直降",
                   "立减", "活动", "预售", "赠品", "coupon", "sale"]
# 促销行噪声：店铺引导/客服文案不是促销信息（京东详情页常见）
_PROMO_NOISE_KEYWORDS = ("进店", "逛逛", "客服", "关注店铺", "sign in", "hello,")


def _extract_promo(markdown: str) -> str:
    """抽取含促销关键词的行"""
    hits: list[str] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line or len(line) > 80:
            continue
        # 跳过纯链接行与占位符行（如 markdown 强调包裹的空 _Coupon:_）
        if line.startswith("[") or line.startswith("!["):
            continue
        cleaned = re.sub(r'[*_`]', '', line).strip().rstrip(":：")
        if not cleaned:
            continue
        if any(kw in line.lower() for kw in _PROMO_NOISE_KEYWORDS):
            continue
        if any(kw in line.lower() for kw in _PROMO_KEYWORDS):
            hits.append(line)
            if len(hits) >= 3:
                break
    return " | ".join(hits)


def _parse_count(raw: str, wan: bool) -> int | None:
    """解析评价数；'5万+' 类表达乘 10000，'10K+' 类表达乘 1000"""
    cleaned = raw.replace("，", "").replace(",", "").rstrip("+.")
    k = cleaned.lower().endswith("k")
    if k:
        cleaned = cleaned[:-1]
    try:
        val = int(cleaned)
    except ValueError:
        return None
    if wan:
        return val * 10_000
    if k:
        return val * 1_000
    return val


# 评价数模式：中文（5万+条评价 / 累计评价 10000）+ 英文（12,345 ratings/reviews）
# 顺序敏感：平台专属格式（淘宝已售/京东买家评价/亚马逊 reviewed in）优先于通用模式，
# 避免通用“评价|评论”模式在专属表达前抢先命中
_REVIEW_PATTERNS: list[tuple[str, bool]] = [
    (r"(?:用户)?(?:买家)?(?:评价|评论)[（(·\s]*([0-9，,+.]{1,12})\s*(万)?\s*\+?", True),  # 淘宝: 用户评价·100+ / 京东: 买家评价(5万+)
    (r"累计(?:评价|评论|销量)\s*([0-9，,+.]{1,12})\s*(万)?\s*\+?", True),  # 京东: 累计评价 5万+
    (r"已售(?:出)?\s*([0-9，,+.]{1,12})\s*(万)?\s*\+?", True),  # 淘宝: 已售 100+ / 已售2万+
    (r"([0-9，,+.]{1,12})\s*(万)?\s*\+?\s*(?:人|条|\+)?\s*(?:评价|评论)", True),
    (r"([0-9，,+.]{1,12})\s*,?\s*(?:global\s*)?(?:ratings?|reviews)\b", False),  # 亚马逊: 12,345 ratings / global ratings
    (r"([0-9][0-9.,]*[kK]?)\s*\+?\s*(?:bought|sold)\s+in\s+(?:past|last)\s+month", False),  # 亚马逊: 10K+ bought in past month
]


def _extract_review_count(markdown: str) -> int | None:
    for pattern, wan_support in _REVIEW_PATTERNS:
        for m in re.finditer(pattern, markdown, re.IGNORECASE):
            # 中文模式第二组为 '万' 才乘；英文模式 group(2) 是 ratings/reviews 不乘
            wan = wan_support and (m.group(2) or "") == "万"
            val = _parse_count(m.group(1), wan)
            if val is not None and val > 0:
                return val
    return None


# 评分模式：Amazon（4.6 out of 5 stars / 4.6 颗星）、通用（评分 4.8 / 4.8 分）、JD 好评率
_RATING_PATTERNS = [
    r"(\d(?:\.\d+)?)\s*out of\s*5\s*stars",
    r"(\d(?:\.\d+)?)\s*颗星",
    r"(?:评分|rating|score)[^\d\n]{0,6}(\d(?:\.\d+)?)",
    r"(\d(?:\.\d+)?)\s*分\s*(?:\(满分|/\s*5|满分)",
]


def _extract_rating(markdown: str) -> float | None:
    for pattern in _RATING_PATTERNS:
        m = re.search(pattern, markdown, re.IGNORECASE)
        if m:
            try:
                v = float(m.group(1))
                if 0 < v <= 5:
                    return v
            except ValueError:
                continue
    # 京东好评率 98% → 换算为 5 分制（98% ≈ 4.9）
    m = re.search(r"好评率\s*([0-9]{2,3}(?:\.\d+)?)\s*%", markdown)
    if m:
        pct = float(m.group(1))
        if 50 <= pct <= 100:
            return round(pct / 20, 1)
    return None


def detect_currency(markdown: str) -> str:
    """从正文货币符号识别币种（£→GBP / $→USD / €→EUR / 默认 CNY）"""
    head = markdown[:3000]
    if "£" in head:
        return "GBP"
    if "€" in head:
        return "EUR"
    if "$" in head:
        return "USD"
    return "CNY"


def extract_by_rules(platform: str, markdown: str) -> dict[str, Any]:
    """规则抽取（LLM 降级方案）。抽取不到的字段保持 None，由上层决定是否可信。"""
    prices = _parse_price_tokens(markdown)
    result: dict[str, Any] = {
        "title": _guess_title(markdown, platform),
        "price": prices[0] if prices else None,
        "original_price": prices[1] if len(prices) > 1 else None,
        "currency": detect_currency(markdown),
        "promo_text": _extract_promo(markdown),
        "rating": _extract_rating(markdown),
        "review_count": _extract_review_count(markdown),
        "in_stock": 0 if re.search(r"无货|缺货|售罄|out of stock", markdown, re.IGNORECASE) else 1,
        "highlights": _extract_highlights(markdown),
    }
    return result


def confidence(result: dict[str, Any]) -> float:
    """抽取结果可信度 0~1：价格 + 标题是硬字段"""
    score = 0.0
    if result.get("price"):
        score += 0.5
    if result.get("title"):
        score += 0.3
    if result.get("review_count") or result.get("promo_text"):
        score += 0.1
    if result.get("rating") or result.get("highlights"):
        score += 0.1
    return round(score, 2)

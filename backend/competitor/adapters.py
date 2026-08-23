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
    """按出现顺序抽取所有价格 token（页面价格一般在标题附近靠前）"""
    tokens: list[float] = []
    for pattern in _PRICE_PATTERNS:
        for m in re.finditer(pattern, markdown):
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


def _guess_title(markdown: str, platform: str) -> str:
    """取正文第一行较长的文本作为标题（跳过导航/帮助文本）"""
    for line in markdown.splitlines()[:50]:
        line = line.strip().lstrip("# ").strip()
        # 剥离列表标记（* - •）
        line = re.sub(r'^[\*\-•]\s*', '', line).strip()
        # 剥离 markdown 强调标记（**加粗** / _斜体_ / `代码`），避免噪声过滤被穿透
        line = re.sub(r'[*_`]', '', line).strip()
        # 跳过纯链接/图片行和列表项链接
        if line.startswith("![") or line.startswith("[") or line.startswith("* [") or line.startswith("- ["):
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


def _extract_promo(markdown: str) -> str:
    """抽取含促销关键词的行"""
    hits: list[str] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line or len(line) > 80:
            continue
        if any(kw in line.lower() for kw in _PROMO_KEYWORDS):
            hits.append(line)
            if len(hits) >= 3:
                break
    return " | ".join(hits)


def _parse_count(raw: str, wan: bool) -> int | None:
    """解析评价数；'5万+' 类表达乘 10000"""
    cleaned = raw.replace("，", "").replace(",", "").rstrip("+.")
    try:
        val = int(cleaned)
    except ValueError:
        return None
    return val * 10_000 if wan else val


# 评价数模式：中文（5万+条评价 / 累计评价 10000）+ 英文（12,345 ratings/reviews）
_REVIEW_PATTERNS: list[tuple[str, bool]] = [
    (r"([0-9，,+.]{1,12})\s*(万)?\s*\+?\s*(?:人|条)?\s*(?:评价|评论)", True),
    (r"([0-9，,+.]{1,12})\s*,?\s*(ratings?|reviews)\b", False),
    (r"累计(?:评价|评论)\s*([0-9，,+.]{1,12})\s*(万)?\s*\+?", True),
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

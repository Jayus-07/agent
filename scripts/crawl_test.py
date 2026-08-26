#!/usr/bin/env python
"""scripts/crawl_test.py — 多平台电商 URL 深度抓取测试

对 4 个 URL（淘宝×2、京东×1、亚马逊×1）执行：
  1. HTTP 预检（状态码、重定向、Set-Cookie）
  2. crawl4ai 浏览器抓取（平台差异化配置 + 重试）
  3. 结构化数据提取（标题、价格、评价数、促销、库存）
  4. 反爬检测（登录页、验证码、空白页）
  5. JSON 报告输出

用法:
  python scripts/crawl_test.py
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backend.competitor.adapters import detect_platform, extract_by_rules, confidence
from backend.competitor.pipeline import _is_login_page
from backend.tools.crawler_runtime import crawl
from backend.shared.logger import logger

# ── 测试 URL ──────────────────────────────────────────────

TEST_URLS = [
    {
        "url": "https://item.taobao.com/item.htm?ali_refid=a3_420860_1007%3A26919299%3AH%3A26919299_0_24957898290%3A1f81231deb8ac4ef8cb9b70ab2b28e37&ali_trackid=319_1f81231deb8ac4ef8cb9b70ab2b28e37&id=815673415507&item_type=ad&mi_id=0000T1e1xN5HFrrUrrKL_ZieiIZHfNrlx8NfWYXCmL2Zv-o&mm_sceneid=0_0_26919299_0&spm=tbpc.pc_sem_alimama%2Fa.201876.d2",
        "label": "淘宝（推广链接 #1）",
        "platform_hint": "taobao",
    },
    {
        "url": "https://item.jd.com/100153289341.html?pcdk=wflZCDtZ6OpVyOKSqaySxBtmE_-RFON1VYsVvxEHM4I%3D.dqA7.aWhW&spmTag=YTAyMTkuYjAwMjM1Ni5jMDAwMDY0MDkuNiU0MDE3ODc0Njk4ODAwOTklMjMxNzg3NDY5ODU0Nzg3NzkwODY2NTMwJTIzMTg4MDk3NTg3Mw",
        "label": "京东（带推广参数）",
        "platform_hint": "jd",
    },
    {
        "url": "https://www.amazon.com/Plotkas-Mouthwatchers-Antimicrobial-Bristle-Toothbrush/dp/B00AFWVHI2/?_encoding=UTF8&pd_rd_w=61vKN&content-id=amzn1.sym.6bf9c4a2-2636-40a9-bd71-5e2ff1c92846&pf_rd_p=6bf9c4a2-2636-40a9-bd71-5e2ff1c92846&pf_rd_r=MSRT8XZDS8E3ARX986VH&pd_rd_wg=jpCvF&pd_rd_r=b2f06c56-03a4-4b41-ba7a-1c04de5f581f&ref_=pd_hp_d_btf_exports_top_sellers_rec&th=1",
        "label": "亚马逊（推荐商品）",
        "platform_hint": "amazon",
    },
    {
        "url": "https://item.taobao.com/item.htm?ali_refid=a3_420860_1007%3A9204908211%3AH%3A800971339928_0_27870828873%3Abce7c8cdbb76ed392aba0c31d9f4a867&ali_trackid=296_bce7c8cdbb76ed392aba0c31d9f4a867&id=1071494256327&item_type=ad&mi_id=0000j4cIGqaNDxQtZ-qVKOgqiKobpr3244P8a4tSenj2n60&mm_sceneid=2_0_9204908211_0&spm=tbpc.31171857%2Fa.201876.d2",
        "label": "淘宝（推广链接 #2）",
        "platform_hint": "taobao",
    },
]

# ── 平台差异化配置 ────────────────────────────────────────

PLATFORM_CONFIGS = {
    "taobao": {
        # domcontentloaded 避免 networkidle 无限等待（淘宝持续加载广告/追踪）
        "wait_until": "domcontentloaded",
        "delay_before_return_html": 3.0,  # 额外等 3s 让 JS 渲染（单位: 秒）
        # 自动滚动触发懒加载
        "js_code": "window.scrollTo(0, document.body.scrollHeight);",
    },
    "jd": {
        "wait_until": "domcontentloaded",
        "delay_before_return_html": 2.0,
    },
    "amazon": {
        "wait_until": "domcontentloaded",
        "delay_before_return_html": 3.0,  # 亚马逊加载较慢
        "page_timeout": 60_000,  # 60s 超时
    },
}

# ── 反爬关键词 ────────────────────────────────────────────

CAPTCHA_KEYWORDS = ["验证码", "captcha", "verify", "人机验证", "滑动验证", "安全验证"]
# 注意: 不可用宽泛的 "IP" 关键词（会误匹配 VIP/tips/shipping 等子串）
# 使用更精确的短语避免误判
BLOCKED_KEYWORDS = [
    "访问受限", "访问被拒绝", "too many requests", "rate limit",
    "blocked", "forbidden", "请求过于频繁", "your ip", "ip address",
    "ip地址", "暂时限制", "稍后再试", "unusual traffic",
]


# ── 预检：HTTP 状态码和响应头 ─────────────────────────────

def precheck_url(url: str) -> dict:
    """用 urllib 预检 HTTP 状态码、重定向、Set-Cookie"""
    result = {"url": url, "ok": False, "status": None, "final_url": url,
              "redirected": False, "headers": {}, "error": ""}
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result["ok"] = True
            result["status"] = resp.status
            result["final_url"] = resp.url
            result["redirected"] = resp.url != url
            # 收集关键 header
            for h in ["Content-Type", "Server", "Set-Cookie", "Location",
                       "X-Frame-Options", "Content-Encoding"]:
                val = resp.headers.get(h)
                if val:
                    result["headers"][h] = val[:200]
    except urllib.error.HTTPError as e:
        result["ok"] = False
        result["status"] = e.code
        result["error"] = f"HTTP {e.code}: {e.reason}"
        # 302/301 重定向也可能触发 HTTPError
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get("Location", "")
            result["headers"]["Location"] = loc[:200]
            result["redirected"] = True
            result["error"] = f"重定向到: {loc[:100]}"
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)[:200]
    return result


# ── 浏览器抓取（带重试） ──────────────────────────────────

def crawl_with_retry(url: str, platform: str, max_retries: int = 3) -> dict:
    """带指数退避重试的 crawl4ai 抓取"""
    overrides = PLATFORM_CONFIGS.get(platform, {})
    backoff_times = [2, 5, 10]  # 指数退避
    attempts = []

    for attempt_idx in range(max_retries):
        attempt = {"attempt": attempt_idx + 1, "start": time.time()}
        logger.info(f"[CrawlTest] {platform} 第 {attempt_idx + 1}/{max_retries} 次尝试: {url[:80]}")

        result = crawl(url, mode="markdown", timeout=60.0, config_overrides=overrides)
        attempt["elapsed"] = round(time.time() - attempt["start"], 1)
        attempt["ok"] = result["ok"]

        if result["ok"]:
            content = result["content"]
            attempt["content_length"] = len(content)
            attempt["preview"] = content[:200]
            return {
                "ok": True,
                "content": content,
                "attempts": attempts + [attempt],
                "total_elapsed": round(sum(a.get("elapsed", 0) for a in attempts + [attempt]), 1),
            }
        else:
            attempt["error"] = result["error"]
            attempts.append(attempt)
            if attempt_idx < max_retries - 1:
                wait = backoff_times[attempt_idx]
                logger.info(f"[CrawlTest] 失败，{wait}s 后重试: {result['error'][:100]}")
                time.sleep(wait)

    return {
        "ok": False,
        "content": "",
        "attempts": attempts,
        "total_elapsed": round(sum(a.get("elapsed", 0) for a in attempts), 1),
        "error": attempts[-1]["error"] if attempts else "未知错误",
    }


# ── 反爬检测 ──────────────────────────────────────────────

def detect_anti_crawl(content: str, platform: str) -> dict:
    """检测反爬拦截

    内容长度启发式: 被拦截页通常很短（<5KB），内容 >10KB 基本是真实页面。
    对长内容跳过宽泛的 blocked 关键词匹配，避免误判。
    """
    head = content[:3000].lower()
    is_login = _is_login_page(content)

    captcha_hits = [kw for kw in CAPTCHA_KEYWORDS if kw.lower() in head]
    # 内容 >10KB 跳过宽泛 blocked 关键词（防止 VIP/tips 等子串误匹配）
    if len(content) < 10_000:
        blocked_hits = [kw for kw in BLOCKED_KEYWORDS if kw.lower() in head]
    else:
        blocked_hits = []

    if is_login:
        return {"type": "login_redirect", "detail": "被重定向到登录页", "keywords": []}
    if captcha_hits:
        return {"type": "captcha", "detail": f"检测到验证码关键词: {captcha_hits}", "keywords": captcha_hits}
    if blocked_hits:
        return {"type": "blocked", "detail": f"检测到访问受限: {blocked_hits}", "keywords": blocked_hits}

    return {"type": "none", "detail": "", "keywords": []}


# ── 数据提取 ──────────────────────────────────────────────

def extract_data(content: str, platform: str) -> dict:
    """使用 adapters.py 规则抽取结构化数据"""
    fields = extract_by_rules(platform, content)
    conf = confidence(fields)

    # 额外提取图片 URL（从 markdown 中提取）
    img_urls = []
    for m in re.finditer(r"!\[.*?\]\((https?://[^\s)]+)\)", content):
        url = m.group(1)
        if any(ext in url.lower() for ext in [".jpg", ".png", ".webp", ".gif"]):
            img_urls.append(url)
            if len(img_urls) >= 5:
                break

    return {
        "title": fields.get("title") or "",
        "price": fields.get("price"),
        "original_price": fields.get("original_price"),
        "currency": fields.get("currency"),
        "promo_text": fields.get("promo_text") or "",
        "review_count": fields.get("review_count"),
        "rating": fields.get("rating"),
        "in_stock": fields.get("in_stock"),
        "highlights": fields.get("highlights") or "",
        "image_urls": img_urls[:3],
        "confidence": conf,
        "extract_method": "regex",
    }


# ── 主流程 ────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  多平台电商 URL 深度抓取测试")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = []

    for i, test in enumerate(TEST_URLS):
        url = test["url"]
        label = test["label"]
        platform = test["platform_hint"] or detect_platform(url)

        print(f"\n[{i+1}/{len(TEST_URLS)}] {label}")
        print(f"  URL: {url[:100]}...")
        print(f"  平台: {platform}")

        entry = {
            "label": label,
            "url": url,
            "platform": platform,
            "precheck": None,
            "crawl": None,
            "anti_crawl": None,
            "extracted_data": None,
            "status": "unknown",
            "error_log": [],
            "suggestions": [],
        }

        # 1. 预检
        print("  [预检] HTTP 请求...")
        pre = precheck_url(url)
        entry["precheck"] = pre
        print(f"  [预检] 状态: {pre['status']} | 重定向: {pre['redirected']} | 耗时: OK")
        if not pre["ok"]:
            entry["error_log"].append(f"预检失败: {pre['error']}")
            entry["status"] = "preflight_failed"
            entry["suggestions"].append("检查网络连接或 URL 是否有效")
            results.append(entry)
            continue

        # 2. 浏览器抓取（带重试）
        print(f"  [抓取] crawl4ai 浏览器抓取（最多 3 次重试）...")
        crawl_result = crawl_with_retry(url, platform, max_retries=3)
        entry["crawl"] = {
            "ok": crawl_result["ok"],
            "content_length": crawl_result.get("content_length", len(crawl_result.get("content", ""))),
            "total_elapsed": crawl_result.get("total_elapsed", 0),
            "attempts": crawl_result.get("attempts", []),
            "error": crawl_result.get("error", ""),
        }

        if not crawl_result["ok"]:
            entry["error_log"].append(f"抓取失败: {crawl_result.get('error', '')}")
            entry["status"] = "crawl_failed"
            # 分类失败类型
            err = crawl_result.get("error", "").lower()
            if "timeout" in err:
                entry["status"] = "timeout"
                entry["suggestions"].append("增加 page_timeout 或降低 wait_until 级别为 domcontentloaded")
            elif "navigation" in err or "net" in err:
                entry["status"] = "network_error"
                entry["suggestions"].append("检查网络连接或使用代理")
            else:
                entry["suggestions"].append("检查 crawl4ai 日志获取详细错误")
            results.append(entry)
            print(f"  [抓取] 失败: {entry['status']}")
            continue

        content = crawl_result["content"]
        print(f"  [抓取] 成功! 内容长度: {len(content)} 字符, 耗时: {crawl_result['total_elapsed']}s")

        # 3. 反爬检测
        ac = detect_anti_crawl(content, platform)
        entry["anti_crawl"] = ac
        if ac["type"] != "none":
            entry["status"] = f"anti_crawl_{ac['type']}"
            entry["error_log"].append(f"反爬拦截: {ac['detail']}")
            if ac["type"] == "login_redirect":
                entry["suggestions"].append("配置 Cookie 或使用扫码登录功能")
            elif ac["type"] == "captcha":
                entry["suggestions"].append("降低抓取频率或使用代理 IP 轮换")
            elif ac["type"] == "blocked":
                entry["suggestions"].append("使用代理池或增加请求间隔")
            results.append(entry)
            print(f"  [反爬] {ac['type']}: {ac['detail']}")
            continue

        # 4. 数据提取
        extracted = extract_data(content, platform)
        entry["extracted_data"] = extracted
        print(f"  [提取] 标题: {extracted['title'][:40] or '未知'}")
        print(f"  [提取] 价格: {extracted['price']} {extracted['currency']}")
        print(f"  [提取] 评价数: {extracted['review_count']}")
        print(f"  [提取] 可信度: {extracted['confidence']}")

        # 5. 结果判定
        if extracted["price"] is not None and extracted["title"]:
            entry["status"] = "success"
        elif extracted["title"] or extracted["price"]:
            entry["status"] = "partial_success"
            entry["suggestions"].append("部分字段未提取到，可能需要调整 CSS 选择器或使用 LLM 抽取")
        else:
            entry["status"] = "extraction_failed"
            entry["error_log"].append("无法提取任何有效字段")
            entry["suggestions"].append("检查页面是否为 SPA（需更长等待）或使用 LLM 抽取")

        results.append(entry)

    # 6. 生成报告
    report = {
        "test_time": datetime.now().isoformat(timespec="seconds"),
        "total_urls": len(TEST_URLS),
        "summary": {
            "success": sum(1 for r in results if r["status"] == "success"),
            "partial": sum(1 for r in results if r["status"] == "partial_success"),
            "login_blocked": sum(1 for r in results if "login" in r["status"]),
            "captcha": sum(1 for r in results if "captcha" in r["status"]),
            "blocked": sum(1 for r in results if "blocked" in r["status"]),
            "failed": sum(1 for r in results if "failed" in r["status"]),
        },
        "results": results,
    }

    # 输出 JSON 报告
    report_path = _PROJECT_ROOT / "data" / "crawl_test_report.json"
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 打印汇总表
    print("\n" + "=" * 70)
    print("  抓取测试报告")
    print("=" * 70)
    print(f"{'#':<3} {'标签':<20} {'状态':<18} {'耗时':>6} {'内容长度':>8} {'标题':<30}")
    print("-" * 90)
    for i, r in enumerate(results):
        elapsed = r.get("crawl", {}).get("total_elapsed", 0) if r.get("crawl") else 0
        clen = r.get("crawl", {}).get("content_length", 0) if r.get("crawl") else 0
        title = (r.get("extracted_data") or {}).get("title", "")[:28] or "-"
        print(f"{i+1:<3} {r['label'][:18]:<20} {r['status']:<18} {elapsed:>5}s {clen:>8} {title:<30}")

    print("-" * 90)
    s = report["summary"]
    print(f"成功: {s['success']} | 部分成功: {s['partial']} | 登录拦截: {s['login_blocked']} | "
          f"验证码: {s['captcha']} | 受限: {s['blocked']} | 失败: {s['failed']}")

    print(f"\n完整 JSON 报告: {report_path}")
    return report


if __name__ == "__main__":
    main()

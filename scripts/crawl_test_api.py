#!/usr/bin/env python
"""scripts/crawl_test_api.py — 通过后端 API 执行多平台抓取测试

复用后端已初始化的 crawl4ai 浏览器实例，避免冷启动延迟。
对 4 个 URL 执行预检 + API 抓取 + 数据提取 + 报告生成。
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backend.competitor.adapters import detect_platform, extract_by_rules, confidence
from backend.competitor.pipeline import _is_login_page

API_BASE = "http://localhost:8000"

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

CAPTCHA_KEYWORDS = ["验证码", "captcha", "verify", "人机验证", "滑动验证", "安全验证"]
# 注意: 不可用宽泛的 "IP" 关键词（会误匹配 VIP/tips/shipping 等子串）
BLOCKED_KEYWORDS = [
    "访问受限", "访问被拒绝", "too many requests", "rate limit",
    "blocked", "forbidden", "请求过于频繁", "your ip", "ip address",
    "ip地址", "暂时限制", "稍后再试", "unusual traffic",
]


def precheck_url(url: str) -> dict:
    """HTTP 预检"""
    result = {"url": url, "ok": False, "status": None, "final_url": url,
              "redirected": False, "headers": {}, "error": ""}
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result["ok"] = True
            result["status"] = resp.status
            result["final_url"] = resp.url
            result["redirected"] = resp.url != url
            for h in ["Content-Type", "Server", "Set-Cookie", "Location"]:
                val = resp.headers.get(h)
                if val:
                    result["headers"][h] = val[:200]
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        result["error"] = f"HTTP {e.code}: {e.reason}"
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get("Location", "")
            result["headers"]["Location"] = loc[:200]
            result["redirected"] = True
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def crawl_via_api(url: str) -> dict:
    """通过后端 API 抓取（复用已初始化的 crawl4ai 浏览器）"""
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(f"{API_BASE}/competitor/analyze",
                               json={"url": url, "use_llm": False})
            if resp.status_code == 200:
                data = resp.json()
                result_text = data.get("result", "")
                # 检测是否抓取失败
                if "[CRAWL FAILED]" in result_text or "竞品分析失败" in result_text:
                    return {"ok": False, "content": "", "error": result_text[:200],
                            "raw_result": result_text}
                # 提取 markdown 内容（分析结果中包含抓取的 markdown）
                return {"ok": True, "content": result_text, "error": "",
                        "raw_result": result_text}
            else:
                return {"ok": False, "content": "",
                        "error": f"API HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "content": "", "error": str(e)[:200]}


def detect_anti_crawl(content: str) -> dict:
    """检测反爬拦截（内容 >10KB 跳过宽泛 blocked 关键词，避免误判）"""
    head = content[:3000].lower()
    if _is_login_page(content):
        return {"type": "login_redirect", "detail": "被重定向到登录页"}
    captcha = [kw for kw in CAPTCHA_KEYWORDS if kw.lower() in head]
    if captcha:
        return {"type": "captcha", "detail": f"验证码关键词: {captcha}"}
    if len(content) < 10_000:
        blocked = [kw for kw in BLOCKED_KEYWORDS if kw.lower() in head]
    else:
        blocked = []
    if blocked:
        return {"type": "blocked", "detail": f"访问受限: {blocked}"}
    return {"type": "none", "detail": ""}


def extract_data(content: str, platform: str) -> dict:
    """从 markdown 内容中提取结构化数据"""
    fields = extract_by_rules(platform, content)
    conf = confidence(fields)
    img_urls = []
    for m in re.finditer(r"!\[.*?\]\((https?://[^\s)]+)\)", content):
        url = m.group(1)
        if any(ext in url.lower() for ext in [".jpg", ".png", ".webp"]):
            img_urls.append(url)
            if len(img_urls) >= 3:
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
        "image_urls": img_urls[:3],
        "confidence": conf,
        "extract_method": "regex",
    }


def main():
    print("=" * 70)
    print("  多平台电商 URL 深度抓取测试（API 模式）")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = []

    for i, test in enumerate(TEST_URLS):
        url = test["url"]
        label = test["label"]
        platform = test["platform_hint"] or detect_platform(url)

        print(f"\n[{i+1}/{len(TEST_URLS)}] {label}")
        print(f"  平台: {platform}")

        entry = {
            "label": label, "url": url, "platform": platform,
            "precheck": None, "crawl": None, "anti_crawl": None,
            "extracted_data": None, "status": "unknown",
            "error_log": [], "suggestions": [],
        }

        # 1. 预检
        print("  [预检] HTTP 请求...")
        pre = precheck_url(url)
        entry["precheck"] = pre
        print(f"  [预检] 状态: {pre['status']} | 重定向: {pre['redirected']}")
        if not pre["ok"] and pre["status"] not in (200, 301, 302, 303, 307, 308):
            entry["error_log"].append(f"预检异常: {pre['error']}")
            entry["suggestions"].append("检查网络连接")

        # 2. API 抓取
        print(f"  [抓取] 通过后端 API 抓取（crawl4ai 已初始化）...")
        t0 = time.time()
        crawl_result = crawl_via_api(url)
        elapsed = round(time.time() - t0, 1)

        content = crawl_result.get("content", "")
        entry["crawl"] = {
            "ok": crawl_result["ok"],
            "content_length": len(content),
            "elapsed": elapsed,
            "error": crawl_result.get("error", ""),
        }

        if not crawl_result["ok"]:
            entry["status"] = "crawl_failed"
            entry["error_log"].append(crawl_result.get("error", ""))
            entry["suggestions"].append("检查 crawl4ai 日志或增加超时时间")
            results.append(entry)
            print(f"  [抓取] 失败: {crawl_result.get('error', '')[:60]}")
            continue

        print(f"  [抓取] 成功! 内容长度: {len(content)} 字符, 耗时: {elapsed}s")

        # 3. 反爬检测
        ac = detect_anti_crawl(content)
        entry["anti_crawl"] = ac
        if ac["type"] != "none":
            entry["status"] = f"anti_crawl_{ac['type']}"
            entry["error_log"].append(f"反爬: {ac['detail']}")
            if ac["type"] == "login_redirect":
                entry["suggestions"].append("使用扫码登录或手动配置 Cookie")
            results.append(entry)
            print(f"  [反爬] {ac['type']}: {ac['detail']}")
            continue

        # 4. 数据提取
        extracted = extract_data(content, platform)
        entry["extracted_data"] = extracted
        print(f"  [提取] 标题: {extracted['title'][:40] or '未知'}")
        print(f"  [提取] 价格: {extracted['price']} {extracted['currency']}")
        print(f"  [提取] 评价: {extracted['review_count']}")

        if extracted["price"] is not None and extracted["title"]:
            entry["status"] = "success"
        elif extracted["title"] or extracted["price"]:
            entry["status"] = "partial_success"
            entry["suggestions"].append("部分字段未提取，建议使用 LLM 抽取")
        else:
            entry["status"] = "extraction_failed"
            entry["suggestions"].append("页面可能为 SPA，需更长等待或 LLM 抽取")

        results.append(entry)

    # 5. 报告
    report = {
        "test_time": datetime.now().isoformat(timespec="seconds"),
        "mode": "backend_api",
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

    report_path = _PROJECT_ROOT / "data" / "crawl_test_report.json"
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("  抓取测试报告")
    print("=" * 70)
    print(f"{'#':<3} {'标签':<20} {'状态':<22} {'耗时':>6} {'内容长度':>8} {'标题':<30}")
    print("-" * 92)
    for i, r in enumerate(results):
        cl = r.get("crawl", {})
        elapsed = cl.get("elapsed", 0) if cl else 0
        clen = cl.get("content_length", 0) if cl else 0
        title = (r.get("extracted_data") or {}).get("title", "")[:28] or "-"
        print(f"{i+1:<3} {r['label'][:18]:<20} {r['status']:<22} {elapsed:>5}s {clen:>8} {title:<30}")
    print("-" * 92)
    s = report["summary"]
    print(f"成功: {s['success']} | 部分成功: {s['partial']} | 登录拦截: {s['login_blocked']} | "
          f"验证码: {s['captcha']} | 受限: {s['blocked']} | 失败: {s['failed']}")
    print(f"\n完整 JSON 报告: {report_path}")
    return report


if __name__ == "__main__":
    main()

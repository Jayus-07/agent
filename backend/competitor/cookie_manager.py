"""competitor/cookie_manager.py — 多平台 Cookie 存储与选择

按平台分键存储 Cookie，抓取时按 URL 识别平台并选择对应 Cookie，
使淘宝/京东/抖音等多平台登录态可并存、互不覆盖。

存储键:
  crawler_cookies:<platform>       — 该平台的 Cookie 字符串
  crawler_cookies_meta:<platform>  — {"source","saved_at"} 来源元数据
另保留旧的全局键 crawler_cookies 作为默认兜底（兼容历史单平台数据）。

来源 source: "manual"（手动粘贴）/ "qr"（扫码登录）。
"""
import json
import os
from datetime import datetime
from typing import Any

from backend.competitor.adapters import detect_platform
from backend.competitor.store import get_store
from backend.shared.logger import logger

# 支持的平台（顺序即前端选择器/列表展示顺序）
SUPPORTED_PLATFORMS = [
    "taobao", "tmall", "jd", "douyin", "pdd", "suning", "amazon",
]

# 平台 → Cookie 生效域名（用于 BrowserConfig 注入时设置 domain）
PLATFORM_DOMAINS: dict[str, list[str]] = {
    "taobao": [".taobao.com"],
    "tmall": [".tmall.com"],
    "jd": [".jd.com"],
    "douyin": [".douyin.com"],
    "pdd": [".pinduoduo.com", ".yangkeduo.com"],
    "suning": [".suning.com"],
    "amazon": [".amazon.com", ".amazon.cn"],
}


def normalize_platform(platform: str) -> str:
    """tmall 与 taobao 共用登录态，统一归一到 taobao"""
    return "taobao" if platform == "tmall" else platform


def _ck(platform: str) -> str:
    return f"crawler_cookies:{platform}"


def _mk(platform: str) -> str:
    return f"crawler_cookies_meta:{platform}"


def _preview(val: str) -> str:
    return val[:20] + "..." if len(val) > 20 else val


def _read_meta(key: str) -> dict:
    try:
        return json.loads(get_store().get_config(key) or "{}")
    except Exception:
        return {}


def save_cookies(platform: str, cookies: str, source: str) -> None:
    """保存某平台的 Cookie 并记录来源（manual/qr）"""
    platform = normalize_platform(platform)
    store = get_store()
    store.set_config(_ck(platform), cookies)
    store.set_config(_mk(platform), json.dumps({
        "source": source,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }))
    logger.info(
        f"[cookie_manager] 保存 {platform} Cookie (source={source}, len={len(cookies)})"
    )


def delete_cookies(platform: str) -> bool:
    """删除某平台的 Cookie 及其元数据"""
    platform = normalize_platform(platform)
    store = get_store()
    a = store.delete_config(_ck(platform))
    b = store.delete_config(_mk(platform))
    logger.info(f"[cookie_manager] 删除 {platform} Cookie (deleted={a or b})")
    return a or b


def delete_all() -> int:
    """删除全部平台 + 旧全局键，返回删除的键数"""
    store = get_store()
    n = 0
    for p in SUPPORTED_PLATFORMS:
        if store.delete_config(_ck(p)):
            n += 1
        store.delete_config(_mk(p))
    if store.delete_config("crawler_cookies"):
        n += 1
    store.delete_config("crawler_cookies_meta")
    return n


def get_cookies_for_platform(platform: str) -> str:
    """读取某平台的 Cookie 字符串（无则空串）"""
    return (get_store().get_config(_ck(normalize_platform(platform))) or "").strip()


def get_cookies_for_url(url: str) -> str:
    """按 URL 识别平台并返回对应 Cookie；无则回退旧全局键/环境变量"""
    platform = normalize_platform(detect_platform(url))
    val = get_cookies_for_platform(platform)
    if not val:
        store = get_store()
        val = (store.get_config("crawler_cookies") or "").strip()
    if not val:
        val = os.getenv("CRAWLER_COOKIES", "").strip()
    return val


def all_platform_cookies() -> dict[str, str]:
    """返回 {platform: cookie_str}，含旧全局键归一到其元数据平台"""
    store = get_store()
    result: dict[str, str] = {}
    for p in SUPPORTED_PLATFORMS:
        val = (store.get_config(_ck(p)) or "").strip()
        if val:
            result[p] = val
    legacy = (store.get_config("crawler_cookies") or "").strip()
    if legacy:
        meta = _read_meta("crawler_cookies_meta")
        lp = normalize_platform(meta.get("platform") or "taobao")
        result.setdefault(lp, legacy)
    return result


def list_cookies() -> list[dict]:
    """列出所有已配置平台的 Cookie 摘要（供前端状态卡片）"""
    items: list[dict] = []
    for p in SUPPORTED_PLATFORMS:
        val = (get_store().get_config(_ck(p)) or "").strip()
        if not val:
            continue
        meta = _read_meta(_mk(p))
        items.append({
            "platform": p,
            "source": meta.get("source", "manual"),
            "saved_at": meta.get("saved_at", ""),
            "preview": _preview(val),
        })
    legacy = (get_store().get_config("crawler_cookies") or "").strip()
    if legacy:
        meta = _read_meta("crawler_cookies_meta")
        items.append({
            "platform": meta.get("platform") or "generic",
            "source": meta.get("source", "manual"),
            "saved_at": meta.get("saved_at", ""),
            "preview": _preview(legacy),
        })
    return items

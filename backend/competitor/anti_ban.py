"""competitor/anti_ban.py — 单账号/无代理场景防封策略中枢

设计前提: 只有一个账号、没有代理 IP 池 —— 没有任何"可牺牲的身份"，
目标从"被封后容错"变为"绝不触发风控"。五道防线:

  1. 量控优先   — 全局每日预算 + 平台日配额 + 随机间隔（真人级请求量）
  2. 一票停熔断  — 命中风控页立即停采该平台至次日；24h 内 2 次 → 全局停采 48h
  3. 指数退避    — 连续失败（超时/抓取错误）后间隔乘 2^n（上限 8 倍）
  4. 行为拟人    — humanize() 生成随机停留 + 分步滚动脚本
  5. 合规闸门    — robots.txt 检查（按域缓存 24h），尊重 Crawl-delay

状态持久化于 competitor_config:
  anti_ban:state   — 限流计数 / 熔断时间戳 / 失败连击
  anti_ban:robots  — robots.txt 缓存（按域）

所有函数接受可选 store 参数（默认全局单例），便于测试隔离。
"""
import json
import os
import random
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlparse

from backend.shared.logger import logger

# ── 恒定指纹（单账号场景: 一致性本身就是伪装）──────────────
# crawler_runtime / qr_login / robots 抓取统一从这里取，禁止各自维护副本

STABLE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

STABLE_VIEWPORT = {"width": 1280, "height": 800}

# ── 平台画像（单账号保守值: 间隔长 / 配额低 / 熔断敏感）────

@dataclass(frozen=True)
class PlatformProfile:
    min_interval: float   # 请求最小间隔（秒）
    max_interval: float   # 请求最大间隔（秒）
    daily_cap: int        # 每日请求上限
    cooldown_s: float     # L1 熔断后的冷却基准（实际停到当日结束）


PROFILES: dict[str, PlatformProfile] = {
    "taobao":  PlatformProfile(60, 180, 15, 7200),   # 淘系风控最严
    "jd":      PlatformProfile(45, 120, 20, 3600),
    "douyin":  PlatformProfile(90, 240, 10, 7200),   # 视频流站点权重高
    "pdd":     PlatformProfile(60, 180, 15, 3600),
    "suning":  PlatformProfile(45, 120, 20, 3600),
    "amazon":  PlatformProfile(45, 120, 20, 3600),
    "generic": PlatformProfile(20, 60, 40, 1800),
}

GLOBAL_DAILY_BUDGET = 40      # 全平台每日请求总预算（硬上限）
BACKOFF_CAP = 8               # 指数退避倍数上限: interval × 2^n, n 为失败连击
ROBOTS_CACHE_TTL = 86400      # robots.txt 缓存 24h
HALT_L2_SECONDS = 48 * 3600   # L2 全局停采时长
COOKIE_STALE_DAYS = 20        # Cookie 龄期预警阈值（平台普遍 30 天过期）

_STATE_KEY = "anti_ban:state"
_ROBOTS_KEY = "anti_ban:robots"


# ── 异常 ──────────────────────────────────────────────

class AntiBanError(Exception):
    """防封闸门拒绝请求（调用方应跳过本次采集并如实汇报）"""


class GlobalHaltError(AntiBanError):
    """L2: 全局停采中"""


class PlatformStoppedError(AntiBanError):
    """L1: 该平台当日已停采"""


class BudgetExhaustedError(AntiBanError):
    """全局预算或平台日配额耗尽"""


_lock = threading.Lock()


# ── 状态读写 ──────────────────────────────────────────

def _get_store(store=None):
    if store is not None:
        return store
    from backend.competitor.store import get_store
    return get_store()


def _default_state() -> dict[str, Any]:
    return {
        "day": datetime.now().strftime("%Y-%m-%d"),
        "global_used": 0,
        "halt_until": 0.0,
        "platforms": {},
        "cookie_suspect": {},   # platform -> ts（登录态疑似失效）
    }


def _load_state(store=None) -> dict[str, Any]:
    raw = _get_store(store).get_config(_STATE_KEY)
    if not raw:
        return _default_state()
    try:
        state = json.loads(raw)
    except Exception:
        return _default_state()
    # 跨天重置计数（熔断时间戳是绝对时间，不受影响）
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("day") != today:
        state["day"] = today
        state["global_used"] = 0
        for p in state.get("platforms", {}).values():
            p["count_today"] = 0
            p["fail_streak"] = 0
    return state


def _save_state(state: dict[str, Any], store=None) -> None:
    _get_store(store).set_config(_STATE_KEY, json.dumps(state, ensure_ascii=False))


def _platform_entry(state: dict[str, Any], platform: str) -> dict[str, Any]:
    platforms = state.setdefault("platforms", {})
    if platform not in platforms:
        platforms[platform] = {
            "last_ts": 0.0,
            "count_today": 0,
            "fail_streak": 0,
            "stopped_until": 0.0,
            "blocked_ts": [],   # 最近 24h 的 blocked 时间戳
        }
    return platforms[platform]


def _normalize(platform: str) -> str:
    """tmall 与 taobao 共享登录态与配额"""
    from backend.competitor.cookie_manager import normalize_platform
    return normalize_platform(platform)


def _profile(platform: str) -> PlatformProfile:
    return PROFILES.get(platform, PROFILES["generic"])


# ── ① 准入闸门: acquire ──────────────────────────────

def interval_for(platform: str, state: dict[str, Any]) -> float:
    """本次应等待的间隔: uniform(min,max) × 2^fail_streak（上限 BACKOFF_CAP）"""
    prof = _profile(platform)
    base = random.uniform(prof.min_interval, prof.max_interval)
    entry = _platform_entry(state, platform)
    multiplier = min(2 ** entry["fail_streak"], BACKOFF_CAP)
    return base * multiplier


def acquire(platform: str, wait: bool = True, store=None) -> float:
    """请求准入。返回实际等待秒数；被闸门拒绝时抛 AntiBanError 子类。

    wait=True 时阻塞 sleep 到间隔满足（scan_watchlist / analyze_url 用）。
    """
    platform = _normalize(platform)
    with _lock:
        state = _load_state(store)
        now = time.time()

        # L2 全局停采
        if now < state.get("halt_until", 0):
            remain = state["halt_until"] - now
            raise GlobalHaltError(
                f"全局停采中（L2 熔断），剩余 {remain / 3600:.1f}h — "
                f"单账号场景不做任何重试，等待人工确认"
            )

        entry = _platform_entry(state, platform)

        # L1 平台当日停采
        if now < entry["stopped_until"]:
            raise PlatformStoppedError(
                f"{platform} 今日已停采（命中风控后的保护性停止），明日自动恢复"
            )

        # 配额检查: 全局预算 + 平台日配额
        if state["global_used"] >= GLOBAL_DAILY_BUDGET:
            raise BudgetExhaustedError(
                f"全局每日预算已耗尽（{GLOBAL_DAILY_BUDGET} 次），明日恢复"
            )
        prof = _profile(platform)
        if entry["count_today"] >= prof.daily_cap:
            raise BudgetExhaustedError(
                f"{platform} 今日配额已用完（{prof.daily_cap} 次），明日恢复"
            )

        # 间隔等待（含指数退避倍数）
        delay = interval_for(platform, state)
        elapsed = now - entry["last_ts"]
        wait_s = max(0.0, delay - elapsed)
        # robots.txt 的 Crawl-delay 取大者
        crawl_delay = get_crawl_delay(platform, store=store)
        if crawl_delay and wait_s < crawl_delay:
            wait_s = crawl_delay

        if wait and wait_s > 0:
            logger.info(f"[AntiBan] {platform} 等待 {wait_s:.0f}s 后放行")
        _save_state(state, store)  # 先落盘等待意图，sleep 期间状态可见

    if wait and wait_s > 0:
        time.sleep(wait_s)

    with _lock:
        state = _load_state(store)
        entry = _platform_entry(state, platform)
        entry["last_ts"] = time.time()
        entry["count_today"] += 1
        state["global_used"] += 1
        _save_state(state, store)
    return wait_s


# ── ② 结果反馈: success / failure / blocked ──────────

def report_success(platform: str, store=None) -> None:
    """抓取成功 → 清零失败连击"""
    platform = _normalize(platform)
    with _lock:
        state = _load_state(store)
        entry = _platform_entry(state, platform)
        entry["fail_streak"] = 0
        _save_state(state, store)


def report_failure(platform: str, reason: str = "", store=None) -> None:
    """普通失败（超时/解析错误）→ 失败连击 +1，下次间隔指数放大"""
    platform = _normalize(platform)
    with _lock:
        state = _load_state(store)
        entry = _platform_entry(state, platform)
        entry["fail_streak"] += 1
        streak = entry["fail_streak"]
        _save_state(state, store)
    logger.warning(
        f"[AntiBan] {platform} 抓取失败 (streak={streak}): {reason[:120]} — "
        f"下次间隔 ×{min(2 ** streak, BACKOFF_CAP)}"
    )


def report_blocked(platform: str, url: str, store=None) -> None:
    """命中风控页 → 一票停。

    L1: 该平台停采至当日结束；
    L2: 24h 内第 2 次 blocked → 全局停采 48h（人工确认前不再采集）。
    """
    platform = _normalize(platform)
    st = _get_store(store)
    now = time.time()
    with _lock:
        state = _load_state(store)
        entry = _platform_entry(state, platform)
        # 仅保留 24h 内的 blocked 记录（平台级观测）
        entry["blocked_ts"] = [t for t in entry["blocked_ts"] if now - t < 86400]
        entry["blocked_ts"].append(now)
        # 全局 blocked 计数：单账号场景任何平台被封都是账号风险信号
        state["global_blocked"] = [
            t for t in state.get("global_blocked", []) if now - t < 86400
        ]
        state["global_blocked"].append(now)
        hits = len(state["global_blocked"])

        if hits >= 2:
            # L2: 全局停采 48h
            state["halt_until"] = now + HALT_L2_SECONDS
            _save_state(state, store)
            st.log_event(platform, url, "halt",
                         f"L2 熔断: 24h 内第 {hits} 次命中风控，全局停采 48h")
            logger.error(
                f"[AntiBan] ⛔ L2 熔断: {platform} 24h 内第 {hits} 次被封 — "
                f"全局停采 48h。单账号场景绝不硬闯，请人工确认后再恢复。"
            )
        else:
            # L1: 平台停采至当日结束
            end_of_day = (datetime.now() + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp()
            entry["stopped_until"] = end_of_day
            _save_state(state, store)
            st.log_event(platform, url, "blocked",
                         "L1 一票停: 命中风控页，该平台今日停止采集")
            logger.error(
                f"[AntiBan] ⛔ L1 一票停: {platform} 命中风控页 ({url[:80]}) — "
                f"该平台今日停止采集，避免封禁升级"
            )


def report_login_redirect(platform: str, url: str, store=None) -> None:
    """抓到了登录页 = Cookie 疑似失效。

    不立即停采（可能是临时跳转），但标记 cookie_suspect：
    后续巡检跳过该平台带登录态的项，避免用死 Cookie 反复撞（撞登录页本身积累风控分）。
    """
    platform = _normalize(platform)
    st = _get_store(store)
    with _lock:
        state = _load_state(store)
        state.setdefault("cookie_suspect", {})[platform] = time.time()
        _save_state(state, store)
    st.log_event(platform, url, "login_redirect", "被重定向到登录页，Cookie 疑似失效")
    logger.warning(f"[AntiBan] {platform} Cookie 疑似失效（登录重定向），已标记待重新扫码")


def clear_cookie_suspect(platform: str, store=None) -> None:
    """登录态刷新后（扫码成功/手动保存）清除疑似标记"""
    platform = _normalize(platform)
    with _lock:
        state = _load_state(store)
        state.get("cookie_suspect", {}).pop(platform, None)
        # 新登录态 = 干净起点，重置该平台的失败连击与停采
        entry = _platform_entry(state, platform)
        entry["fail_streak"] = 0
        entry["stopped_until"] = 0.0
        _save_state(state, store)


def is_cookie_suspect(platform: str, store=None) -> bool:
    platform = _normalize(platform)
    state = _load_state(store)
    return platform in state.get("cookie_suspect", {})


def resume_after_halt(store=None) -> None:
    """人工确认后解除 L2 全局停采"""
    with _lock:
        state = _load_state(store)
        state["halt_until"] = 0.0
        _save_state(state, store)
    logger.info("[AntiBan] 人工确认，L2 全局停采已解除")


# ── ③ robots.txt 合规闸门 ────────────────────────────

def _load_robots_cache(store=None) -> dict[str, Any]:
    raw = _get_store(store).get_config(_ROBOTS_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _fetch_robots(domain: str, scheme: str) -> Optional[str]:
    """拉取 robots.txt 原文；失败返回 None（放行但记录告警）"""
    url = f"{scheme}://{domain}/robots.txt"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": STABLE_UA})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"[AntiBan] robots.txt 拉取失败 {url}: {e}（放行）")
        return None


def robots_allowed(url: str, store=None) -> bool:
    """检查 URL 是否被 robots.txt Disallow。

    - 缓存 24h；拉取失败 → 放行（记录告警）
    - ROBOTS_OVERRIDE=warn_only → 仅告警不拦截（用户显式决策）
    """
    if os.getenv("ROBOTS_OVERRIDE", "").strip() == "warn_only":
        return True
    parsed = urlparse(url)
    domain = parsed.netloc
    if not domain:
        return True

    cache = _load_robots_cache(store)
    entry = cache.get(domain)
    now = time.time()
    if not entry or now - entry.get("fetched_at", 0) > ROBOTS_CACHE_TTL:
        text = _fetch_robots(domain, parsed.scheme or "https")
        entry = {"fetched_at": now, "text": text or ""}
        cache[domain] = entry
        _get_store(store).set_config(_ROBOTS_KEY, json.dumps(cache))

    text = entry.get("text", "")
    if not text:
        return True  # 无 robots 或拉取失败 → 放行

    from urllib.robotparser import RobotFileParser
    rp = RobotFileParser()
    rp.parse(text.splitlines())
    if not rp.can_fetch(STABLE_UA, url):
        _get_store(store).log_event(
            "", url, "robots_skip", "robots.txt Disallow，按合规策略跳过采集"
        )
        logger.info(f"[AntiBan] robots.txt 禁止抓取，跳过: {url[:100]}")
        return False
    return True


def get_crawl_delay(platform: str, store=None) -> float:
    """从 robots 缓存取该平台的 Crawl-delay（无则 0）"""
    from backend.competitor.cookie_manager import PLATFORM_DOMAINS
    domains = PLATFORM_DOMAINS.get(platform, [])
    if not domains:
        return 0.0
    cache = _load_robots_cache(store)
    for domain in domains:
        entry = cache.get(domain.lstrip("."))
        text = (entry or {}).get("text", "")
        if not text:
            continue
        from urllib.robotparser import RobotFileParser
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        delay = rp.crawl_delay(STABLE_UA)
        if delay:
            return float(delay)
    return 0.0


# ── ④ 行为拟人 ───────────────────────────────────────

# 分步滚动脚本: 3~6 步、随机幅度与停顿，模拟人类浏览到页面中后段（价格/评价区）
_SCROLL_JS = """
(async () => {
  try {
    const steps = 3 + Math.floor(Math.random() * 4);
    for (let i = 1; i <= steps; i++) {
      await new Promise(r => setTimeout(r, 400 + Math.random() * 800));
      window.scrollTo({
        top: document.body.scrollHeight * (i / steps) * (0.5 + Math.random() * 0.4),
        behavior: 'smooth'
      });
    }
  } catch (e) {}
})();
"""


def humanize() -> dict[str, Any]:
    """生成本次抓取的行为拟人参数（作为 crawl 的 config_overrides）。

    返回键:
      delay_before_return_html — 随机停留 2.5~6s（替代固定 3s）
      _humanize_js             — 滚动脚本（crawler_runtime 负责与 Cookie 注入合并，
                                 避免覆盖 js_code_before_wait）
    """
    return {
        "delay_before_return_html": round(random.uniform(2.5, 6.0), 2),
        "_humanize_js": _SCROLL_JS,
    }


# ── ⑤ Cookie 龄期管理（减少重复登录）─────────────────

def cookie_age_days(platform: str, store=None) -> Optional[float]:
    """该平台 Cookie 的保存天数（未配置返回 None）"""
    from backend.competitor import cookie_manager
    platform = _normalize(platform)
    if not cookie_manager.get_cookies_for_platform(platform):
        return None
    meta_key = f"crawler_cookies_meta:{platform}"
    try:
        meta = json.loads(_get_store(store).get_config(meta_key) or "{}")
        saved_at = datetime.fromisoformat(meta["saved_at"])
        return (datetime.now() - saved_at).total_seconds() / 86400
    except Exception:
        return None


def stale_cookie_platforms(
    max_age_days: float = COOKIE_STALE_DAYS, store=None
) -> list[str]:
    """Cookie 龄期超过阈值的平台列表（提醒预防性刷新，避免突然失效）"""
    from backend.competitor import cookie_manager
    result = []
    for p in cookie_manager.SUPPORTED_PLATFORMS:
        age = cookie_age_days(p, store=store)
        if age is not None and age > max_age_days:
            result.append(p)
    return result


# ── 观测 ─────────────────────────────────────────────

def stats(store=None) -> dict[str, Any]:
    """防封策略当前状态（供 /competitor/anti-ban/stats 端点）"""
    st = _get_store(store)
    state = _load_state(store)
    now = time.time()
    return {
        "day": state["day"],
        "global_used": state["global_used"],
        "global_budget": GLOBAL_DAILY_BUDGET,
        "halted": now < state.get("halt_until", 0),
        "halt_remaining_h": round(max(0.0, state.get("halt_until", 0) - now) / 3600, 1),
        "platforms": {
            p: {
                "count_today": e["count_today"],
                "daily_cap": _profile(p).daily_cap,
                "fail_streak": e["fail_streak"],
                "stopped": now < e["stopped_until"],
                "blocked_24h": len(e["blocked_ts"]),
                "cookie_suspect": p in state.get("cookie_suspect", {}),
            }
            for p, e in state.get("platforms", {}).items()
        },
        "stale_cookies": stale_cookie_platforms(),
        "recent_events": st.recent_events(limit=20),
    }

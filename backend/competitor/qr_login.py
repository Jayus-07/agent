"""competitor/qr_login.py — 平台扫码登录（Playwright 浏览器方案）

平台 QR 登录 API 已迁移至 SPA 框架（淘宝 Havana nlogin / 京东新版 passport），
旧的 httpx 直接调用 API 已失效（返回 HTML 错误页而非 JSON）。
改用 Playwright 真实浏览器渲染登录页，截图 QR 码，轮询页面状态检测登录成功。

架构:
  _qr_sessions: 全局会话表（token → browser/page 引用）
  start_qr_login()   — 启动浏览器，导航到登录页，截图 QR 码，保持浏览器活跃
  poll_qr_login()    — 检测 URL 变化 / 登录 Cookie，确认后提取 Cookie 并入库
  _cleanup_session() — 关闭浏览器并清理会话

支持的登录页结构:
  淘宝: login.taobao.com/member/login.jhtml → iframe(havanaone) 内 .qrcode-img > canvas
  京东: passport.jd.com/new/login.aspx → 主页面 .qrcode-img > img
  抖音: www.douyin.com 弹窗登录 → 先点「登录」按钮，QR 异步渲染于 #animate_qrcode_container

注意: 浏览器进程在会话期间保持活跃，轮询时直接检查浏览器状态。
      会话超时（120s）后自动关闭浏览器。用户也可退回手动 Cookie 配置。
"""
import asyncio
import base64
import random
import time
import uuid
from enum import Enum
from typing import Any

from backend.competitor.anti_ban import STABLE_UA, STABLE_VIEWPORT
from backend.shared.logger import logger

# 登录会话与抓取会话共用同一恒定指纹（单账号场景: 一致性 = 伪装）
_UA = STABLE_UA

_QR_TIMEOUT = 120  # QR 码有效期（秒），超时自动关闭浏览器


class QrStatus(str, Enum):
    """QR 扫码状态"""
    NEW = "new"             # 等待扫码
    SCANNED = "scanned"     # 已扫码待确认
    CONFIRMED = "confirmed"  # 已确认登录
    EXPIRED = "expired"     # QR 码已过期
    ERROR = "error"         # 异常


# ── 平台配置 ───────────────────────────────────────────────

_PLATFORM_CONFIG: dict[str, dict] = {
    "taobao": {
        "login_url": "https://login.taobao.com/member/login.jhtml",
        "iframe_keyword": "havanaone",      # 登录表单在 iframe 内
        "qr_selector": ".qrcode-img",       # 包裹 QR canvas 的 div
        "login_cookies": ["unb", "login", "uc1", "sgcookie"],  # 仅登录后才会出现
        "scanned_text": ["扫描成功", "请在手机", "确认登录"],
        "login_domain": "login.taobao.com",  # URL 离开该域 = 登录重定向成功
        "pre_click_selector": None,
    },
    "jd": {
        "login_url": "https://passport.jd.com/new/login.aspx",
        "iframe_keyword": None,             # 无 iframe
        "qr_selector": ".qrcode-img",       # 包裹 QR img 的 div
        "login_cookies": ["thor", "pt_key", "pt_pin", "pwdt_id"],
        "scanned_text": ["扫描成功", "请确认", "请在手机"],
        "login_domain": "passport.jd.com",
        "pre_click_selector": None,
    },
    "tmall": {
        # 天猫复用淘宝登录流程
        "login_url": "https://login.taobao.com/member/login.jhtml",
        "iframe_keyword": "havanaone",
        "qr_selector": ".qrcode-img",
        "login_cookies": ["unb", "login", "uc1", "sgcookie"],
        "scanned_text": ["扫描成功", "请在手机", "确认登录"],
        "login_domain": "login.taobao.com",
        "pre_click_selector": None,
    },
    "douyin": {
        # 抖音网页版为弹窗登录：先点顶部「登录」，QR 异步渲染（实测约 14s）
        "login_url": "https://www.douyin.com/",
        "iframe_keyword": None,
        "qr_selector": "#animate_qrcode_container img",  # 178×178 二维码 img
        "login_cookies": ["sessionid", "sid_tt", "uid_tt", "sid_guard"],
        "scanned_text": ["扫描成功", "请在手机", "确认登录"],
        "login_domain": None,  # 弹窗登录不跳转，靠新增 Cookie 判定
        "pre_click_selector": "text=登录",
        "wait_until": "domcontentloaded",  # 视频流站点 networkidle 难收敛
        "goto_timeout": 20000,
        "pre_wait_ms": 3000,
        "qr_timeout": 50000,  # QR 渲染耗时波动大（实测 14s~50s+）
    },
}

SUPPORTED_PLATFORMS = list(_PLATFORM_CONFIG.keys())


def get_supported_platforms() -> list[str]:
    """返回支持扫码登录的平台列表"""
    return SUPPORTED_PLATFORMS


# ── 会话管理 ───────────────────────────────────────────────

# token → {page, browser, context, pw, platform, config, qr_frame, created_at, initial_url}
_qr_sessions: dict[str, dict[str, Any]] = {}


async def _cleanup_session(token: str):
    """关闭浏览器并移除会话"""
    session = _qr_sessions.pop(token, None)
    if not session:
        return
    try:
        browser = session.get("browser")
        pw = session.get("pw")
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    except Exception as e:
        logger.warning(f"[QR-Login] cleanup error for {token[:8]}: {e}")


async def _cleanup_old_sessions():
    """清理所有过期会话"""
    now = time.time()
    expired = [
        token for token, s in _qr_sessions.items()
        if now - s["created_at"] > _QR_TIMEOUT
    ]
    for token in expired:
        await _cleanup_session(token)


async def _auto_cleanup(token: str):
    """超时自动清理（后台任务）"""
    await asyncio.sleep(_QR_TIMEOUT)
    if token in _qr_sessions:
        await _cleanup_session(token)
        logger.info(f"[QR-Login] session {token[:8]}... auto-expired")


# ── 统一调度入口 ──────────────────────────────────────────

async def start_qr_login(platform: str) -> dict[str, Any]:
    """启动 QR 登录：Playwright 浏览器渲染登录页，截图 QR 码。

    返回值:
      {
        "platform": "taobao",
        "token": "uuid",
        "qr_url": "data:image/png;base64,...",  # base64 截图
        "session_cookies": "",                   # 浏览器方案不需要
        "expires_in": 120,
      }
    """
    from playwright.async_api import async_playwright

    config = _PLATFORM_CONFIG.get(platform)
    if not config:
        raise ValueError(
            f"不支持的平台: {platform}，当前支持: {SUPPORTED_PLATFORMS}"
        )

    # 清理过期会话 + 同平台旧会话
    await _cleanup_old_sessions()
    for t, s in list(_qr_sessions.items()):
        if s.get("platform") == platform:
            await _cleanup_session(t)

    token = str(uuid.uuid4())

    # 启动 Playwright + 浏览器
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=_UA,
        viewport=STABLE_VIEWPORT,
        ignore_https_errors=True,
    )
    page = await context.new_page()

    # 导航到登录页
    logger.info(f"[QR-Login] {platform} navigating to {config['login_url']}")
    await page.goto(
        config["login_url"],
        wait_until=config.get("wait_until", "networkidle"),
        timeout=config.get("goto_timeout", 30000),
    )
    # 额外等待 SPA 渲染 QR 码
    await page.wait_for_timeout(config.get("pre_wait_ms", 3000))

    # 部分平台（如抖音）需先点击「登录」按钮才会弹出 QR 弹窗
    if config.get("pre_click_selector"):
        try:
            # 拟人: 点击前先随机滚动浏览，避免直达即点击的机械模式
            await page.mouse.wheel(0, random.randint(150, 450))
            await page.wait_for_timeout(random.randint(800, 2000))
            btn = page.locator(config["pre_click_selector"]).first
            if await btn.count() and await btn.is_visible():
                await btn.click()
                logger.info(f"[QR-Login] {platform} pre-click: {config['pre_click_selector']}")
                await page.wait_for_timeout(3000)
        except Exception as e:
            logger.warning(f"[QR-Login] {platform} pre-click failed: {e}")

    # 定位 QR 码元素（可能在 iframe 内）
    qr_frame = page
    if config["iframe_keyword"]:
        qr_frame = None
        for frame in page.frames:
            if config["iframe_keyword"] in frame.url:
                qr_frame = frame
                break
        if qr_frame is None:
            await browser.close()
            await pw.stop()
            raise RuntimeError(
                f"未找到登录 iframe（keyword={config['iframe_keyword']}），"
                f"页面可能已改版"
            )

    # 等待 QR 码元素出现（抖音等异步渲染平台需更长超时）
    try:
        qr_el = await qr_frame.wait_for_selector(
            config["qr_selector"], timeout=config.get("qr_timeout", 10000)
        )
    except Exception:
        await browser.close()
        await pw.stop()
        raise
    if not qr_el:
        await browser.close()
        await pw.stop()
        raise RuntimeError("QR 码元素未出现，页面可能未正确渲染")

    # 截图 QR 码
    screenshot = await qr_el.screenshot()
    qr_b64 = base64.b64encode(screenshot).decode()

    # 记录初始 Cookie 集（登录页自身设置的跟踪 Cookie）
    # 轮询时只检查新增的 Cookie，避免预登录 Cookie 导致误判
    initial_cookies = await context.cookies()
    initial_cookie_names = {c["name"] for c in initial_cookies}

    # 存储会话
    _qr_sessions[token] = {
        "page": page,
        "browser": browser,
        "context": context,
        "pw": pw,
        "platform": platform,
        "config": config,
        "qr_frame": qr_frame,
        "created_at": time.time(),
        "initial_url": page.url,
        "initial_cookie_names": initial_cookie_names,
    }

    # 调度自动清理
    asyncio.create_task(_auto_cleanup(token))

    logger.info(
        f"[QR-Login] {platform} QR 码已生成 (token={token[:8]}..., "
        f"img={len(qr_b64)} bytes)"
    )

    return {
        "platform": platform,
        "token": token,
        "qr_url": f"data:image/png;base64,{qr_b64}",
        "session_cookies": "",  # 浏览器方案不需要，但保留字段兼容 API
        "expires_in": _QR_TIMEOUT,
    }


async def poll_qr_login(
    platform: str, token: str, session_cookies: str
) -> dict[str, Any]:
    """轮询扫码状态：检查浏览器 URL 变化和登录 Cookie。

    返回值:
      {
        "status": "new" | "scanned" | "confirmed" | "expired" | "error",
        "saved": bool,          # 仅 confirmed 时为 True
        "cookie_length": int,   # 仅 confirmed 时有值
      }
    """
    session = _qr_sessions.get(token)
    if not session:
        return {"status": QrStatus.EXPIRED.value}

    # 检查超时
    if time.time() - session["created_at"] > _QR_TIMEOUT:
        await _cleanup_session(token)
        return {"status": QrStatus.EXPIRED.value}

    page = session["page"]
    config = session["config"]

    # 1. 检查 URL 变化（登录成功后会重定向；仅适用于有独立登录域的平台）
    try:
        current_url = page.url
        initial_url = session.get("initial_url", "")
        login_domain = config.get("login_domain")
        # URL 不再是登录页 = 已重定向 = 登录成功
        if login_domain and login_domain not in current_url:
            logger.info(
                f"[QR-Login] {platform} URL changed: "
                f"{initial_url[:50]} → {current_url[:50]}"
            )
            return await _complete_login(token, session)
    except Exception as e:
        logger.warning(f"[QR-Login] URL check error: {e}")

    # 2. 检查登录 Cookie 是否出现（只看新增的，排除预登录 Cookie）
    try:
        cookies = await session["context"].cookies()
        current_names = {c["name"] for c in cookies}
        initial_names = session.get("initial_cookie_names", set())
        new_names = current_names - initial_names
        matched = [c for c in config["login_cookies"] if c in new_names]
        if matched:
            logger.info(
                f"[QR-Login] {platform} new login cookies detected: {matched}"
            )
            return await _complete_login(token, session)
    except Exception as e:
        logger.warning(f"[QR-Login] cookie check error: {e}")

    # 3. 检查页面文本是否显示 "已扫描" 状态
    try:
        qr_frame = session.get("qr_frame", page)
        text = await qr_frame.evaluate("document.body.innerText")
        for pattern in config.get("scanned_text", []):
            if pattern in text:
                return {"status": QrStatus.SCANNED.value}
    except Exception:
        pass

    # 4. QR 码元素是否仍可见（不可见可能已扫描或过期）
    try:
        qr_frame = session.get("qr_frame", page)
        qr_el = await qr_frame.query_selector(config["qr_selector"])
        if not qr_el:
            return {"status": QrStatus.SCANNED.value}
    except Exception:
        pass

    return {"status": QrStatus.NEW.value}


async def _complete_login(token: str, session: dict) -> dict[str, Any]:
    """从浏览器提取 Cookie 并保存到数据库"""
    try:
        cookies = await session["context"].cookies()
        cookie_str = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies
        )

        if not cookie_str or len(cookie_str) < 20:
            raise RuntimeError(f"提取的 Cookie 异常短: {len(cookie_str)} 字符")

        # 按平台保存 + 记录来源（扫码登录）
        from backend.competitor import cookie_manager
        cookie_manager.save_cookies(session["platform"], cookie_str, "qr")

        # 新登录态 = 干净起点: 清除 Cookie 疑似失效标记与失败连击
        from backend.competitor import anti_ban
        anti_ban.clear_cookie_suspect(session["platform"])

        logger.info(
            f"[QR-Login] {session['platform']} 登录成功，"
            f"Cookie 已保存 ({len(cookie_str)} 字符, {len(cookies)} 个 cookie)"
        )

        await _cleanup_session(token)
        return {
            "status": QrStatus.CONFIRMED.value,
            "saved": True,
            "cookie_length": len(cookie_str),
        }
    except Exception as e:
        logger.error(f"[QR-Login] Cookie 提取失败: {e}", exc_info=True)
        await _cleanup_session(token)
        return {
            "status": QrStatus.ERROR.value,
            "error": f"Cookie 提取失败: {e}",
        }

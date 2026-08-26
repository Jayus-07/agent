"""tools/crawler_runtime.py — 常驻 crawl4ai 运行时（修复 Windows 清理崩溃）

问题背景（2026-08-21 排查）:
  本机 crawl4ai 0.9.2 在 `async with AsyncWebCrawler()` 的 __aexit__ 清理阶段
  会触发 Playwright 硬崩溃（os._exit 级别，无法 try/except 捕获），导致
  web_crawl_tool 每次调用后整个进程被杀（抓取结果拿到了但调用方拿不到）。

修复方案:
  专用后台线程持有一个事件循环 + 单例 AsyncWebCrawler（官方推荐的复用模式）:
  - 浏览器只启动一次，跨多次抓取复用（免每次 ~10s 冷启动）
  - 永不调用 crawler.aclose()，规避崩溃的清理路径
  - run_coroutine_threadsafe 提交任务，线程安全，任意线程可调用

2026-08-23 改造:
  - BrowserConfig: enable_stealth=True（替代 magic=True，后者在 0.9.2 无限挂起）
  - BrowserConfig: 真实 User-Agent + 代理支持（CRAWLER_PROXY 环境变量）
  - CrawlerRunConfig: delay_before_return_html 单位是秒不是毫秒（原 3000=50分钟 → 3.0=3秒）
  - Cookie 注入: BrowserConfig.cookies（浏览器上下文级，支持 HttpOnly）
    + 保留 js_code_before_wait 作为运行时更新通道
"""
import asyncio
import os
import threading
from typing import Any

from backend.shared.logger import logger


class _CrawlerRuntime:
    """后台线程 + 单例 crawler 的 crawl4ai 运行时"""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._lock = threading.Lock()
        self._crawler: Any = None

    # ── 后台线程主体 ────────────────────────────────

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._init_crawler())
        except Exception as e:
            logger.error(f"[CrawlerRuntime] 浏览器初始化失败: {e}")
        finally:
            self._started.set()
        self._loop.run_forever()

    async def _init_crawler(self):
        from crawl4ai import AsyncWebCrawler
        bc = _build_browser_config()
        self._crawler = AsyncWebCrawler(config=bc)
        await self._crawler.start()
        logger.info("[CrawlerRuntime] 单例浏览器已启动（常驻，不关闭，stealth+UA+proxy）")

    # ── 对外接口 ────────────────────────────────────

    def _ensure(self, startup_timeout: float = 60.0):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._started.clear()
            self._thread = threading.Thread(
                target=self._run, name="crawl4ai-runtime", daemon=True
            )
            self._thread.start()
            if not self._started.wait(timeout=startup_timeout):
                raise RuntimeError("crawl4ai 运行时启动超时")

    def submit(self, coro_factory, timeout: float = 60.0) -> Any:
        """把协程提交到常驻循环执行（线程安全）。

        coro_factory: Callable[[crawler], coroutine] — 拿到单例 crawler 构造协程
        """
        self._ensure()
        if self._crawler is None:
            raise RuntimeError("crawl4ai 浏览器不可用（初始化失败）")
        coro = coro_factory(self._crawler)
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)


# ── BrowserConfig 构建 ─────────────────────────────

# UA/viewport 恒定化: 单账号无代理场景下，指纹一致性本身就是伪装，
# 统一从 anti_ban.STABLE_UA 取值（与 qr_login 登录会话同源，禁止各自维护副本）
from backend.competitor.anti_ban import STABLE_UA, STABLE_VIEWPORT


def _build_browser_config() -> Any:
    """构建 BrowserConfig: stealth + UA + proxy + cookies

    enable_stealth=True 替代 CrawlerRunConfig.magic=True（后者在 crawl4ai 0.9.2 无限挂起）。
    stealth 在浏览器上下文级别注入反检测脚本，比 CrawlerRunConfig 级别的 magic 更稳定。

    代理通过 CRAWLER_PROXY 环境变量配置，格式如:
      http://user:pass@proxy.example.com:8080
      socks5://127.0.0.1:1080
    """
    from crawl4ai import BrowserConfig

    # 代理配置（可选）
    proxy_url = os.getenv("CRAWLER_PROXY", "").strip()
    proxy_config = None
    if proxy_url:
        proxy_config = {"server": proxy_url}
        logger.info(f"[CrawlerRuntime] 代理已配置: {proxy_url[:60]}")

    config = BrowserConfig(
        headless=True,
        enable_stealth=True,           # 反检测（替代 magic=True）
        user_agent=STABLE_UA,          # 恒定真实 UA（与登录会话同源）
        viewport=STABLE_VIEWPORT,      # 恒定 viewport，指纹成套保持一致
        proxy_config=proxy_config,     # 代理（可选）
        ignore_https_errors=True,      # 忽略 SSL 错误（代理场景常见）
    )

    # Cookie 注入: 如果 DB 中有 cookies，在浏览器上下文级别注入
    # 优势: 支持 HttpOnly cookies，比 JS document.cookie 更可靠
    # 局限: 需要知道 cookie 的 domain，此处用通配方式
    cookies_list = _load_browser_cookies()
    if cookies_list:
        config.cookies = cookies_list
        logger.info(f"[CrawlerRuntime] BrowserConfig 注入 {len(cookies_list)} 个 cookies")

    return config


def _load_browser_cookies() -> list[dict]:
    """从 DB 读取各平台 cookies，转为带 domain 的 Playwright cookie 格式

    每个平台的 cookie 绑定到该平台域名，浏览器导航时仅向同域发送，
    避免跨平台 Cookie 泄漏（淘宝 cookie 不会发给京东/抖音）。
    """
    from backend.competitor import cookie_manager

    cookies_list = []
    for platform, cookies_str in cookie_manager.all_platform_cookies().items():
        domains = cookie_manager.PLATFORM_DOMAINS.get(platform, [])
        if not domains:
            continue
        for pair in cookies_str.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            for domain in domains:
                cookies_list.append({
                    "name": k.strip(),
                    "value": v.strip(),
                    "domain": domain,
                    "path": "/",
                })
    return cookies_list


_runtime: _CrawlerRuntime | None = None
_runtime_lock = threading.Lock()


def _get_runtime() -> _CrawlerRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = _CrawlerRuntime()
        return _runtime


def crawl(url: str, mode: str = "markdown", timeout: float = 60.0,
         config_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """安全抓取入口。返回 {"ok", "content", "error"}，不抛 crawl4ai 内部异常。

    P1-6: 入口处统一执行 SSRF 校验（scheme 白名单 + DNS 解析后私网/元数据
    IP 拦截），常驻路径与降级路径都被覆盖。

    config_overrides: 可选的 CrawlerRunConfig 覆盖项（如 wait_until、
    delay_before_return_html、js_code 等），用于平台差异化配置。
    """
    from backend.tools.url_guard import assert_url_allowed, UrlBlockedError

    try:
        url = assert_url_allowed(url)
    except UrlBlockedError as e:
        logger.warning(f"[CrawlerRuntime] SSRF 拦截: {url} — {e}")
        return {"ok": False, "content": "", "error": f"URL 被安全策略拦截: {e}"}

    async def _do(crawler):
        from crawl4ai import CrawlerRunConfig

        # 构建 crawl 配置：DOM 就绪即返回 + 额外延迟让 JS 渲染完成
        # 注: networkidle 在电商页面会导致 30s 超时（页面有持续后台请求）
        # 注: magic=True 在 crawl4ai 0.9.2 会导致无限挂起，改用 BrowserConfig.enable_stealth
        #     override_navigator 也由 enable_stealth 覆盖，此处不再重复设置
        config_kwargs = dict(
            page_timeout=60_000,          # 60s 超时（亚马逊等加载慢）
            magic=False,                  # 保持关闭，stealth 由 BrowserConfig 处理
            wait_until="domcontentloaded",  # DOM 就绪即返回，不等待网络空闲
            delay_before_return_html=3.0,  # 额外等 3s 让异步组件加载（单位: 秒）
        )

        # Cookie 补充注入：按 URL 平台选择对应 Cookie（QR 登录/手动保存后即时生效）。
        # JS 注入的局限: 无法设置 HttpOnly cookies（已在 BrowserConfig.cookies 中按域处理）
        from backend.competitor import cookie_manager
        from backend.competitor.adapters import detect_platform
        cookies_str = cookie_manager.get_cookies_for_url(url)
        if cookies_str:
            # 转义单引号防止 JS 注入
            safe_cookies = cookies_str.replace("'", "\\'")
            config_kwargs["js_code_before_wait"] = (
                f"document.cookie='{safe_cookies}; path=/';"
            )
            logger.info(f"[CrawlerRuntime] 运行时注入 {detect_platform(url)} cookies")

        # 应用调用方覆盖项（平台差异化配置 / anti_ban.humanize 行为拟人）
        # _humanize_js 与 Cookie 注入脚本合并而非覆盖，避免丢失登录态
        if config_overrides:
            overrides = dict(config_overrides)
            humanize_js = overrides.pop("_humanize_js", None)
            config_kwargs.update(overrides)
            if humanize_js:
                base_js = config_kwargs.get("js_code_before_wait", "")
                config_kwargs["js_code_before_wait"] = (
                    f"{base_js}\n{humanize_js}" if base_js else humanize_js
                )
        config = CrawlerRunConfig(**config_kwargs)
        result = await crawler.arun(url, config=config)
        if result is None:
            return {"ok": False, "content": "", "error": "无响应"}
        if result.error_message:
            return {"ok": False, "content": "", "error": str(result.error_message)}
        content = result.markdown if mode == "markdown" else result.html
        if not content or not str(content).strip():
            return {"ok": False, "content": "", "error": "页面无有效正文内容"}
        return {"ok": True, "content": str(content), "error": ""}

    try:
        return _get_runtime().submit(_do, timeout=timeout)
    except Exception as e:
        # 运行时整体异常（启动失败/超时）→ 降级：一次性进程内抓取（老路径，
        # 可能触发本机清理崩溃，但至少返回前能把结果写完由调用方处理）
        logger.warning(f"[CrawlerRuntime] 常驻运行时异常，降级一次性抓取: {e}")
        return _fallback_crawl(url, mode, config_overrides)


def _fallback_crawl(url: str, mode: str,
                    config_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """降级路径: 新起事件循环一次性抓取（保留旧 web_crawl_tool 行为）"""

    async def _do():
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
        from backend.competitor import cookie_manager

        bc = _build_browser_config()
        crawler = AsyncWebCrawler(config=bc)
        await crawler.start()
        run_kwargs: dict[str, Any] = dict(
            page_timeout=60_000, magic=False,
            wait_until="domcontentloaded", delay_before_return_html=3.0,
        )
        cookies_str = cookie_manager.get_cookies_for_url(url)
        if cookies_str:
            run_kwargs["js_code_before_wait"] = (
                f"document.cookie='{cookies_str.replace(chr(39), chr(92) + chr(39))}; path=/';"
            )
        if config_overrides:
            overrides = dict(config_overrides)
            humanize_js = overrides.pop("_humanize_js", None)
            run_kwargs.update(overrides)
            if humanize_js:
                base_js = run_kwargs.get("js_code_before_wait", "")
                run_kwargs["js_code_before_wait"] = (
                    f"{base_js}\n{humanize_js}" if base_js else humanize_js
                )
        result = await crawler.arun(url, config=CrawlerRunConfig(**run_kwargs))
        if result is None:
            return {"ok": False, "content": "", "error": "无响应"}
        if result.error_message:
            return {"ok": False, "content": "", "error": str(result.error_message)}
        content = result.markdown if mode == "markdown" else result.html
        text = str(content or "")
        if not text.strip():
            return {"ok": False, "content": "", "error": "页面无有效正文内容"}
        return {"ok": True, "content": text, "error": ""}

    try:
        return asyncio.run(_do())
    except Exception as e:
        return {"ok": False, "content": "", "error": str(e)}

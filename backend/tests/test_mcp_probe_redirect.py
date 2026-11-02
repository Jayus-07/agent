"""probe_http_api 重定向不跟随（S2 SSRF 修复回归）。

探活只允许触达配置 URL 本身：管理端/巡检入口已对配置 URL 做 SSRF 校验，
自动跟随跳转会绕过它把请求打进私网。3xx 响应仍按「可达」语义返回 ok，
但服务端不得向跳转目标发出任何请求。纯 HTTP 桩测试，不依赖 PG/Redis。
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.infra.mcp_registry.probe import probe_http_api


class _RedirectHandler(BaseHTTPRequestHandler):
    """把 HEAD 全部 302 到类属性 target 指向的地址。"""

    target = ""

    def do_HEAD(self):
        self.send_response(302)
        self.send_header("Location", self.target)
        self.end_headers()

    def log_message(self, *_args):
        pass


class _CountingHandler(BaseHTTPRequestHandler):
    """记录收到的 HEAD 次数（用于断言私网监听器零触达）。"""

    hits = 0

    def do_HEAD(self):
        type(self).hits += 1
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


def _start(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_redirect_target_is_never_contacted():
    """公网地址 302 重定向到"私网"监听器：目标不得收到请求，探活仍可达。"""
    private = _start(_CountingHandler)
    redirector = _start(_RedirectHandler)
    _RedirectHandler.target = f"http://127.0.0.1:{private.server_address[1]}/"
    try:
        elapsed, ok, error = probe_http_api(
            f"http://127.0.0.1:{redirector.server_address[1]}/", None)
        assert ok is True, f"3xx 本身应按可达语义返回 ok: {error}"
        assert elapsed >= 0
        assert _CountingHandler.hits == 0, (
            "探活跟随了重定向并触达跳转目标——SSRF 防线失效")
    finally:
        private.shutdown()
        private.server_close()
        redirector.shutdown()
        redirector.server_close()


def test_normal_endpoint_probes_ok():
    """正常 200 端点：探活成功（回归正例）。"""
    server = _start(_CountingHandler)
    try:
        elapsed, ok, error = probe_http_api(
            f"http://127.0.0.1:{server.server_address[1]}/", None)
        assert ok is True, error
        assert _CountingHandler.hits == 1
    finally:
        server.shutdown()
        server.server_close()

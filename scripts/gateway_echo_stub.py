#!/usr/bin/env python
"""网关身份头协议验证用的回显桩（仅测试用，无外部依赖）。

用途：把网关的 AI 路由临时指向本桩，直接观察"网关究竟向下游发了什么头"，
用于验证 P2 的两条硬性验收标准：

  ① 注入正确：持有效 JWT 时下游应收到 X-Auth-Type=jwt / X-User-Id / X-User-Name
  ② 剥离彻底：客户端伪造的 X-User-Id 不得出现在下游请求里

用法：
    python scripts/gateway_echo_stub.py [端口，默认 8099]
    # 另一个终端：让网关把 /api/** 打到本桩
    AI_SERVICE_URL=http://host.docker.internal:8099 docker compose up -d --force-recreate api-gateway

注意：仅为本地验证工具，不得部署到生产。
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _echo(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        payload = {
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _echo
    do_POST = _echo
    do_PUT = _echo
    do_DELETE = _echo

    def log_message(self, *args) -> None:  # 静默：由调用方断言
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"[echo-stub] listening on 0.0.0.0:{port}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), EchoHandler)
    server.daemon_threads = True  # 线程随主进程退出；避免单线程被 keepalive 空闲连接阻塞（B2 实测）
    server.serve_forever()


if __name__ == "__main__":
    main()

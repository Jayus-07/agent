#!/usr/bin/env python
"""echo_headers_stub.py — 回显请求头的验收桩（纯标准库，无外部依赖）

用途：验收 APISIX `gateway-auth` 插件的**身份头剥离 / 注入**行为时，需要一个
「把收到的请求头原样回显出来」的上游。`apisix-test/apisix.yaml` 的路由 `/echo`
就指向 `host.docker.internal:8099`，但桩本身此前没入库（历史会话只留了配置）。

配合 `scripts/gateway_auth_matrix.py` 的场景 10（forged_headers）使用：
  - 若剥离生效  → 响应头里看不到被伪造的 X-User-* / X-Operator-*
  - 若注入生效  → 响应头里能看到网关注入的合法身份头

为什么必须用回显桩（而不是「发个伪造头看接口行为变不变」）：
  py 侧 `resolve_operator_role()` 本轮只认 `X-Internal-Token`，**根本不消费**
  `X-Operator-*`（B4 故意不开）→ 行为不变**证明不了**剥离生效，必须看头。

用法：
  python scripts/echo_headers_stub.py                 # 默认 0.0.0.0:8099
  python scripts/echo_headers_stub.py --port 8099

自测：
  curl -s http://127.0.0.1:8099/echo -H "X-Operator-Role: admin"

响应：200 + JSON {"method","path","headers":{...},"body"}
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "echo-headers-stub/1.0"

    def _handle(self) -> None:
        # 头名统一小写，便于断言；同名头合并为列表
        headers: dict[str, object] = {}
        for key, value in self.headers.items():
            k = key.lower()
            if k in headers:
                existing = headers[k]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    headers[k] = [existing, value]
            else:
                headers[k] = value

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""

        payload = json.dumps(
            {"method": self.command, "path": self.path, "headers": headers, "body": body},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

        # 顺手打一行访问日志，方便肉眼确认请求确实到达了上游
        print(f"[echo] {self.command} {self.path} <- {self.client_address[0]}", flush=True)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle
    do_HEAD = _handle

    def log_message(self, fmt: str, *args) -> None:  # 覆盖默认 stderr 噪音日志
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="回显请求头的验收桩")
    ap.add_argument("--host", default="0.0.0.0",
                    help="监听地址；默认 0.0.0.0 以便容器经 host.docker.internal 访问")
    ap.add_argument("--port", type=int, default=8099, help="监听端口，默认 8099")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), EchoHandler)
    print(f"[echo] listening on http://{args.host}:{args.port}  (Ctrl+C 退出)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[echo] bye", flush=True)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""alert-bridge — Alertmanager webhook → 企微/飞书/钉钉 转发桥（零第三方依赖）

背景（2026-10-06 验收补链）：钉钉/企微群机器人要求特定 msgtype 报文，
Alertmanager 原生 webhook 直连会被拒；本桥把 AM 标准告警 JSON 转成各平台
群机器人格式。未配置任何通道时仅打印日志（交付链仍闭环到桥，docker logs 可查）。

通道（环境变量，全部可选，可同时配多个，同一条告警发给所有已配置通道）：
  ALERT_BRIDGE_WECOM_WEBHOOK     企微群机器人 webhook
  ALERT_BRIDGE_FEISHU_WEBHOOK    飞书群机器人 webhook
  ALERT_BRIDGE_DINGTALK_WEBHOOK  钉钉群机器人 webhook
  ALERT_BRIDGE_DINGTALK_SECRET   钉钉加签密钥（可选）
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = ("0.0.0.0", int(os.environ.get("ALERT_BRIDGE_PORT", "9094")))
CHANNELS = {
    "wecom": os.environ.get("ALERT_BRIDGE_WECOM_WEBHOOK", "").strip(),
    "feishu": os.environ.get("ALERT_BRIDGE_FEISHU_WEBHOOK", "").strip(),
    "dingtalk": os.environ.get("ALERT_BRIDGE_DINGTALK_WEBHOOK", "").strip(),
}
DINGTALK_SECRET = os.environ.get("ALERT_BRIDGE_DINGTALK_SECRET", "").strip()
MAX_MSG_CHARS = 1800  # 群机器人单条消息上限（企微 2048 字节级），超长截断


def _post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        resp.read()


def _dingtalk_url() -> str:
    if not DINGTALK_SECRET:
        return CHANNELS["dingtalk"]
    ts = str(round(__import__("time").time() * 1000))
    sign_str = f"{ts}\n{DINGTALK_SECRET}"
    sign = base64.b64encode(
        hmac.new(DINGTALK_SECRET.encode(), sign_str.encode(), hashlib.sha256).digest()
    ).decode()
    quoted = urllib.parse.quote_plus(sign)
    return f"{CHANNELS['dingtalk']}&timestamp={ts}&sign={quoted}"


def _send(text: str) -> dict:
    text = text[:MAX_MSG_CHARS]
    sent = {}
    if CHANNELS["wecom"]:
        _post_json(CHANNELS["wecom"], {"msgtype": "text", "text": {"content": text}})
        sent["wecom"] = "ok"
    if CHANNELS["feishu"]:
        _post_json(CHANNELS["feishu"], {"msg_type": "text", "content": {"text": text}})
        sent["feishu"] = "ok"
    if CHANNELS["dingtalk"]:
        _post_json(_dingtalk_url(), {"msgtype": "text", "text": {"content": text}})
        sent["dingtalk"] = "ok"
    return sent


def format_alerts(payload: dict) -> str:
    lines = []
    for a in payload.get("alerts", []):
        labels = a.get("labels", {})
        anno = a.get("annotations", {})
        status = a.get("status", "firing").upper()
        lines.append(
            f"[{status}] {labels.get('alertname', '?')}"
            f" ({labels.get('severity', '?')})\n"
            f"{anno.get('summary', '')}\n"
            f"{anno.get('description', '')}\n"
            f"labels: {', '.join(f'{k}={v}' for k, v in sorted(labels.items()) if k not in ('alertname', 'severity', 'alertstate'))}"
        )
    return "\n—— ———— ——\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/healthz", "/health"):
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path not in ("/alert", "/webhook", "/"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
            text = format_alerts(payload)
            active = [k for k, v in CHANNELS.items() if v]
            if active:
                sent = _send(text)
                print(f"[bridge] delivered via {list(sent)}: {text[:120]}...", flush=True)
            else:
                print(f"[bridge] (未配置通道, 仅日志) {text[:300]}", flush=True)
            self.send_response(200)
            self.end_headers()
        except Exception as e:  # 桥自身故障不能让 AM 重试风暴：记日志并 500
            print(f"[bridge] ERROR {e!r}", flush=True)
            self.send_response(500)
            self.end_headers()

    def log_message(self, *args):  # 静默默认访问日志，只留业务日志
        pass


if __name__ == "__main__":
    active = [k for k, v in CHANNELS.items() if v]
    print(f"[bridge] listening :{LISTEN[1]}, channels={active or '无(仅日志)'}", flush=True)
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()

"""坐席 WS 推送链路 e2e（在 agent-app-1 容器内运行）。

步骤：ticket 签发 → WS 握手(hello) → 认领(claimed) → 坐席消息(message.created)
→ 关闭(closed) → 无效 ticket 拒绝(4401)。
"""
import asyncio
import json
import os
import urllib.request

BASE = "http://127.0.0.1:8000"
CONV_ID = "default"
AGENT_ID = "agent-e2e"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


def http_json(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={
            "X-API-Key": os.environ["API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


async def main():
    import websockets

    # 1. 签发 ticket
    info = http_json("POST", "/cs/conversations/agent/ws-ticket")
    check("issue ws-ticket", bool(info.get("ticket")), f"ttl={info.get('ttl')}")

    # 2. WS 握手 + hello
    async def collect_events(ws, want_types, timeout=5.0):
        got = []
        try:
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                got.append(frame)
                if frame.get("type") in want_types:
                    break
        except (asyncio.TimeoutError, TimeoutError):
            pass
        return got

    async with websockets.connect(
        f"{BASE.replace('http', 'ws')}/ws/cs/agent?ticket={info['ticket']}"
    ) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        check("ws handshake hello", hello.get("type") == "hello")

        # 3. 认领 → conversation.claimed
        claim = http_json(
            "POST", f"/cs/conversations/{CONV_ID}/claim", {"agent_id": AGENT_ID}
        )
        check("claim ok", claim.get("handoff_state") == "human_active", str(claim))
        events = await collect_events(ws, {"conversation.claimed"})
        check(
            "push conversation.claimed",
            any(e.get("type") == "conversation.claimed" for e in events),
            str([e.get("type") for e in events]),
        )

        # 4. 坐席消息 → message.created (sender_type=human_agent)
        msg = http_json(
            "POST",
            f"/cs/conversations/{CONV_ID}/agent-messages",
            {"agent_id": AGENT_ID, "content": "您好，人工客服为您服务"},
        )
        check("send agent message", msg.get("sender_type") == "human_agent")
        events = await collect_events(ws, {"message.created"})
        mc = next((e for e in events if e.get("type") == "message.created"), None)
        check(
            "push message.created",
            mc is not None
            and mc.get("conversation_id") == CONV_ID
            and mc["message"]["sender_type"] == "human_agent"
            and isinstance(mc.get("last_id"), int),
            f"last_id={mc.get('last_id') if mc else None}",
        )

        # 5. 关闭 → conversation.closed
        close = http_json(
            "POST", f"/cs/conversations/{CONV_ID}/close", {"agent_id": AGENT_ID}
        )
        check("close ok", close.get("handoff_state") == "closed", str(close))
        events = await collect_events(ws, {"conversation.closed"})
        check(
            "push conversation.closed",
            any(e.get("type") == "conversation.closed" for e in events),
        )

        # 6. 关闭后再发消息应 409
        try:
            http_json(
                "POST",
                f"/cs/conversations/{CONV_ID}/agent-messages",
                {"agent_id": AGENT_ID, "content": "should fail"},
            )
            check("post-close message rejected", False, "no 409 raised")
        except urllib.error.HTTPError as e:
            # 404=无进行中工单 / 409=状态机拒绝，均视为正确拒绝
            check("post-close message rejected", e.code in (404, 409), f"code={e.code}")

    # 7. 无效 ticket 拒绝
    try:
        async with websockets.connect(
            f"{BASE.replace('http', 'ws')}/ws/cs/agent?ticket=bogus"
        ) as ws2:
            await asyncio.wait_for(ws2.recv(), timeout=3)
        check("invalid ticket rejected(4401)", False, "connection accepted")
    except websockets.exceptions.ConnectionClosed as e:
        rcvd = getattr(e, "rcvd", None)
        code = getattr(e, "code", None) or (rcvd.code if rcvd else None)
        check("invalid ticket rejected(4401)", code == 4401, f"code={code}")
    except (asyncio.TimeoutError, TimeoutError):
        check("invalid ticket rejected(4401)", False, "timeout")

    print(f"\n== {len(PASS)} passed, {len(FAIL)} failed ==")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

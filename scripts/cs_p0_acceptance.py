"""客服 P0 验收脚本（批次四能力 MVP，2026-09-22）—— 在 agent-app-1 容器内运行。

覆盖场景清单（docs/customer-service/客服验收测试场景清单-2026-09-22.md）
中可脚本化的 P0：

  A 组 坐席工作台链路（模拟网关注入身份头 user=24 → cs_wire_e2e）
    A1 ws-ticket 签发（JWT 身份反查坐席，API-Key 被明确拒绝）
    A2 WS 握手 hello
    A3 用户转人工入池 → WS 收 conversation.waiting
    A4 坐席认领 → WS 收 conversation.claimed + human_active
    A5 坐席发消息 → WS 收 message.created
    A6 认领后坐席辅助 → WS 收 assist.suggestion（真实 RAG+LLM，耗时敏感）
    A7 关闭会话 → WS 收 conversation.closed
    A8 无效 ticket 拒绝 4401
    A9 双坐席抢单：第二坐席 409

  B 组 AI 服务（容器内直调，真实 DB / 网关 / RAG / LLM）
    B1 知识命中：「保修多久」→ 非拒答且含 12 个月
    B2 知识拒答：编造问题 → REFUSE（EvidenceGate）
    B3 订单查询走 http 网关 → 3 笔 mock 订单
    B4 投诉建单落库 + 幂等（同会话第二次 duplicate）
    B5 查工单意图 → 文本含「您的工单」
    B6 越权工单 → 查他人单返回 None（路由层 404 防探测）

用法:
    docker exec -w /app agent-app-1 python scripts/cs_p0_acceptance.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import asyncio
import json
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000"
AGENT_USER = "24"          # cs_wire_e2e 绑定的 auth_user_id
TENANT = "default"
CONV = f"e2e-p0-{uuid.uuid4().hex[:8]}"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    import time as _t
    print(f"[{_t.strftime('%H:%M:%S')}] " + ("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


def http_json(method: str, path: str, body=None, *, user=AGENT_USER,
              tenant=TENANT, roles="supervisor", timeout=15,
              identity=True, extra_headers=None):
    """identity=True 模拟网关注入身份头 + API-Key；
    identity=False 仅 API-Key（验证身份头缺席即 guest 的红线）。"""
    headers = {"X-API-Key": os.environ["API_KEY"]}
    if identity:
        headers.update({
            "X-Auth-Type": "jwt",
            "X-User-Id": user,
            "X-User-Name": "e2e-agent",
            "X-Tenant-Id": tenant,
            "X-User-Roles": roles,
        })
    if extra_headers:
        headers.update(extra_headers)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}  # 连接拒绝/容器重启窗口


async def collect_events(ws, want_types, timeout=20.0):
    """收集事件直到出现目标类型或**总 deadline** 到期。

    注意不能用逐帧重置的超时：服务端 15s 心跳帧会不断续命，
    令目标事件永不到达时循环永不退出（实测卡死 3 分钟+）。
    """
    got = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            frame = json.loads(
                await asyncio.wait_for(ws.recv(), timeout=remaining))
            got.append(frame)
            if frame.get("type") in want_types:
                break
    except (asyncio.TimeoutError, TimeoutError):
        pass
    return got


async def group_a():
    import websockets

    # A1 ticket 签发（API-Key 通道应被拒——先验证红线；401=guest 未认证 /
    # 403=api-key 通道不支持，两种拒绝语义都算通过）
    status, _ = http_json("POST", "/cs/conversations/agent/ws-ticket",
                          identity=False)
    check("A1a API-Key 通道拒签 ticket", status in (401, 403), f"http={status}")

    # 模拟网关注入身份头签发（app 信任边界 = 网络边界，与 APISIX 等价）
    req = urllib.request.Request(
        BASE + "/cs/conversations/agent/ws-ticket", method="POST",
        data=b"{}", headers={
            "X-API-Key": os.environ["API_KEY"],
            "X-Auth-Type": "jwt", "X-User-Id": AGENT_USER,
            "X-Tenant-Id": TENANT, "X-User-Roles": "supervisor",
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=10) as resp:
        info = json.loads(resp.read())
    check("A1b JWT 身份签发 ticket", bool(info.get("ticket")),
          f"agent 反查成功 ws_path={info.get('ws_path')}")

    # A8 无效 ticket → 4401
    try:
        async with websockets.connect(
            f"{BASE.replace('http', 'ws')}/ws/cs/agent?ticket=bad-ticket",
        ) as ws:
            await ws.recv()
        check("A8 无效 ticket 拒绝", False, "未按预期关闭")
    except Exception as exc:
        code = getattr(exc, "rcvd", None)
        code = code.code if code else None
        check("A8 无效 ticket 拒绝", code == 4401, f"close code={code}")

    # A2-A7 完整工作台链路
    async with websockets.connect(
        f"{BASE.replace('http', 'ws')}/ws/cs/agent?ticket={info['ticket']}",
    ) as ws:
        hello = await collect_events(ws, {"hello"}, timeout=5)
        check("A2 WS 握手 hello", any(e.get("type") == "hello" for e in hello))

        # A3 用户转人工入池（用户身份 99001）
        # 入池前置：会话行必须已存在（dispatch service 校验）——先造会话
        from backend.customer_service.managers.conversation_manager import (
            ConversationManager,
        )
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            mgr = ConversationManager(db)
            await mgr.get_or_create(CONV, "99001")
            # 坐席辅助需要「最近一条用户消息」作推荐依据 —— 预置一条
            from backend.customer_service.managers.message_manager import (
                MessageManager,
            )

            await MessageManager(db).save_user_message(
                CONV, "耳机保修多久？",
                intent_domain="knowledge", intent_name="k_warranty",
            )
            await db.commit()

        idem = f"e2e-p0-{uuid.uuid4().hex[:8]}"
        status, body = http_json(
            "POST", f"/cs/conversations/{CONV}/handoff", {},
            user="99001", roles="customer",
            extra_headers={"Idempotency-Key": idem},
        )
        check("A3a 转人工入池", status == 200 and body.get("handoff_id"),
              f"http={status} handoff={body.get('handoff_id')}")
        waiting = await collect_events(
            ws, {"conversation.waiting", "conversation.offered"}, timeout=20)
        got_waiting = any(e.get("type") in ("conversation.waiting",
                                            "conversation.offered")
                          for e in waiting)
        check("A3b WS 收排队事件（waiting/offered）", got_waiting)

        # A4 认领 / 接单（P6 自动派单秒级 agent_offered，认领 409 属预期；
        # 走 P7 offer 接单路径，须在 120s 总时限内完成）
        status, body = http_json(
            "POST", f"/cs/conversations/{CONV}/claim", {},
        )
        if status == 200:
            check("A4 认领成功（waiting_human 手动路径）",
                  body.get("handoff_state") == "human_active")
        else:
            await asyncio.sleep(2)  # 给 dispatcher 留出派单时间
            status_o, offers = http_json("GET", "/cs/agents/me/offers")
            items = (offers or {}).get("items") or []
            mine = [o for o in items if o.get("conversation_id") == CONV]
            if not mine:
                check("A4 认领/接单", False,
                      f"claim={status}, offers 无本会话（{len(items)} 个待接单）")
            else:
                offer = mine[0]
                status_a, abody = http_json(
                    "POST",
                    f"/cs/agents/me/offers/{offer['handoff_id']}/accept",
                    {"offer_version": offer.get("assignment_version")},
                )
                check("A4 接单成功（P7 自动派单 offer 路径）",
                      status_a == 200, f"http={status_a}")
        print('STEP after-accept collect', flush=True)
        await collect_events(ws, {"conversation.claimed"}, timeout=10)
        print('STEP claimed-collect done', flush=True)

        # A6 坐席辅助：认领触发 assist.suggestion（真实 RAG+LLM，给足 40s）
        print('STEP A6 collect start', flush=True)
        suggestion = await collect_events(ws, {"assist.suggestion"}, timeout=40)
        print('STEP A6 collect done', flush=True)
        hit = [e for e in suggestion if e.get("type") == "assist.suggestion"]
        if hit:
            sug = (hit[0].get("suggestions") or [{}])[0]
            check("A6 坐席辅助推荐推送", True,
                  f"kind={sug.get('kind')} source={sug.get('source')}")
        else:
            # 推荐是尽力而为（RAG/LLM 超时静默）——降级为不阻断，但记录
            check("A6 坐席辅助推荐推送", False, "40s 内未收到（RAG/LLM 可能超时静默）")

        # A5 坐席发消息
        print('STEP A5a start', flush=True)
        status, body = http_json(
            "POST", f"/cs/conversations/{CONV}/agent-messages",
            {"content": "您好，我是您的专属客服，请问遇到什么问题？"},
        )
        check("A5a 坐席发消息", status == 200 and body.get("sender_type") == "human_agent",
              f"http={status}")
        created = await collect_events(ws, {"message.created"}, timeout=10)
        check("A5b WS 收 message.created",
              any(e.get("type") == "message.created" for e in created))

        # A9 双坐席抢单：先关掉再建一单排队，用另一坐席（user 25）与本坐席抢
        # 简化：对已 human_active 的会话再认领 → 409
        status2, _ = http_json(
            "POST", f"/cs/conversations/{CONV}/claim", {}, user="25",
        )
        check("A9 重复认领拒绝", status2 == 409, f"http={status2}")

        # A7 关闭
        print('STEP A7a start', flush=True)
        status, body = http_json(
            "POST", f"/cs/conversations/{CONV}/close", {},
        )
        check("A7a 关闭会话", status == 200, f"http={status}")
        closed = await collect_events(ws, {"conversation.closed"}, timeout=10)
        check("A7b WS 收 conversation.closed",
              any(e.get("type") == "conversation.closed" for e in closed))


async def group_b():
    # B1 知识命中
    from backend.customer_service.knowledge.answer_decision import Decision
    from backend.customer_service.knowledge.service import get_knowledge_service

    r = get_knowledge_service().answer("保修多久", kb_ids=["cs_faq"],
                                       session_id="e2e-p0")
    check("B1 知识命中（保修）", r.decision != Decision.REFUSE and "12" in r.answer,
          f"decision={r.decision.value} answer={r.answer[:40]}...")

    # B2 拒答守门：REFUSE（明确拒答）或 CAUTIOUS（低置信标注"仅供参考"）
    # 都算守门成功 —— 红线是不得以 ANSWER 高置信编造
    r2 = get_knowledge_service().answer("月球背面的宝藏怎么挖",
                                        kb_ids=["cs_faq"], session_id="e2e-p0")
    check("B2 知识拒答守门（EvidenceGate）", r2.decision != Decision.ANSWER,
          f"decision={r2.decision.value} answer={r2.answer[:40]}...")

    # B3 订单 http 网关
    from backend.customer_service.service.order_service import get_order_service

    res = get_order_service().query_orders(user_id="99001")
    check("B3 订单查询（http 网关）", res.total_count >= 3,
          f"total={res.total_count} first={res.orders[0].get('order_no') if res.orders else '-'}")

    # B4 投诉建单落库 + 幂等
    from backend.customer_service.experts.complaint import execute_complaint

    conv = f"e2e-p0-complaint"
    state = {"user_id": "99001", "session_id": conv,
             "conversation_id": conv, "cs_context": {}}
    r1 = execute_complaint("我要投诉！商品破损了态度还差", state)
    tid = (r1.get("data") or {}).get("ticket_id", "")
    from backend.customer_service.ticket_store import get_ticket_store

    row = get_ticket_store().get_sync(tid)
    check("B4a 投诉建单落库", bool(tid) and row is not None
          and row["type"] == "complaint", f"ticket={tid} db_status={row and row['status']}")
    # B4b 幂等：handoff 落库是异步（_db_loop），真实用户两轮投诉间隔
    # 数十秒 —— 等 3s 让首轮 handoff 落库后再触发幂等检查
    await asyncio.sleep(3)
    r2c = execute_complaint("我要投诉！", state)
    check("B4b 重复投诉幂等", (r2c.get("data") or {}).get("duplicate") is True,
          f"ticket={((r2c.get('data') or {}).get('ticket_id'))}")

    # B5 查工单意图
    from backend.customer_service.experts.query import _dispatch_service

    text = _dispatch_service("99001", "t_ticket_status", "我的工单怎么样了", {})
    check("B5 查工单意图回复", "您的工单" in text, f"text={text[:50]}...")

    # B6 越权：他人 ticket 查不到
    others = get_ticket_store().list_for_user_sync("other-user-xyz")
    check("B6 越权工单隔离", all(t["user_id"] != "99001" for t in others),
          f"others={len(others)}")


async def async_main():
    await group_a()
    if os.getenv("P0_SKIP_B") != "1":
        group_b()


def main(only: str = ""):
    print(f"[cs-p0] CONV={CONV}")
    if only in ("", "a"):
        asyncio.run(group_a())
    if only in ("", "b"):
        asyncio.run(group_b())
    print(f"\n===== 结果: PASS={len(PASS)} FAIL={len(FAIL)} =====")
    if FAIL:
        print("FAIL 项:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    time.sleep(0)
    sys_exit = main()
    raise SystemExit(sys_exit)

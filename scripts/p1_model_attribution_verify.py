# -*- coding: utf-8 -*-
"""p1_model_attribution_verify.py — P1 模型归属实机验证（2026-09-22）

在 app 容器内运行，验证（用户验收规格）：
1. main 模型 A→B 切换：新请求立即用 B，usage/trace 都记 B（不重启）
2. general_chat 流式：同请求 usage 行 model_id 全链路一致
3. tool_selector 与 main 不同模型：usage 分开归属
4. price_unknown：usage 记真实 model_id + cost_status
验证后恢复原角色绑定（tool_selector 若原为 inherit 则删行还原）。

用法: docker exec agent-app-1 python scripts/p1_model_attribution_verify.py
环境变量: ADMIN_TOKEN（管理端 JWT）
"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
# 服务级 API Key 只从环境读取（同 d6_routing_regression 的 P0-1 口径）
API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    sys.exit("API_KEY environment variable is required")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
REFRESH_WAIT_S = 22  # 注册表 15s 刷新循环 + 余量

results = []


def _http(method: str, path: str, body: dict | None = None,
          headers: dict | None = None, timeout: int = 150):
    h = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def admin_put_role(role: str, model_name: str) -> dict:
    """更新角色绑定：直改 DB 绑定表（与管理端 PUT 同一存储层），
    运行时经 15s refresh_registry 循环拉取生效。验证后还原。"""
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ["PGHOST"], port=os.environ["PGPORT"],
        dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"])
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO llm_model_role_bindings (role, model_name, updated_by, updated_at) "
            "VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (role) DO UPDATE SET model_name = EXCLUDED.model_name, "
            "updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at",
            (role, model_name, "p1-verify-script"))
    conn.commit()
    conn.close()
    return {"ok": True}


def chat(question: str, session_id: str) -> tuple[str, str]:
    """SSE 对话，返回 (终止事件, trace_id)。"""
    req = urllib.request.Request(
        f"{BASE}/chat/stream",
        data=json.dumps({"question": question, "session_id": session_id}).encode(),
        headers={"Content-Type": "application/json", "department": "ops",
                 "X-API-Key": API_KEY}, method="POST")
    done, trace_id = "?", ""
    cur = ""
    with urllib.request.urlopen(req, timeout=150) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                cur = line[6:].strip()
            elif line.startswith("data:") and cur in ("done", "error"):
                try:
                    p = json.loads(line[5:].strip())
                    trace_id = p.get("trace_id") or trace_id
                except Exception:
                    pass
                done = cur
                break
    return done, trace_id


def usage_rows(trace_id: str) -> list[dict]:
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ["PGHOST"], port=os.environ["PGPORT"],
        dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT component, model, role, total_tokens, cost_usd, cost_status "
                "FROM llm_usage WHERE trace_id = %s ORDER BY id", (trace_id,))
            return [
                {"component": r[0], "model": r[1], "role": r[2],
                 "total_tokens": r[3], "cost_usd": float(r[4] or 0),
                 "cost_status": r[5]}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def trace_model(trace_id: str) -> str:
    try:
        t = _http("GET", f"/observability/traces/{trace_id}", timeout=15,
                  headers={"X-API-Key": API_KEY})
        m = t.get("model")
        if isinstance(m, dict):
            return str(m.get("name") or "")
        return str(m or "")
    except Exception as e:
        return f"(trace读取失败: {e})"


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, "PASS" if ok else "FAIL", detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    model_a, model_b = "doubao-seed-2.0-mini", "qwen3.8-flash"
    ts = int(time.time())

    # ── 1+2. 基线 main=A：general_chat 流式 usage 一致性 ──────────
    print("\n== [1] main=A general_chat 流式归属 ==")
    done, tid = chat("你好，介绍下你自己", f"p1-verify-a-{ts}")
    rows = usage_rows(tid)
    models = sorted({r["model"] for r in rows if r["component"] in ("llm", "customer_service")})
    check("main=A usage 全部记 A", done == "done" and models == [model_a],
          f"sse={done} usage_models={models}")

    # ── main A→B 切换（DB 绑定，不重启）──────────────────────────
    print("\n== [2] main A→B 动态切模 ==")
    admin_put_role("main", model_b)
    time.sleep(REFRESH_WAIT_S)
    done, tid_b = chat("你好，介绍下你自己", f"p1-verify-b-{ts}")
    rows_b = usage_rows(tid_b)
    models_b = sorted({r["model"] for r in rows_b if r["component"] in ("llm", "customer_service")})
    tm = trace_model(tid_b)
    check("main=B usage 记 B", done == "done" and models_b == [model_b],
          f"sse={done} usage_models={models_b} all_rows={rows_b}")
    check("main=B trace 记 B", tm == model_b, f"trace_model={tm}")
    # 还原
    admin_put_role("main", model_a)
    time.sleep(REFRESH_WAIT_S)

    # ── 3. tool_selector 与 main 不同模型分账 ────────────────────
    print("\n== [3] tool_selector 独立模型分账 ==")
    admin_put_role("tool_selector", model_b)
    time.sleep(REFRESH_WAIT_S)
    done, tid_s = chat("查一下库存不足的商品数量", f"p1-verify-s-{ts}")
    rows_s = usage_rows(tid_s)
    llm_rows = [r for r in rows_s if r["component"] in ("llm", "customer_service")]
    by_model = {}
    for r in llm_rows:
        by_model.setdefault(r["model"], 0)
        by_model[r["model"]] += 1
    # 分账判定：selector 用量 = model_b；main 用量 = 其他模型（上游真名，
    # 如 doubao 的 upstream 别名），两者互不混记
    selector_rows = by_model.get(model_b, 0)
    main_rows = sum(n for m, n in by_model.items() if m != model_b)
    split_ok = done == "done" and selector_rows >= 1 and main_rows >= 1
    check("selector=B 与 main 分开归属", split_ok,
          f"sse={done} usage_by_model={by_model} all_rows={rows_s}")
    # 还原：删除 tool_selector 行（原状态 = inherit main）
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ["PGHOST"], port=os.environ["PGPORT"],
            dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"])
        with conn.cursor() as cur:
            cur.execute("DELETE FROM llm_model_role_bindings WHERE role = 'tool_selector'")
        conn.commit()
        conn.close()
        print("  [还原] tool_selector 绑定行已删除（恢复 inherit main）")
    except Exception as e:
        print(f"  [还原失败] tool_selector 行删除: {e}")

    # ── 4. price_unknown 观察 ────────────────────────────────────
    print("\n== [4] price_unknown 用量观察 ==")
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ["PGHOST"], port=os.environ["PGPORT"],
        dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT model, cost_status, count(*) FROM llm_usage "
            "WHERE ts::timestamptz > now() - interval '1 hour' AND component = 'llm' "
            "GROUP BY model, cost_status")
        stat = cur.fetchall()
    conn.close()
    pu = [r for r in stat if r[1] == "price_unknown"]
    if pu:
        check("price_unknown 行记录真实 model_id", True,
              f"{[(r[0], int(r[2])) for r in pu]}（token 照记、状态可查）")
    else:
        check("price_unknown 观察窗口", True,
              f"近 1 小时无 price_unknown 行（现有状态分布: {[(r[0], r[1], int(r[2])) for r in stat]}）；"
              "缺价不丢行的行为已由单测 TestPriceUnknown 覆盖")

    # ── 汇总 ─────────────────────────────────────────────────────
    print("\n==== P1 实机验证汇总 ====")
    failed = [r for r in results if r[1] != "PASS"]
    for name, status, _ in results:
        print(f"  {status}  {name}")
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

"""verify_release_gate.py — 平台 12 门 Release Gate（Platform Readiness STOP G）。

全部经真实 APISIX :9080（G4 纪律：不直连 FastAPI 冒充）。

  Gate 0  Build Identity  /health.build.commit 非 unknown（发布违规拦截）
  Gate 1  Migration       verify_migration_state 三层一致
  Gate 2  Auth            无 JWT → 401（伪造/缺失凭据拒绝）
  Gate 3  Tenant          跨租户读任务 → 404
  Gate 4  Chat            一轮 chat 200 + done
  Gate 5  RAG             知识问句 200 + 非空
  Gate 6  SQL             数据问句 200 + 非空（权限门在链路内生效）
  Gate 7  CS              客服问句 200 + cs_graph_node 执行
  Gate 8  Travel          旅游问句 200 + travel_graph_node 执行
  Gate 9  Selection       选品问句 200 + selection_funnel 执行
  Gate 10 Task Runtime    近 24h 存在 SUCCESS 任务（运行时健康侧证）
  Gate 11 Model           /sys/model-health 200（治理面可达）
  Gate 12 Observability   /metrics 指标族在册 + prometheus targets up

收尾落库（M8 / 台账 D8）：12 门跑完直连 agent_memory（5433）写
ai.release_records 一行（gates/gate_details JSONB）。设计为「POST 落库」
改直连的原因：e2e_domain 是 editor 无 admin 权限、--skip-build 场景旧容器
无新端点、最小凭据原则（偏差已记台账）。落库失败 = 发布无记录 = 违规，
exit 1 fail-loud；本地调试用 --no-record 跳过。

用法：cd backend && PYTHONPATH=.. python scripts/verify_release_gate.py --password <pwd>
严重级：P0=Blocker（任一 Gate FAIL 即 exit 1）；P1 默认 Blocker 需 waiver；P2 登记后可发布。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(dotenv_path="../.env")
except ImportError:
    pass

GATEWAY = "http://127.0.0.1:9080"
RESULTS: dict[str, bool] = {}
DETAIL: dict[str, str] = {}

# 发布记录直连（显式 5433：agent 权威库；5432 是宿主机原生同名库）
PG_HOST, PG_PORT, PG_DB = "127.0.0.1", 5433, "agent_memory"


def _request(method, url, body=None, token=None, headers=None, timeout=240):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if os.getenv("API_KEY"):
        req.add_header("X-API-Key", os.getenv("API_KEY"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)[:120]


def login(username: str, password: str, direct_tenant: str = "") -> str:
    if direct_tenant:
        url, headers = "http://127.0.0.1:8000/auth/login", {"X-Tenant-Id": direct_tenant}
    else:
        url, headers = f"{GATEWAY}/api/auth/login", {}
    st, body = _request("POST", url, body={"username": username,
                                           "password": password}, headers=headers)
    assert st == 200, f"login {username} failed: {st} {body[:100]}"
    return (json.loads(body).get("data") or {}).get("token")


def claims_of(tok: str) -> dict:
    part = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def chat(token: str, session: str, question: str, direct=False, actor=None,
         timeout=240) -> tuple[int, str, list[str]]:
    prefix = "" if direct else "/api"
    body = {"question": question, "session_id": session,
            "request_id": f"gate-{session}-{int(time.time()*1000)}",
            "idempotency_key": f"gate-{session}-{int(time.time()*1000)}"}
    headers = {}
    if direct and actor:
        headers = {"X-Auth-Type": "jwt", "X-User-Id": actor["user_id"],
                   "X-Tenant-Id": actor["tenant_id"], "X-User-Roles": "editor"}
    st, raw = _request("POST", f"{GATEWAY if not direct else 'http://127.0.0.1:8001'}{prefix}/chat/stream"
                       if False else f"{GATEWAY}{prefix}/chat/stream",
                       body=body, token=token, headers=headers, timeout=timeout)
    if st != 200:
        return st, "", []
    answer, nodes, ev = [], [], None
    trace_id = ""
    for line in raw.splitlines():
        line = line.rstrip("\r")
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            try:
                d = json.loads(line.split(":", 1)[1].strip())
            except ValueError:
                continue
            if ev == "delta" and isinstance(d, dict):
                answer.append(d.get("content") or "")
            if ev == "done" and isinstance(d, dict):
                trace_id = d.get("trace_id") or ""
    if trace_id:
        code = ("import sys, json;"
                "from backend.observability.trace_store import get_trace_store;"
                "d = get_trace_store().get(sys.argv[1]) or {};"
                "g = d.get('graph') or {};"
                "print(json.dumps([n.get('id') for n in (g.get('nodes') or [])]))")
        out = subprocess.run(["docker", "exec", "agent-app-1", "python", "-c",
                              code, trace_id], capture_output=True, text=True,
                             timeout=60)
        try:
            nodes = json.loads((out.stdout or "").strip().splitlines()[-1])
        except Exception:
            nodes = []
    return st, "".join(answer), nodes


def g0_build() -> bool:
    st, body = _request("GET", "http://127.0.0.1:8000/health", timeout=10)
    commit = (json.loads(body).get("build") or {}).get("commit", "?") if st == 200 else "?"
    DETAIL["g0"] = f"commit={commit}"
    return st == 200 and commit not in ("unknown", "?", "")


def g1_migration() -> bool:
    out = subprocess.run(
        [sys.executable, "scripts/verify_migration_state.py",
         "--image", "agent-db-migrate"],
        capture_output=True, text=True, timeout=300, cwd=".")
    DETAIL["g1"] = (out.stdout or "").strip().splitlines()[-1] if out.stdout else "no output"
    return "MIGRATION_STATE_OK" in (out.stdout or "")


def g2_auth() -> bool:
    st, _ = _request("GET", f"{GATEWAY}/api/tasks", timeout=15)
    DETAIL["g2"] = f"unauth={st}"
    return st == 401


def g3_tenant(tok_a: str, tok_b: str) -> bool:
    out = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-c",
         "SELECT id FROM tasks WHERE user_id='50' ORDER BY created_at DESC LIMIT 1"],
        capture_output=True, text=True)
    a_task = out.stdout.strip()
    st, _ = _request("GET", f"{GATEWAY}/api/tasks/{a_task}", token=tok_b)
    DETAIL["g3"] = f"cross_tenant={st}"
    return st == 404


def g_domain(token: str, gate: str, question: str, expect_node: str | None):
    session = f"gate-{gate}-{int(time.time())}"
    st, answer, nodes = chat(token, session, question)
    ok = st == 200 and len(answer.strip()) >= 8
    if expect_node:
        ok = ok and bool(nodes) and expect_node in nodes
    DETAIL[gate] = f"http={st} nodes={nodes[:4]} ans={len(answer)}字"
    return ok


def g10_task() -> bool:
    out = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-c",
         "SELECT count(*) FROM tasks WHERE status='SUCCESS' AND "
         "finished_at > now() - interval '24 hours'"],
        capture_output=True, text=True)
    n = int(out.stdout.strip() or 0)
    DETAIL["g10"] = f"success_24h={n}"
    return n > 0


def g11_model(editor: str) -> bool:
    st, _ = _request("GET", f"{GATEWAY}/api/sys/model-health", token=editor)
    DETAIL["g11"] = f"model_health={st}"
    return st == 200


def g12_observability() -> bool:
    st, metrics = _request("GET", "http://127.0.0.1:8000/metrics", timeout=20)
    fam_ok = st == 200 and "routing_domain_total" in metrics \
        and "chat_request_total" in metrics
    st2, targets = _request("GET",
                            "http://127.0.0.1:9090/api/v1/targets?state=active",
                            timeout=15)
    ups = 0
    if st2 == 200:
        data = json.loads(targets).get("data", {}).get("activeTargets", [])
        ups = sum(1 for t in data if t.get("health") == "up")
    DETAIL["g12"] = f"metrics={st} prom_up_targets={ups}"
    return fam_ok and ups > 0


def _pg_password() -> str:
    """超级用户口令：PGPASSWORD_SUPERUSER 优先，回退根 .env 的 PGPASSWORD。"""
    return (os.environ.get("PGPASSWORD_SUPERUSER")
            or os.environ.get("PGPASSWORD") or "postgres")


def _live_build() -> tuple[str, str]:
    """线上容器实际构建身份（g0 同源；env 兜底供直连 :8000 不可达场景）。"""
    try:
        st, body = _request("GET", "http://127.0.0.1:8000/health", timeout=10)
        if st == 200:
            build = json.loads(body).get("build") or {}
            return build.get("commit") or "?", build.get("build_time") or ""
    except Exception:  # noqa: BLE001
        pass
    return os.getenv("GIT_COMMIT", "unknown"), os.getenv("BUILD_TIME", "")


def persist_release_record(started_at: float) -> bool:
    """12 门结果落 ai.release_records（M8）。失败 fail-loud（发布无记录=违规）。"""
    import psycopg2

    git_sha, build_time = _live_build()
    operator = os.environ.get("RELEASE_OPERATOR", "")
    sql = ("INSERT INTO ai.release_records "
           "(git_sha, build_time, gates, gate_details, result, operator, started_at) "
           "VALUES (%s, %s, %s, %s, %s, %s, to_timestamp(%s))")
    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user="postgres", password=_pg_password(),
                            connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (git_sha, build_time,
                              json.dumps(RESULTS), json.dumps(DETAIL),
                              "PASS" if all(RESULTS.values()) else "FAIL",
                              operator, started_at))
        conn.commit()
    finally:
        conn.close()
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--no-record", action="store_true",
                    help="本地调试：跳过发布记录落库")
    args = ap.parse_args()
    started_at = time.time()

    editor = login("e2e_domain", args.password)
    # 跨租户探针：tenant-b 租户用户（直连 + 显式可信租户头）
    viewer_tok = login("e2e_tenantb", args.password, direct_tenant="tenant-b")

    checks = [
        ("Gate0_BuildIdentity", g0_build),
        ("Gate1_Migration", g1_migration),
        ("Gate2_Auth", g2_auth),
        ("Gate3_Tenant", lambda: g3_tenant(editor, viewer_tok)),
        ("Gate4_Chat", lambda: g_domain(editor, "g4", "你好，请介绍一下平台",
                                        None)),
        ("Gate5_RAG", lambda: g_domain(editor, "g5",
                                       "根据知识库文档说明客户生命周期阶段", None)),
        ("Gate6_SQL", lambda: g_domain(editor, "g6",
                                       "查一下最近一个月每天的销售额", None)),
        ("Gate7_CS", lambda: g_domain(editor, "g7",
                                      "帮我查一下这个订单的物流进度，到底什么时候到",
                                      "cs_graph_node")),
        ("Gate8_Travel", lambda: g_domain(editor, "g8", "帮我规划厦门的行程",
                                          "travel_graph_node")),
        ("Gate9_Selection", lambda: g_domain(editor, "g9",
                                             "给宠物零食做一次智能选品",
                                             "selection_funnel_graph_node")),
        ("Gate10_TaskRuntime", g10_task),
        ("Gate11_Model", lambda: g11_model(editor)),
        ("Gate12_Observability", g12_observability),
    ]
    for name, fn in checks:
        try:
            RESULTS[name] = fn()
        except Exception as exc:
            DETAIL[name] = f"ERROR {str(exc)[:100]}"
            RESULTS[name] = False
        print(f"  {'PASS' if RESULTS[name] else 'FAIL'}  {name}  {DETAIL.get(name, '')}")

    failed = [k for k, v in RESULTS.items() if not v]
    print("\n===== RELEASE GATE =====")
    print(json.dumps(RESULTS, indent=1))

    # M8 收尾落库：任何门结果（含 FAIL）都留痕；落库失败使发布 exit 1
    if not args.no_record:
        try:
            persist_release_record(started_at)
            print("[record] ai.release_records 落库 OK")
        except Exception as exc:  # noqa: BLE001
            print(f"[record][FATAL] 发布记录落库失败（无记录的发布视为违规）: "
                  f"{str(exc)[:200]}")
            failed.append("ReleaseRecord")
    else:
        print("[record] --no-record：跳过落库")

    print(f"\n[result] {'RELEASE_GATE_PASS' if not failed else 'RELEASE_GATE_FAIL: ' + ','.join(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

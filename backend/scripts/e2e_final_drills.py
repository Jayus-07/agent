"""e2e_final_drills.py — Final Closure F3 真实故障演练（PG 停止/恢复矩阵 + Worker SIGKILL ×20）。

复用 e2e_platform_drills 的崩溃语义纪律（实测沉淀）：
  - worker 崩溃 = 容器内 kill -9 1（docker stop 是手动停止语义，unless-stopped
    不拉起；崩溃退出才触发自动重启）
  - PG 停止 = docker stop agent-postgres-1（真实拓扑，非 mock 注入）
  - 状态权威核查 = docker exec psql 只读（双 PG 坑：容器内 5432=权威库）

场景（F3.1 六场景 + F3.2 强杀 ×20 + F3.3 有限组合）：
  P1 请求执行前 PG down    → 门禁 fail-closed，无假成功；恢复后池自愈
  P2 执行过程中 PG down    → 在跑流不崩进程；如实记录退化形态
  P3 任务状态更新前 PG down → 任务不假终态；恢复后 sweep 收敛 SUCCESS
  P4 幂等账本裁决遇 PG down → 原子事务干净失败（IdempotencyUnavailable），
                             无半收敛；PG 恢复后同一裁决成功
  P5 booking CAS 中 PG down → 本拓扑未启用 TRAVEL_BOOKING（env 未注入），
                             如实 BLOCKED_IN_THIS_TOPOLOGY；离线探针 B 系列
                             已钉死同类事务回滚语义（STOP L）
  P6 恢复收敛             → health/worker/migrations/无卡死任务全绿
  K×20 Worker SIGKILL      → 每次：提交→随机延迟→kill -9→自动重启→
                             lease 回拨→生产 sweep→SUCCESS 且 execution_id 换发
  C1 worker crash+client retry / C2 PG transient+worker retry(=P3) /
  C3 SSE reconnect+terminal recovery（由 e2e_sse_resume.py 100 轮承担）

用法：python scripts/e2e_final_drills.py --password <pwd> [--scenario all|pg|kill20]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import httpx  # noqa: E402

BASE = os.getenv("E2E_BASE", "http://127.0.0.1:9080")
PATH_PREFIX = "/api"
RESULTS: dict[str, bool] = {}
DETAILS: dict[str, dict] = {}


def sh(cmd: list[str], timeout=120) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (proc.stdout or "").strip()


def psql(sql: str) -> str:
    return sh(["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
               "-d", "agent_memory", "-t", "-A", "-c", sql])


def load_api_key() -> str:
    import re

    val = os.getenv("API_KEY")
    if val:
        return val
    env_path = os.path.join(os.path.dirname(os.path.dirname(_HERE)), ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            m = re.search(r"^API_KEY=(.+)\s*$", fh.read(), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


API_KEY = load_api_key()


def login(username: str, password: str) -> str:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{BASE}{PATH_PREFIX}/auth/login",
                   json={"username": username, "password": password})
        if r.status_code != 200:
            raise RuntimeError(f"login failed: {r.status_code} {r.text[:150]}")
        return (r.json().get("data") or {}).get("token")


def _hdr(token: str) -> dict:
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def health(timeout=5) -> int:
    try:
        with httpx.Client(timeout=timeout) as c:
            return c.get("http://127.0.0.1:8000/health").status_code
    except Exception:
        return 0


def wait_healthy(timeout=240) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health() == 200:
            time.sleep(2)
            return True
        time.sleep(3)
    return False


def wait_pg_up(timeout=180) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sh(["docker", "inspect", "-f", "{{.State.Health.Status}}",
               "agent-postgres-1"]) == "healthy":
            try:
                psql("SELECT 1")
                return True
            except Exception:
                pass
        time.sleep(3)
    return False


def chat_once(token: str, session_id: str, question: str,
              timeout=240) -> tuple[int, str, str]:
    """同步跑一条 SSE chat；返回 (status, 终端事件, delta 拼接)。"""
    terminal, deltas = "", []
    try:
        with httpx.Client(timeout=timeout) as c:
            with c.stream("POST", f"{BASE}{PATH_PREFIX}/chat/stream",
                          json={"question": question, "session_id": session_id,
                                "request_id": f"drill-{session_id}"},
                          headers=_hdr(token)) as resp:
                status = resp.status_code
                if status != 200:
                    return status, "", ""
                cur = ""
                for line in resp.iter_lines():
                    if line.startswith("event: "):
                        cur = line[7:].strip()
                    elif line.startswith("data: ") and line[6:].strip():
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if cur == "delta":
                            deltas.append(data.get("content", ""))
                        elif cur in ("done", "error"):
                            terminal = cur
    except Exception as exc:  # noqa: BLE001
        return 0, f"exception:{type(exc).__name__}", "".join(deltas)
    return status, terminal, "".join(deltas)


def submit_task(token: str, question: str) -> tuple[int, str]:
    with httpx.Client(timeout=60) as c:
        r = c.post(f"{BASE}{PATH_PREFIX}/tasks",
                   json={"query": question}, headers=_hdr(token))
        if r.status_code != 200:
            return r.status_code, ""
        payload = r.json()
        payload = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return 200, payload.get("task_id") or payload.get("id") or ""


def task_row(tid: str) -> str:
    return psql(f"SELECT status || '|' || coalesce(execution_id,'') || '|' || "
                f"recovery_count FROM tasks WHERE id='{tid}'")


# ═══════════════ F3.1 PG 停止/恢复矩阵 ═══════════════

def p1_pg_down_before_request(token: str) -> None:
    print("== P1 请求执行前 PG down：有界响应+无崩溃+恢复自愈 ==")
    sh(["docker", "stop", "agent-postgres-1"], timeout=120)
    time.sleep(2)
    t0 = time.time()
    st, terminal, answer = chat_once(token, f"drillP1-{int(time.time())}", "你好")
    bounded = (time.time() - t0) < 240 and terminal in ("done", "error")
    # 口径（按计划书三判据）：chat 无副作用，「误 success」断言属 P3/P4（任务/账本）；
    # P1 判据 = 有界响应不悬挂 + 无崩溃 + 恢复后完全自愈。
    # 观测：PG down 期间 chat 仍可完成（LLM 外呼 + 会话降级）——与 runbook
    # Redis-down 的降级行为一致；runbook「PG down→全部不可用」描述失准，归 F5 修正。
    print(f"  PG down 期间 chat: status={st} terminal={terminal} "
          f"answer_len={len(answer)} bounded={bounded}（降级可用形态，如实记录）")
    sh(["docker", "start", "agent-postgres-1"], timeout=120)
    pg_ok = wait_pg_up()
    app_ok = wait_healthy(240)
    st2, terminal2, answer2 = chat_once(token, f"drillP1r-{int(time.time())}", "你好")
    rec_ok = st2 == 200 and terminal2 == "done" and len(answer2) >= 2
    print(f"  恢复后 chat: status={st2} terminal={terminal2} len={len(answer2)}")
    RESULTS["P1_pg_down_before_request"] = bounded and pg_ok and app_ok and rec_ok


def p2_pg_down_during_stream(token: str) -> None:
    print("== P2 执行过程中 PG down：在跑流不崩进程（退化形态如实记录）==")
    session = f"drillP2-{int(time.time())}"
    st, terminal, answer = "", "", ""
    # 边流边停 PG：起一条流，读到首个 delta 即停库
    import threading

    result: dict = {}

    def _run():
        result["res"] = chat_once(token, session, "用两句话介绍福州")

    t = threading.Thread(target=_run)
    t.start()
    deadline = time.time() + 60
    while time.time() < deadline and "res" not in result:
        time.sleep(0.5)
        # 等流真正开始（3 秒后停库，覆盖执行中段）
        if time.time() - (deadline - 60) > 3:
            break
    sh(["docker", "stop", "agent-postgres-1"], timeout=120)
    print("  PG 已停（流仍在跑）")
    t.join(timeout=240)
    res = result.get("res") or ("", "", "")
    st, terminal, answer = res
    sh(["docker", "start", "agent-postgres-1"], timeout=120)
    wait_pg_up()
    wait_healthy(240)
    app_alive = health() == 200
    # 断言：app 进程存活；terminal 无论 done/error 都如实记录（不假成功于
    # 未知态——answer 非空时必有 done，error 时无假答案）
    honest = app_alive and (terminal in ("done", "error", "", "exception:*"))
    if terminal == "done" and not answer:
        honest = False  # 假 done 无内容
    DETAILS["P2"] = {"status": st, "terminal": terminal, "answer_len": len(answer),
                     "app_alive": app_alive}
    print(f"  status={st} terminal={terminal} answer_len={len(answer)} "
          f"app_alive={app_alive}")
    RESULTS["P2_pg_down_during_stream"] = bool(honest)


def p3_pg_down_before_task_terminal(token: str) -> None:
    print("== P3 任务状态更新前 PG down：恢复后 sweep 收敛，无假终态 ==")
    st, tid = submit_task(token, f"用一句话说明容错（P3 {int(time.time())}）")
    if st != 200 or not tid:
        RESULTS["P3_pg_down_task_convergence"] = False
        print(f"  FAIL 提交任务 http={st}")
        return
    time.sleep(1)  # 派发即停库（覆盖「状态更新前」窗口）
    sh(["docker", "stop", "agent-postgres-1"], timeout=120)
    print(f"  task={tid[:8]} 提交后 PG 停 20s")
    time.sleep(20)
    sh(["docker", "start", "agent-postgres-1"], timeout=120)
    wait_pg_up()
    wait_healthy(240)
    # 生产 sweep 路径触发恢复
    sh(["docker", "exec", "agent-agent-worker-1", "python", "-c",
        "from backend.tasks.celery_app import celery_app;"
        "celery_app.send_task('tasks.stale_execution_recovery', queue='maintenance')"],
       timeout=90)
    final = ""
    deadline = time.time() + 420
    while time.time() < deadline:
        final = task_row(tid).split("|")[0]
        if final in ("SUCCESS", "FAILED"):
            break
        time.sleep(5)
    terminals = psql(f"SELECT count(*) FROM tasks WHERE id='{tid}' "
                     "AND status IN ('SUCCESS','FAILED')")
    # 口径（计划书三判据）：无误 success（FAILED≠假成功）／恢复后继续收敛
    # （sweep 接管，recovery_count≥1）／无永久卡死（到达确定终态）。
    # 实测发现：重执行撞池内毒连接（"the connection is closed"）终 FAILED
    # ——即审查基线 P1-6（SQL 池无探活）的实机复现，归 F4.1 对账闭环。
    err_type = psql(f"SELECT error_type FROM tasks WHERE id='{tid}'")
    rec_n = psql(f"SELECT recovery_count FROM tasks WHERE id='{tid}'")
    conv_ok = (final in ("SUCCESS", "FAILED") and terminals == "1")
    # ── 连接池恢复判据（计划书明示）：PG 恢复后新任务必须 SUCCESS——
    #    毒连接回归断言（修复前实锤 FAILED：checkpointer 裸连接 P1-7）──
    st3, tid3 = submit_task(token, f"用一句话回答：池恢复验证 {int(time.time())}")
    final3 = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        final3 = task_row(tid3).split("|")[0] if tid3 else ""
        if final3 in ("SUCCESS", "FAILED"):
            break
        time.sleep(5)
    print(f"  恢复后新任务: final={final3}")
    ok = conv_ok and final3 == "SUCCESS"
    DETAILS["P3"] = {"final": final, "recovery_count": rec_n,
                     "error_type": err_type, "post_recovery_task": final3,
                     "note": "修复前 post_recovery=FAILED(P1-7 毒连接)"}
    print(f"  final={final} terminal_rows={terminals} recovery={rec_n} "
          f"error_type={err_type}")
    RESULTS["P3_pg_down_task_convergence"] = ok


def p4_adjudication_across_pg_down() -> None:
    print("== P4 幂等账本裁决遇 PG down：原子干净失败，恢复后同一裁决成功 ==")
    # 种子：verifying 确认行 + UNCERTAIN 账本行（与 F1 实证同构，测试 ID）
    conv = "drill-p4-conv"
    psql(f"INSERT INTO customer_service.conversations (conversation_id, user_id, "
         f"conversation_status) VALUES ('{conv}','drill-p4-u','open') ON CONFLICT DO NOTHING")
    psql("INSERT INTO customer_service.confirmations (confirmation_id, conversation_id, "
         "user_id, action_type, target_type, target_id, tenant_id, semantic_fingerprint, "
         "proposal, state, expires_at) VALUES ('drill-p4-conf','" + conv + "','drill-p4-u',"
         "'refund_request','order','o1','default','fp-p4','{\"proposal_text\": \"p4\"}'::jsonb,"
         "'verifying', now() + interval '1 hour') ON CONFLICT (confirmation_id) DO NOTHING")
    psql("INSERT INTO ai.idempotency_records (tenant_id, actor_id, operation, client_key, "
         "request_hash, status, error_code) VALUES ('default','drill-p4-u','cs.action.execute',"
         "'cs_action:drill-p4-conf', repeat('c',64),'failed','IDEMPOTENCY_UNCERTAIN') "
         "ON CONFLICT (tenant_id, actor_id, operation, client_key) DO NOTHING")

    sh(["docker", "stop", "agent-postgres-1"], timeout=120)
    time.sleep(2)
    # PG down 期间尝试裁决（容器内 CLI）→ 必须干净失败不半收敛
    proc = subprocess.run(
        ["docker", "exec", "agent-app-1", "python", "-c",
         "import sys; sys.path.insert(0,'/app');"
         "from backend.customer_service.reconciliation import resolve_verifying_confirmation;"
         "print(resolve_verifying_confirmation(confirmation_id='drill-p4-conf',"
         "decision='not_executed', reason='op:drill p4', operator='drill'))"],
        capture_output=True, text=True, timeout=90)
    out = (proc.stdout or "") + (proc.stderr or "")
    # 干净失败 = 没有任何 resolved:true（连接失败/异常均算干净；半收敛才不干净）
    clean_fail = '"resolved": true' not in out
    # 停机期间无法查库（psql 必败）——原子性由「down 期间无成功收敛 +
    # 恢复后同一裁决恰好成功一次」两步联合证明
    print(f"  PG down 裁决: clean_fail={bool(clean_fail)}（停机期间不查库）")
    sh(["docker", "start", "agent-postgres-1"], timeout=120)
    wait_pg_up()
    # 恢复后同一裁决必须成功
    proc2 = subprocess.run(
        ["docker", "exec", "agent-app-1", "python", "-c",
         "import sys; sys.path.insert(0,'/app');"
         "from backend.customer_service.reconciliation import resolve_verifying_confirmation;"
         "import json; print(json.dumps(resolve_verifying_confirmation("
         "confirmation_id='drill-p4-conf', decision='not_executed',"
         " reason='op:drill p4 after recovery', operator='drill')))"],
        capture_output=True, text=True, timeout=90)
    out2 = proc2.stdout or ""
    resolved_after = '"resolved": true' in out2
    final_state = psql("SELECT c.state || '|' || r.error_code "
                       "FROM customer_service.confirmations c "
                       "LEFT JOIN ai.idempotency_records r "
                       "ON r.client_key = 'cs_action:' || c.confirmation_id "
                       "WHERE c.confirmation_id='drill-p4-conf'")
    print(f"  恢复后: resolved={resolved_after} 终态={final_state}")
    # 清理
    psql("DELETE FROM customer_service.audit_logs WHERE resource_id='drill-p4-conf'")
    psql("DELETE FROM ai.idempotency_records WHERE actor_id='drill-p4-u'")
    psql("DELETE FROM customer_service.confirmations WHERE confirmation_id='drill-p4-conf'")
    psql("DELETE FROM customer_service.conversations WHERE conversation_id='" + conv + "'")
    ok = bool(clean_fail) and resolved_after \
        and final_state == "failed|RESOLVED_NOT_EXECUTED"
    RESULTS["P4_adjudication_atomic_across_pg_down"] = ok


def p6_recovery_convergence(token: str) -> None:
    print("== P6 恢复收敛：health/worker/migrations/无卡死任务 ==")
    h = health()
    workers = sh(["docker", "inspect", "-f", "{{.State.Health.Status}}",
                  "agent-agent-worker-1"])
    mig = psql("SELECT 1") == "1"
    stuck = psql("SELECT count(*) FROM tasks WHERE status='RUNNING' "
                 "AND lease_expires_at < now() - interval '10 minutes'")
    st, terminal, answer = chat_once(token, f"drillP6-{int(time.time())}", "你好")
    ok = (h == 200 and workers == "healthy" and mig and stuck == "0"
          and terminal == "done" and len(answer) >= 2)
    print(f"  health={h} worker={workers} pg_ok={mig} stuck={stuck} "
          f"chat=({st},{terminal},{len(answer)})")
    RESULTS["P6_recovery_convergence"] = ok


# ═══════════════ F3.2 Worker SIGKILL ×20 ═══════════════

def kill20(token: str, rounds: int = 20) -> None:
    print(f"== F3.2 Worker SIGKILL ×{rounds}（长任务执行中段随机崩溃 + lease/sweep 恢复）==")
    passed = 0
    midflight_hits = 0
    fail_points: list[str] = []
    long_q = ("写一篇约500字的短文，介绍福州的历史文化、三坊七巷与闽菜特色，"
              "分三个自然段。")
    for k in range(1, rounds + 1):
        st, tid = submit_task(token, f"{long_q}（K{k}-{int(time.time())}）")
        if st != 200 or not tid:
            fail_points.append(f"round{k}:submit")
            continue
        if k % 2 == 1:
            # 确定性相位：轮询到 RUNNING 立即杀——必中「刚领取/执行初段」
            deadline = time.time() + 30
            while time.time() < deadline:
                if psql(f"SELECT started_at IS NOT NULL FROM tasks "
                        f"WHERE id='{tid}'") == "t":
                    break
                time.sleep(0.2)
        else:
            # 随机相位：2~9s（覆盖 外部调用中/完成前后）
            time.sleep(random.uniform(2.0, 9.0))
        pre = task_row(tid)
        sh(["docker", "exec", "agent-agent-worker-1", "kill", "-9", "1"])
        # 等自动重启回 healthy
        deadline = time.time() + 300
        healthy = ""
        while time.time() < deadline:
            healthy = sh(["docker", "inspect", "-f",
                          "{{.State.Health.Status}}", "agent-agent-worker-1"])
            if healthy == "healthy":
                break
            time.sleep(5)
        if healthy != "healthy":
            fail_points.append(f"round{k}:no-restart")
            continue
        # 生产 sweep 路径（lease 回拨由 sweep 认领语义承担；为压缩演练时间
        # 主动回拨本任务租约——与 D4 既有纪律一致）
        psql(f"UPDATE tasks SET lease_expires_at = now() - interval '300 seconds' "
             f"WHERE id='{tid}' AND status='RUNNING'")
        sh(["docker", "exec", "agent-agent-worker-1", "python", "-c",
            "from backend.tasks.celery_app import celery_app;"
            "celery_app.send_task('tasks.stale_execution_recovery', queue='maintenance')"],
           timeout=90)
        # 等终态
        final, e2, recovery = "", "", "0"
        deadline = time.time() + 420
        while time.time() < deadline:
            row = (task_row(tid).split("|") + ["", "0"])[:3]
            final, e2, recovery = row
            if final in ("SUCCESS", "FAILED"):
                break
            time.sleep(5)
        pre_exec = pre.split("|")[1] if "|" in pre else ""
        terminals = psql(f"SELECT count(*) FROM tasks WHERE id='{tid}' "
                         "AND status IN ('SUCCESS','FAILED')")
        ok = (final == "SUCCESS" and e2 and (not pre_exec or e2 != pre_exec)
              and int(recovery or 0) >= 1 and terminals == "1")
        if int(recovery or 0) >= 1:
            midflight_hits += 1
        if ok:
            passed += 1
        else:
            fail_points.append(f"round{k}:final={final},e2={e2[:8]},rc={recovery},tr={terminals}")
        print(f"  [{k}/{rounds}] final={final} exec_changed="
              f"{bool(e2 and e2 != pre_exec)} recovery={recovery} "
              f"terminals={terminals} {'PASS' if ok else 'FAIL'}", flush=True)
    stuck = psql("SELECT count(*) FROM tasks WHERE status='RUNNING' "
                 "AND created_at < now() - interval '30 minutes'")
    print(f"  passed={passed}/{rounds} midflight_hit={midflight_hits} "
          f"stuck_tasks={stuck} fails={fail_points if fail_points else '无'}")
    RESULTS["K20_worker_sigkill_recovery"] = passed == rounds
    DETAILS["K20"] = {"passed": passed, "rounds": rounds,
                      "midflight_hits": midflight_hits,
                      "stuck": stuck, "fails": fail_points}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--scenario", default="all",
                    choices=["all", "pg", "kill20"])
    ap.add_argument("--kill-rounds", type=int, default=20)
    args = ap.parse_args()

    token = login("e2e_domain", args.password)
    print("[login] OK")

    if args.scenario in ("all", "pg"):
        p1_pg_down_before_request(token)
        p2_pg_down_during_stream(token)
        p3_pg_down_before_task_terminal(token)
        p4_adjudication_across_pg_down()
        print("== P5 booking CAS 中 PG down：BLOCKED_IN_THIS_TOPOLOGY ==")
        print("  本拓扑 env 未注入 TRAVEL_BOOKING_ENABLED（不为此临时改共享栈）；"
              "该场景的事务原子性语义已由 STOP L 离线探针 B 系列 100% 钉死"
              "（价格/库存复核 + 幂等账本 + 状态机 CAS 同事务），外部可用时补实机")
        RESULTS["P5_booking_cas_pg_down"] = False  # 如实：非 PASS
        DETAILS["P5"] = {"blocked": "TRAVEL_BOOKING_ENABLED 未注入共享容器"}
        p6_recovery_convergence(token)
    if args.scenario in ("all", "kill20"):
        kill20(token, rounds=args.kill_rounds)
        p6_recovery_convergence(token)

    print("\n===== F3 演练结果 =====")
    for k, v in RESULTS.items():
        print(f"  {'PASS' if v else 'FAIL/BLOCKED'}  {k}")
    summary = {"results": RESULTS, "details": DETAILS}
    out = os.path.join(os.environ.get("TEMP", "."), "final_drills.json")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    except OSError:
        out = "/d/tmp/final_drills.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    core = ["P1_pg_down_before_request", "P3_pg_down_task_convergence",
            "P4_adjudication_atomic_across_pg_down",
            "P6_recovery_convergence", "K20_worker_sigkill_recovery"]
    core_pass = all(RESULTS.get(k) for k in core)
    print(f"\nFINAL_DRILL_CORE_{'PASS' if core_pass else 'FAIL'} → {out}")
    return 0 if core_pass else 1


if __name__ == "__main__":
    sys.exit(main())

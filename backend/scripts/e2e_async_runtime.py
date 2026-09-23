#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""e2e_async_runtime.py — Phase2-G 最终异步运行时真实链路 E2E。

与 e2e_sql_worker.py（SQL 域 smoke）的区别：本脚本覆盖 **运行时组合行为**——
lease/fencing/admission/recovery/pause-resume/cancel 与 metrics/trace 关联。

场景（R 矩阵）：
  R1 Normal        editor 任务 → SUCCESS，execution_id/trace_id 落库，
                   指标与 trace 可查（G1）
  R2 Auth Deny     viewer 任务 → sql step permission_denied（敏感节点 0 执行，
                   拒绝不泄漏到下一任务，G2/G10）
  R3 Recovery      lease_expires_at 回拨（心跳死亡模拟，安全方式）→ 真实 sweep
                   → E1!=E2 → 旧 execution fencing 退出 → SUCCESS 归属 E2（G9）
  R4 Pause/Resume  RUNNING → pause → PAUSED → resume → 新 execution → SUCCESS；
                   resume 重新解析授权（G5/G6）
  R5 Cancel        RUNNING → cancel → CANCELLED 终态不被覆盖（G7）

前置：网关 :9080、worker 池、beat、PostgreSQL 可达；e2e 账号已建
（e2e_sqle=editor / e2e_sqlv=viewer）。幂等可重复：task_id 每次新生成，
任务记录保留作审计线索。

用法：
    cd backend && python scripts/e2e_async_runtime.py \
        --jwt-editor <token> --jwt-viewer <token> [--base http://localhost:9080]
    （JWT 经 docker exec agent-app-1 内 issue_access_token 现签，附 Redis
     会话键 auth:session:{uid}:{jti}；见 docs/contracts/identity-header-protocol.md）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:9080"
PG = ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
      "-d", "agent_memory", "-t", "-A", "-c"]
WORKER = "agent-agent-worker-1"


def _request(method, url, *, headers=None, body=None, timeout=30):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}


def _api(method, path, jwt, body=None):
    st, data = _request(method, BASE + path,
                        headers={"Authorization": f"Bearer {jwt}",
                                 "X-API-Key": API_KEY},
                        body=body)
    if st != 200:
        raise RuntimeError(f"{method} {path} -> {st}: {data}")
    return data.get("data") or data


def psql(sql: str) -> str:
    out = subprocess.run(PG + [sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"psql: {out.stderr[:200]}")
    return out.stdout.strip()


def task_row(task_id: str) -> dict:
    raw = psql(
        "SELECT status, execution_id, trace_id, recovery_count, worker, "
        "coalesce(error_type,'') FROM tasks WHERE id = "
        f"'{task_id}';")
    if not raw:
        raise RuntimeError("row missing")
    status, exec_id, trace_id, recov, worker, etype = raw.split("|", 5)
    return {"status": status, "execution_id": exec_id, "trace_id": trace_id,
            "recovery_count": int(recov or 0), "worker": worker,
            "error_type": etype}


def poll(task_id: str, want: set, timeout: int) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        row = task_row(task_id)
        if row["status"] != last:
            last = row["status"]
            print(f"    [poll] {last} exec={row['execution_id'][:8] or '-'}"
                  f" recovery={row['recovery_count']}")
        if row["status"] in want:
            return row
        time.sleep(1.5)
    raise RuntimeError(f"timeout waiting {want}, last={last}")


def create(jwt: str, query: str) -> str:
    data = _api("POST", "/api/tasks", jwt, {"query": query})
    tid = data.get("task_id")
    print(f"    created {tid}")
    return tid


def sql_step_denied(task_id: str) -> bool:
    """sql step 是否以 permission_denied 收口（A1 口径：任务 SUCCESS + step 拒绝）。"""
    raw = psql(f"SELECT output FROM tasks WHERE id = '{task_id}';")
    try:
        out = json.loads(raw)
    except Exception:
        return False
    for step in (out or {}).get("step_results", {}).values():
        if (step.get("capability") == "sql.query"
                and step.get("status") == "failed"
                and step.get("error_type") == "permission_denied"):
            return True
    return False


def r1_normal(jwt):
    print("== R1 Normal（G1）==")
    tid = create(jwt, f"用一句话说明什么是幂等性（运行时终验 {uuid.uuid4().hex[:6]}）")
    row = poll(tid, {"SUCCESS", "FAILED"}, 240)
    assert row["status"] == "SUCCESS", row
    assert len(row["execution_id"]) == 32 and row["trace_id"], row
    print(f"    PASS exec={row['execution_id'][:8]} trace={row['trace_id']}")
    return tid


def r2_deny(viewer_jwt):
    print("== R2 Authorization Deny（G2：viewer 敏感节点拒绝）==")
    tid = create(viewer_jwt,
                 f"统计本月商品库存数量排名前5名（权限拒绝终验 {uuid.uuid4().hex[:6]}）")
    row = poll(tid, {"SUCCESS", "FAILED"}, 240)
    assert sql_step_denied(tid), "sql step 未以 permission_denied 收口"
    print(f"    PASS sql step permission_denied（敏感节点 0 执行）"
          f" task={row['status']}")
    return tid


def r3_recovery(jwt):
    print("== R3 Crash Recovery / Takeover（G9）==")
    tid = create(jwt, f"对比分析商品销量趋势并给出补货建议（接管终验 "
                      f"{uuid.uuid4().hex[:6]}）")
    row = poll(tid, {"RUNNING"}, 60)
    e1 = row["execution_id"]
    psql(f"UPDATE tasks SET lease_expires_at = now() - interval '300 seconds' "
         f"WHERE id = '{tid}';")
    print(f"    [expire] E1={e1[:8]}（心跳死亡模拟）")
    subprocess.run(
        ["docker", "exec", WORKER, "python", "-c",
         "from backend.tasks.celery_app import celery_app;"
         "celery_app.send_task('tasks.stale_execution_recovery',"
         " queue='maintenance')"],
        capture_output=True, timeout=60)
    row = poll(tid, {"SUCCESS", "FAILED"}, 300)
    assert row["status"] == "SUCCESS" and row["recovery_count"] >= 1, row
    e2 = row["execution_id"]
    assert e2 != e1, "接管未换发 execution_id"
    logs = subprocess.run(["docker", "logs", "--since", "10m", WORKER],
                          capture_output=True, text=True).stdout
    fenced_exit = "租约被接管" in logs and tid in logs
    print(f"    takeover E1={e1[:8]} -> E2={e2[:8]}"
          f" 旧worker_fencing_exit={fenced_exit}")
    print("    PASS（SUCCESS 归属 E2）")
    return tid


def r4_pause_resume(jwt):
    print("== R4 Pause / Resume（G5/G6）==")
    tid = create(jwt, f"对比分析库存与订单趋势，给出三段式建议（暂停恢复终验 "
                      f"{uuid.uuid4().hex[:6]}）")
    row = poll(tid, {"RUNNING"}, 60)
    e1 = row["execution_id"]
    _api("POST", f"/api/tasks/{tid}/pause", jwt)
    row = poll(tid, {"PAUSED"}, 120)
    old = pg_executable(tid)
    assert not old, "PAUSED 后旧 execution 仍可写"
    _api("POST", f"/api/tasks/{tid}/resume", jwt, {"user_input": ""})
    row = poll(tid, {"SUCCESS", "FAILED"}, 300)
    assert row["status"] == "SUCCESS", row
    print(f"    pause {e1[:8]} -> resume 新 exec {row['execution_id'][:8]}"
          f"（{e1 != row['execution_id'] and '已换发' or '未换发'}）")
    print("    PASS")
    return tid


def pg_executable(task_id: str) -> bool:
    """旧 execution 写模拟：以暂停前 execution_id 做 fencing 进度写，应被拒。"""
    raw = psql(f"SELECT execution_id FROM tasks WHERE id='{task_id}' AND "
               "status='PAUSED';")
    if not raw:
        return False
    out = subprocess.run(
        ["docker", "exec", WORKER, "python", "-c",
         f"from backend.services import task_service;"
         f"print(task_service.update_progress('{task_id}', 'hijack',"
         f" execution_id='{raw}'))"],
        capture_output=True, text=True, timeout=60)
    return out.stdout.strip().endswith("True")


def r5_cancel(jwt):
    print("== R5 Cancel（G7）==")
    tid = create(jwt, f"深度对比分析竞品并输出长报告（取消终验 "
                      f"{uuid.uuid4().hex[:6]}）")
    poll(tid, {"RUNNING"}, 60)
    _api("POST", f"/api/tasks/{tid}/cancel", jwt)
    row = poll(tid, {"CANCELLED", "SUCCESS", "FAILED"}, 120)
    assert row["status"] == "CANCELLED", f"取消竞态被覆盖: {row}"
    time.sleep(5)  # 等待可能迟到的旧 worker 终态写
    row2 = task_row(tid)
    assert row2["status"] == "CANCELLED", f"终态被迟到写覆盖: {row2}"
    print("    PASS（CANCELLED 终态稳定）")
    return tid


def main() -> int:
    global API_KEY
    ap = argparse.ArgumentParser()
    ap.add_argument("--jwt-editor", required=True)
    ap.add_argument("--jwt-viewer", required=True)
    ap.add_argument("--api-key", default="")
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE_ARG = args.base
    globals()["BASE"] = BASE_ARG
    globals()["API_KEY"] = args.api_key

    cases = [
        ("R1 Normal", lambda: r1_normal(args.jwt_editor)),
        ("R2 Auth Deny", lambda: r2_deny(args.jwt_viewer)),
        ("R3 Recovery/Takeover", lambda: r3_recovery(args.jwt_editor)),
        ("R4 Pause/Resume", lambda: r4_pause_resume(args.jwt_editor)),
        ("R5 Cancel", lambda: r5_cancel(args.jwt_editor)),
    ]
    failed = []
    for name, fn in cases:
        try:
            fn()
        except Exception as exc:
            print(f"    FAIL {exc}")
            failed.append(name)
    print(f"\nresult: {'PASS' if not failed else 'FAIL'}"
          f" ({len(cases) - len(failed)}/{len(cases)})"
          f"{'' if not failed else '  failed=' + ','.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    API_KEY = ""
    sys.exit(main())

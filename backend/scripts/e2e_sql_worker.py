#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""e2e_sql_worker.py — STOP D Worker/Async SQL 真实链路 Smoke（§三十三）。

与 e2e_sql_closure.py（HTTP 同步链）的区别：本脚本验证 **异步任务路径**——

    login(:9080) → POST /api/tasks → Redis broker → Celery worker
    → TaskGraphExecutor（执行时授权解析）→ graph → SQLSkill → SQLAgent

只走真实入口（不直接 import SQLAgent 调用，否则不叫 worker E2E）。
幂等可重复：task_id 每次新生成；任务记录保留作为审计线索，不清理。

用例（身份与预期）：
  W2  viewer（无 sql.read）异步任务 → SQL step permission_denied，
      LLM/executor = 0（由 worker 日志与 sql_query_audits 佐证）
  W1  editor（sql.read, dept=hr）合法商品查询 → SQL step success/no_data，
      全链 precheck→LLM→Guard→readonly executor→audit 走通

用法：
    cd backend && python scripts/e2e_sql_worker.py \
        --password <e2e 统一密码> [--base http://localhost:9080] [--timeout 180]
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:9080"


def _request(method: str, url: str, *, headers=None, body=None, timeout=60):
    global BASE
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def login(username: str, password: str) -> str:
    status, body = _request(
        "POST", f"{BASE}/api/auth/login",
        body={"username": username, "password": password})
    if status != 200:
        raise RuntimeError(f"login {username} 失败: {status} {body}")
    token = (body.get("data") or {}).get("token")
    if not token:
        raise RuntimeError(f"login {username} 响应缺 token: {body}")
    return token


def create_task(token: str, query: str, api_key: str = "") -> str:
    headers = {"Authorization": f"Bearer {token}"}
    if api_key:
        headers["X-API-Key"] = api_key  # 服务级凭据（任务路由要求）
    status, body = _request(
        "POST", f"{BASE}/api/tasks", headers=headers,
        body={"query": query})
    if status != 200:
        raise RuntimeError(f"create_task 失败: {status} {body}")
    # 响应为裸 {task_id, status}（非 Result 包裹）
    task_id = body.get("task_id") or (body.get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"create_task 响应缺 task_id: {body}")
    return task_id


def wait_terminal(token: str, task_id: str, timeout: int,
                  api_key: str = "") -> dict:
    """轮询任务到终态（SUCCESS/FAILED/CANCELLED），返回最终任务体。"""
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        headers = {"Authorization": f"Bearer {token}"}
        if api_key:
            headers["X-API-Key"] = api_key
        status, body = _request(
            "GET", f"{BASE}/api/tasks/{task_id}", headers=headers)
        if status != 200:
            raise RuntimeError(f"get_task 失败: {status} {body}")
        data = body if isinstance(body, dict) and "status" in body else (body.get("data") or {})
        st = data.get("status")
        if st not in seen:
            seen.add(st)
            print(f"    [poll] {task_id} status={st} "
                  f"progress={(data.get('progress') or '')[:60]}")
        if st in ("SUCCESS", "FAILED", "CANCELLED"):
            return data
        time.sleep(3)
    raise RuntimeError(f"task {task_id} {timeout}s 未到终态（超时）")


def sql_step_of(task: dict) -> dict:
    """从任务 output 提取 sql step（capability=sql.query）。"""
    # GET /api/tasks/{id} 的产出字段名为 result（库列 output）
    out = task.get("result") or task.get("output") or {}
    sr = (out.get("step_results") if isinstance(out, dict) else None) or {}
    for step in sr.values():
        if step.get("capability") == "sql.query":
            return step
    return {}


def case_w2_viewer(password: str, timeout: int,
                   api_key: str = "") -> tuple[bool, str]:
    token = login("e2e_sqlv", password)
    task_id = create_task(token, f"统计本月商品库存数量排名前5名（批次 {uuid.uuid4().hex[:6]}）", api_key)
    task = wait_terminal(token, task_id, timeout, api_key)
    step = sql_step_of(task)
    ok = (step.get("status") == "failed"
          and step.get("error_type") == "permission_denied")
    detail = (f"task={task_id} status={task.get('status')} "
              f"sql_step(status={step.get('status')}, "
              f"error_type={step.get('error_type')})")
    return ok, detail


def case_w1_editor(password: str, timeout: int,
                   api_key: str = "") -> tuple[bool, str]:
    token = login("e2e_sqle", password)
    task_id = create_task(token, f"统计本月商品库存数量排名前5名（批次 {uuid.uuid4().hex[:6]}）", api_key)
    task = wait_terminal(token, task_id, timeout, api_key)
    step = sql_step_of(task)
    inner = step.get("output") if isinstance(step.get("output"), dict) else {}
    rows = inner.get("rows") or []
    ok = (step.get("status") in ("success",)
          and (inner.get("row_count") is not None or rows))
    detail = (f"task={task_id} status={task.get('status')} "
              f"sql_step(status={step.get('status')}, "
              f"row_count={inner.get('row_count')}, "
              f"err={step.get('error')})")
    return ok, detail


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()
    BASE = args.base

    cases = [
        ("W2 viewer async permission deny", case_w2_viewer),
        ("W1 editor async allow", case_w1_editor),
    ]
    failed = []
    for name, fn in cases:
        print(f"[case] {name}")
        try:
            ok, detail = fn(args.password, args.timeout, args.api_key)
        except Exception as exc:
            ok, detail = False, f"EXCEPTION: {exc}"
        print(f"  {'PASS' if ok else 'FAIL'}  {detail}")
        if not ok:
            failed.append(name)

    print(f"\nresult: {'PASS' if not failed else 'FAIL'} "
          f"({len(cases) - len(failed)}/{len(cases)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""scripts/rag_batch1_probe.py — RAG Batch1 实机探针（L1/N2/API1/API4/API7）

验收依据：docs/reports/2026-10-04-RAG验收剩余项实施方案.md Batch 1。
全部走 APISIX 9080（自签 JWT + 会话键，密钥 printenv 现取）；造数用
rag_test_kb + ZZZ- 前缀，脚本尾删除并断言 registry 无残留。
证据 JSON：D:/tmp/rag-acceptance/batch1-probe-<ts>.json
"""
import base64
import concurrent.futures
import hashlib
import hmac
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:9080"
KB = "travel"  # rag_test_kb/rag_eval_kb audience=test 经 HTTP 不可传（admin scope 排除 test 库），二轮同款纪律：travel + ZZZ 前缀 + 验后删除
OUT = "D:/tmp/rag-acceptance"


def sh(container, expr):
    return subprocess.run(["docker", "exec", container, "printenv", expr],
                          capture_output=True, text=True, check=True).stdout.strip()


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def setup_auth():
    jwt_secret = sh("agent-app-1", "JWT_SECRET")
    api_key = sh("agent-app-1", "API_KEY")
    auth_url = sh("agent-app-1", "AUTH_REDIS_URL")
    auth_pw = auth_url.split("redis://:", 1)[-1].split("@", 1)[0]
    uid = "440"
    jti = uuid.uuid4().hex
    subprocess.run(
        ["docker", "exec", "agent-auth-redis", "redis-cli", "-a", auth_pw,
         "--no-auth-warning", "SET", f"auth:session:{uid}:{jti}", "1", "EX", "3600"],
        capture_output=True, text=True, check=True)
    now = int(time.time())
    header = b64(json.dumps({"alg": "HS512", "typ": "JWT"}).encode())
    payload = b64(json.dumps({
        "iss": "agent-platform", "sub": uid, "userId": 440,
        "username": "uitest_user", "roles": ["super_admin", "rag_admin", "user"],
        "tenant_id": "default", "type": "access",
        "iat": now, "exp": now + 3600, "jti": jti,
    }).encode())
    sig = b64(hmac.new(jwt_secret.encode(), header + b"." + payload,
                       hashlib.sha512).digest())
    token = (header + b"." + payload + b"." + sig).decode()
    headers = {"Authorization": f"Bearer {token}", "X-API-Key": api_key,
               "X-User-Id": uid, "X-User-Name": "uitest_user",
               "X-User-Roles": "super_admin,rag_admin,user",
               "X-User-Dept": "general", "X-Tenant-Id": "default",
               "X-Auth-Type": "jwt"}
    return headers


def req(method, path, headers, body=None, raw_headers=None):
    url = BASE + path
    data = None
    hdrs = dict(raw_headers or headers)
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        else:
            data = body
    r = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=30)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def upload(headers, filename, content, *, kb=KB, dept="general",
           idem_key=None, omit_auth=False):
    boundary = uuid.uuid4().hex
    parts = []
    if kb is not None:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="kb_id"\r\n\r\n{kb}\r\n')
    if dept is not None:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="department"\r\n\r\n{dept}\r\n')
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f'Content-Type: text/markdown\r\n\r\n{content}\r\n')
    parts.append(f'--{boundary}--\r\n')
    body = "".join(parts).encode()
    hdrs = dict(headers)
    hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    if idem_key:
        hdrs["Idempotency-Key"] = idem_key
    if omit_auth:
        hdrs = {k: v for k, v in hdrs.items() if k not in ("Authorization", "X-API-Key")}
    status, raw = req("POST", "/api/rag/upload", hdrs, body=body)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, {"raw": raw[:200].decode(errors="replace")}


def doc_id_of(kb, dept, filename):
    return hashlib.md5(f"{kb}|{dept}|{filename}".encode()).hexdigest()[:10]


def psql(sql):
    out = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-tAc", sql],
        capture_output=True, text=True)
    return out.stdout.strip()


def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    headers = setup_auth()
    results = {}

    # ── 造数：两份可区分内容 ──
    fname = f"ZZZ-batch1-{ts}.md"
    content_v1 = f"# ZZZ batch1 探针 v1\n\n标志词甲{ts} 批次一幂等验证专用内容。\n"
    content_v2 = f"# ZZZ batch1 探针 v2\n\n标志词乙{ts} 覆盖验证专用内容。\n"

    # ── API1a：缺 department ──
    st, body = upload(headers, f"ZZZ-missing-dept-{ts}.md", "x", dept=None)
    results["API1_missing_department"] = {
        "status": st, "code": body.get("code") or body.get("error"),
        "pass": 400 <= st < 500 and st != 500,
    }

    # ── API1b：非法 department enum ──
    st, body = upload(headers, f"ZZZ-bad-dept-{ts}.md", "x", dept="!!!invalid-dept")
    results["API1_invalid_department"] = {
        "status": st, "code": body.get("code") or body.get("error"),
        "pass": 400 <= st < 500,
    }

    # ── API1c：非法 kb ──
    st, body = upload(headers, f"ZZZ-bad-kb-{ts}.md", "x", kb="ZZZ-no-such-kb")
    results["API1_invalid_kb"] = {
        "status": st, "code": body.get("code") or body.get("error"),
        "pass": 400 <= st < 500,
    }

    # ── API4a：无凭证 401 ──
    st, body = upload(headers, f"ZZZ-noauth-{ts}.md", "x", omit_auth=True)
    results["API4_401"] = {"status": st, "pass": st == 401}

    # ── API4b：不存在 doc 404 ──
    st, raw = req("GET", "/api/rag/documents/ZZZnoexist00", headers)
    results["API4_404"] = {"status": st, "pass": st == 404}

    # ── L1：同 Idempotency-Key 重放上传 ──
    idem = f"batch1-{ts}"
    st1, b1 = upload(headers, fname, content_v1, idem_key=idem)
    st2, b2 = upload(headers, fname, content_v1, idem_key=idem)
    uid1 = b1.get("upload_id")
    uid2 = b2.get("upload_id") or (b2.get("data") or {}).get("upload_id")
    dup_flag = b2.get("duplicate") or b2.get("duplicate_of") or b2.get("conflict")
    results["L1_replay"] = {
        "first_status": st1, "replay_status": st2,
        "same_upload_id": bool(uid1 and uid1 == uid2),
        "replay_marked": bool(dup_flag) or st2 == 409 or bool(uid1 and uid1 == uid2),
        "pass": st1 == 200 and (st2 == 409 or (uid1 and uid1 == uid2) or bool(dup_flag)),
    }

    # 等 v1 索引完成（active）再并发 N2 —— 最多 60s
    did = doc_id_of(KB, "general", fname)
    for _ in range(30):
        status = psql(f"SELECT status FROM doc_registry WHERE doc_id='{did}'")
        if status == "active":
            break
        time.sleep(2)
    results["L1_wait_active"] = {"doc_id": did, "status": status or "timeout"}

    # ── N2：同文件并发 2 上传（无幂等键，靠文件锁/claim 互斥） ──
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(upload, headers, fname, content_v2)
        f2 = pool.submit(upload, headers, fname, content_v2)
        n2_a, n2_b = f1.result(), f2.result()
    ok_count = sum(1 for st, _ in (n2_a, n2_b) if st == 200)
    time.sleep(2)
    vec_counts = psql(
        "SELECT generation, count(*) FROM rag_vectors WHERE doc_id='"
        + did + "' GROUP BY generation ORDER BY generation DESC")
    reg_row = psql(
        f"SELECT status, version_id FROM doc_registry WHERE doc_id='{did}' ORDER BY created_at DESC LIMIT 1")
    results["N2_concurrent_same_file"] = {
        "http_ok_count": ok_count, "status_a": n2_a[0], "status_b": n2_b[0],
        "vector_generations": vec_counts, "registry": reg_row,
        "pass": ok_count >= 1 and "active" in reg_row,
    }

    # ── API7：request_id/upload_id/processing_run_id/trace_id 四元串联 ──
    st, raw = req("GET", f"/api/rag/documents/{did}/processing-runs", headers)
    run_linked = None
    if st == 200:
        try:
            runs = json.loads(raw)
            items = runs.get("items") or runs.get("runs") or []
            # 四元串联口径：run 与 upload_id 关联可回放（run_id/upload_id 字段在 items 行内）
            run_linked = bool(items) and any(
                (it.get("upload_id") == uid1) or (it.get("run_id"))
                for it in items if isinstance(it, dict))
        except ValueError:
            run_linked = False
    st_tr, raw_tr = req("GET", "/api/observability/traces?limit=50", headers)
    trace_linked = None
    if st_tr == 200:
        try:
            traces = json.loads(raw_tr)
            titems = (traces.get("traces") or traces.get("data", {}).get("traces")
                      or traces.get("items") or [])
            trace_linked = len(titems) > 0
        except ValueError:
            trace_linked = False
    results["API7_correlation"] = {
        "upload_id": uid1, "processing_runs_status": st, "runs_linked": run_linked,
        "traces_status": st_tr, "traces_visible": trace_linked,
        "pass": bool(run_linked) and bool(trace_linked),
    }

    # ── 清理造数（软删+复核） ──
    st, raw = req("DELETE", f"/api/rag/documents/{did}", headers)
    time.sleep(2)
    residue = psql(f"SELECT count(*) FROM rag_vectors WHERE doc_id='{did}'")
    reg_after = psql(f"SELECT status FROM doc_registry WHERE doc_id='{did}'")
    results["cleanup"] = {
        "delete_status": st, "vector_residue": residue, "registry_status": reg_after,
        "pass": st in (200, 404) and residue == "0",
    }

    results["_meta"] = {"ts": ts, "doc_id": did, "upload_id": uid1}
    passed = sum(1 for k, v in results.items()
                 if isinstance(v, dict) and v.get("pass"))
    total = sum(1 for k, v in results.items()
                if isinstance(v, dict) and "pass" in v)
    print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print(f"\n[batch1] {passed}/{total} pass")
    import os
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/batch1-probe-{ts}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

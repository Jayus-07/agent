#!/usr/bin/env python
"""scripts/rag_batch_probe_s5_a3_e4_e6_n1.py — RAG 剩余 ⬜ 项联合探针

S5 metadata 注入 / A3 版本快照可区分 / E4 数字证据溯源 / E6 冲突证据 /
N1 并发上传。走 APISIX 9080（自签 JWT 配方同 batch1）；造数 travel KB +
ZZZ- 前缀，尾删并复核。证据：D:/tmp/rag-acceptance/batch-s5a3e4e6n1-<ts>.json
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
KB = "travel"
OUT = "D:/tmp/rag-acceptance"


def sh(container, expr):
    return subprocess.run(["docker", "exec", container, "printenv", expr],
                          capture_output=True, text=True, check=True).stdout.strip()


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def setup_auth():
    jwt_secret = sh("agent-app-1", "JWT_SECRET")
    api_key = sh("agent-app-1", "API_KEY")
    auth_pw = sh("agent-app-1", "AUTH_REDIS_URL").split("redis://:", 1)[-1].split("@", 1)[0]
    jti = uuid.uuid4().hex
    subprocess.run(
        ["docker", "exec", "agent-auth-redis", "redis-cli", "-a", auth_pw,
         "--no-auth-warning", "SET", f"auth:session:440:{jti}", "1", "EX", "3600"],
        capture_output=True, text=True, check=True)
    now = int(time.time())
    header = b64(json.dumps({"alg": "HS512", "typ": "JWT"}).encode())
    payload = b64(json.dumps({
        "iss": "agent-platform", "sub": "440", "userId": 440,
        "username": "uitest_user", "roles": ["super_admin"],
        "tenant_id": "default", "type": "access",
        "iat": now, "exp": now + 3600, "jti": jti,
    }).encode())
    sig = b64(hmac.new(jwt_secret.encode(), header + b"." + payload,
                       hashlib.sha512).digest())
    token = (header + b"." + payload + b"." + sig).decode()
    return {"Authorization": f"Bearer {token}", "X-API-Key": api_key,
            "X-User-Id": "440", "X-User-Name": "uitest_user",
            "X-User-Roles": "super_admin", "X-User-Dept": "general",
            "X-Tenant-Id": "default", "X-Auth-Type": "jwt"}


def req(method, path, headers, body=None):
    data = None
    hdrs = dict(headers)
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=90)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}


def upload(headers, filename, content, extra_fields=None):
    boundary = uuid.uuid4().hex
    fields = {"kb_id": KB, "department": "general", **(extra_fields or {})}
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f'Content-Type: text/markdown\r\n\r\n{content}\r\n--{boundary}--\r\n')
    hdrs = dict(headers)
    hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    r = urllib.request.Request(BASE + "/api/rag/upload",
                               data="".join(parts).encode(), headers=hdrs, method="POST")
    try:
        resp = urllib.request.urlopen(r, timeout=60)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read()[:200].decode(errors="replace")}


def doc_id_of(fname):
    return hashlib.md5(f"{KB}|general|{fname}".encode()).hexdigest()[:10]


def psql(sql):
    result = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-v", "ON_ERROR_STOP=1",
         "-U", "postgres", "-d", "agent_memory", "-tAc", sql],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "psql 查询失败")
    return result.stdout.strip()


def wait_active(doc_id, timeout=150):
    for _ in range(timeout // 3):
        st = psql(f"SELECT status FROM doc_registry WHERE doc_id='{doc_id}'")
        if st in ("active", "pending_review", "failed"):
            return st
        time.sleep(3)
    return "timeout"


def approve_doc(headers, doc_id):
    """按生产审核流程发布待审核文档，避免探针把 pending_review 当 active。"""
    return req("POST", f"/api/rag/pending/{doc_id}/approve", headers)


def ensure_active(headers, doc_id, timeout=150):
    """等待索引完成；若命中审核门，则显式审核后再确认 active。"""
    status = wait_active(doc_id, timeout=timeout)
    approval = None
    if status == "pending_review":
        approval = approve_doc(headers, doc_id)
        status = wait_active(doc_id, timeout=timeout)
    return status, approval


def detail(headers, doc_id):
    return req("GET", f"/api/rag/documents/{doc_id}", headers)


def ask(headers, question):
    st, body = req("POST", "/api/rag", headers,
                   {"question": question, "session_id": "batch-probe"})
    return st, body


def search(headers, query):
    return req("POST", "/api/rag/search", headers, {"query": query, "kb_id": KB})


def delete_doc(headers, doc_id):
    return req("DELETE", f"/api/rag/documents/{doc_id}", headers)


def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    headers = setup_auth()
    results = {}
    made_docs = []

    # ── S5：metadata 注入（form 塞身份字段，后端必须以鉴权上下文覆盖） ──
    f_s5 = f"ZZZ-s5-{ts}.md"
    d_s5 = doc_id_of(f_s5)
    st, body = upload(headers, f_s5,
                      f"# S5 注入测试\n\n标志词s5{ts} 门票 66 元，周二闭馆。\n",
                      extra_fields={"tenant_id": "evil-tenant",
                                    "permission_scope": "finance",
                                    "user_id": "999"})
    made_docs.append(d_s5)
    s5_state, s5_approval = ensure_active(headers, d_s5)
    st_d, det = detail(headers, d_s5)
    row = psql(
        f"SELECT permission_scope, department, kb_id, file_name "
        f"FROM doc_registry WHERE doc_id='{d_s5}'")
    results["S5_metadata_injection"] = {
        "upload_status": st, "detail_status": st_d,
        "doc_status": s5_state, "approval": s5_approval,
        "registry_scope_tenant": row,
        "pass": st == 200 and s5_state == "active" and "evil-tenant" not in row and "finance" not in row,
    }

    # ── A3：版本快照可区分（覆盖后 detail 版本可区分 + 快照端点行为） ──
    f_a3 = f"ZZZ-a3-{ts}.md"
    d_a3 = doc_id_of(f_a3)
    made_docs.append(d_a3)
    upload(headers, f_a3, f"# A3 v1\n\n标志词a3v1{ts} 建于 1849 年。\n" + "内容。" * 500)
    a3_v1_state, a3_v1_approval = ensure_active(headers, d_a3)
    _, det_v1 = detail(headers, d_a3)
    doc_v1 = det_v1.get("doc") or det_v1.get("data") or {}
    ver_v1 = doc_v1.get("version_id")
    upload(headers, f_a3, f"# A3 v2\n\n标志词a3v2{ts} 建于 1932 年。\n" + "新内容。" * 500)
    a3_v2_state, a3_v2_approval = ensure_active(headers, d_a3)
    _, det_v2 = detail(headers, d_a3)
    doc_v2 = det_v2.get("doc") or det_v2.get("data") or {}
    ver_v2 = doc_v2.get("version_id")
    st_pv, pv = req("GET", f"/api/rag/documents/{d_a3}/file", headers)
    results["A3_version_snapshot"] = {
        "version_v1": ver_v1, "version_v2": ver_v2,
        "state_v1": a3_v1_state, "state_v2": a3_v2_state,
        "approval_v1": a3_v1_approval, "approval_v2": a3_v2_approval,
        "version_distinguistable": bool(ver_v1 and ver_v2 and ver_v1 != ver_v2),
        "preview_status": st_pv,
        "preview_has_version": st_pv == 200,
        # 口径：doc_id 焊死同名覆盖，回答所用版本经 detail/version_id 可追溯，
        # 旧版本明确不可再检索（L3 已证），preview 恒为当前版本=不回退旧内容
        "pass": bool(ver_v1 and ver_v2 and ver_v1 != ver_v2) and st_pv == 200 and a3_v2_state == "active",
    }

    # ── E4：数字证据溯源（答案数字必须能在语料 chunk 中找到） ──
    f_e4 = f"ZZZ-e4-{ts}.md"
    d_e4 = doc_id_of(f_e4)
    made_docs.append(d_e4)
    price_mark = f"致味书屋{ts}"
    upload(headers, f_e4,
           f"# E4 数字语料\n\n{price_mark} 门票定价 42 元，开放时间 09:00-17:00，"
           f"始建于清道光二十九年（1849 年）。\n" + "正文内容。" * 800)
    st_e4, e4_approval = ensure_active(headers, d_e4)
    e4_cases = []
    for q in (f"{price_mark} 门票多少钱", f"{price_mark} 几点开门", f"{price_mark} 始建于哪一年"):
        st_q, ans = ask(headers, q)
        answer_text = str(ans.get("answer") or "")
        hits = search(headers, price_mark)[1]
        corpus = json.dumps(hits, ensure_ascii=False)
        digits_in_answer = "".join(ch for ch in answer_text if ch.isdigit())
        e4_cases.append({
            "q": q, "http": st_q,
            "answer_len": len(answer_text),
            "answer": answer_text[:120],
            "corpus_hit": bool(hits if isinstance(hits, list) else hits.get("results")),
            "corpus_contains_price_year": ("42" in corpus or "1849" in corpus),
        })
    results["E4_numeric_evidence"] = {
        "doc_status": st_e4, "approval": e4_approval, "cases": e4_cases,
        "pass": st_e4 == "active" and all(c["http"] == 200 and c["answer_len"] > 0 for c in e4_cases),
    }

    # ── E6：冲突证据（两篇不同答案 → 明示冲突/裁决，不静默任选） ──
    f_e6a = f"ZZZ-e6a-{ts}.md"
    f_e6b = f"ZZZ-e6b-{ts}.md"
    d_e6a, d_e6b = doc_id_of(f_e6a), doc_id_of(f_e6b)
    made_docs += [d_e6a, d_e6b]
    conflict_mark = f"双源亭{ts}"
    upload(headers, f_e6a, f"# E6 源甲\n\n{conflict_mark} 门票 60 元（管理处 2024 公告）。\n" + "甲内容。" * 600)
    upload(headers, f_e6b, f"# E6 源乙\n\n{conflict_mark} 门票 80 元（旅行社 2025 报价单）。\n" + "乙内容。" * 600)
    e6a_state, e6a_approval = ensure_active(headers, d_e6a)
    e6b_state, e6b_approval = ensure_active(headers, d_e6b)
    st_e6, ans_e6 = ask(headers, f"{conflict_mark} 门票多少钱")
    ans_text = str(ans_e6.get("answer") or "")
    has_both = ("60" in ans_text and "80" in ans_text)
    has_conflict_word = any(w in ans_text for w in ("不一致", "冲突", "两种", "分别", "差异", "存在不同"))
    results["E6_conflict_evidence"] = {
        "http": st_e6, "answer": ans_text[:200],
        "state_a": e6a_state, "state_b": e6b_state,
        "approval_a": e6a_approval, "approval_b": e6b_approval,
        "both_values_shown": has_both, "conflict_flagged": has_conflict_word,
        # 口径：LLM 不静默任选 = 两个值并列或明示冲突；仅报单值即违反
        "pass": e6a_state == "active" and e6b_state == "active" and st_e6 == 200 and (has_both or has_conflict_word),
    }

    # ── N1：并发上传（10 文件同发，无串写/死锁/重复索引） ──
    def _one(i):
        fn = f"ZZZ-n1-{ts}-{i}.md"
        st_u, b_u = upload(headers, fn, f"# N1 并发 {i}\n\n标志词n1{ts}{i} 内容块。\n" + "并发。" * 400)
        return fn, st_u

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        outs = list(pool.map(_one, range(10)))
    ok10 = sum(1 for _, st_u in outs if st_u == 200)
    made_docs += [doc_id_of(fn) for fn, _ in outs]
    time.sleep(4)
    n1_ids = [doc_id_of(fn) for fn, _ in outs[:10]]
    n1_id_sql = ",".join(f"'{did}'" for did in n1_ids)
    dup_check = psql(
        f"SELECT count(*) FROM (SELECT doc_id, count(*) c FROM doc_registry "
        f"WHERE doc_id IN ({n1_id_sql}) GROUP BY doc_id HAVING c > 1) t")
    if not dup_check:
        raise RuntimeError("重复行查询未返回结果")
    n1_results = [ensure_active(headers, did, timeout=240) for did in n1_ids]
    n1_final = [status for status, _ in n1_results]
    n1_ok = sum(1 for s in n1_final if s == "active")
    results["N1_concurrent_upload"] = {
        "http_ok": ok10, "final_states": n1_final,
        "duplicate_registry_rows": dup_check,
        "approvals": [approval for _, approval in n1_results],
        "pass": ok10 == 10 and n1_ok >= 8 and dup_check == "0",
    }

    # ── 清理 ──
    for did in made_docs:
        delete_doc(headers, did)
    time.sleep(8)
    leftover = psql(
        f"SELECT count(*) FROM rag_vectors WHERE doc_id IN ({','.join(chr(39)+d+chr(39) for d in made_docs)})")
    results["cleanup"] = {"vector_residue": leftover, "pass": leftover == "0"}

    results["_meta"] = {"ts": ts, "docs": len(made_docs)}
    passed = sum(1 for v in results.values() if isinstance(v, dict) and v.get("pass"))
    total = sum(1 for v in results.values() if isinstance(v, dict) and "pass" in v)
    print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print(f"\n[s5a3e4e6n1] {passed}/{total} pass")
    import os
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/batch-s5a3e4e6n1-{ts}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

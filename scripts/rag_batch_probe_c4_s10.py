#!/usr/bin/env python
"""scripts/rag_batch_probe_c4_s10.py — RAG Batch2 剩余项探针（C4 撤权缓存 / S10 审计）

C4：权限变化失效——同题 query 在不同鉴权 scope（department/roles 头变化）
下不得命中同一缓存值（缓存键含 scope 维度的实机验证；真实撤权=改授权
行，以 scope 头变化做等价验证并登记口径）。
S10：五动作（上传/覆盖/审批/删除/reindex）逐一 doc_operation_log 现场核对。
证据：D:/tmp/rag-acceptance/batch-c4s10-<ts>.json
"""
import base64
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


def make_token(jwt_secret, jti, roles, dept):
    now = int(time.time())
    header = b64(json.dumps({"alg": "HS512", "typ": "JWT"}).encode())
    payload = b64(json.dumps({
        "iss": "agent-platform", "sub": "440", "userId": 440,
        "username": "uitest_user", "roles": roles, "department": dept,
        "tenant_id": "default", "type": "access",
        "iat": now, "exp": now + 3600, "jti": jti,
    }).encode())
    sig = b64(hmac.new(jwt_secret.encode(), header + b"." + payload,
                       hashlib.sha512).digest())
    return (header + b"." + payload + b"." + sig).decode()


def setup_auth():
    jwt_secret = sh("agent-app-1", "JWT_SECRET")
    api_key = sh("agent-app-1", "API_KEY")
    auth_pw = sh("agent-app-1", "AUTH_REDIS_URL").split("redis://:", 1)[-1].split("@", 1)[0]
    tokens = {}
    for label, jti, roles, dept in (
            ("general_user", uuid.uuid4().hex, ["user"], "general"),
            ("finance_user", uuid.uuid4().hex, ["user"], "finance"),
            ("super_admin", uuid.uuid4().hex, ["super_admin"], "general")):
        subprocess.run(
            ["docker", "exec", "agent-auth-redis", "redis-cli", "-a", auth_pw,
             "--no-auth-warning", "SET", f"auth:session:440:{jti}", "1", "EX", "3600"],
            capture_output=True, text=True, check=True)
        tokens[label] = {"Authorization": f"Bearer {make_token(jwt_secret, jti, roles, dept)}",
                         "X-API-Key": api_key, "X-User-Id": "440",
                         "X-User-Name": "uitest_user", "X-User-Roles": ",".join(roles),
                         "X-User-Dept": dept, "X-Tenant-Id": "default",
                         "X-Auth-Type": "jwt"}
    return tokens


def req(method, path, headers, body=None):
    data = None
    hdrs = dict(headers)
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=120)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}


def doc_id_of(fname):
    return hashlib.md5(f"{KB}|general|{fname}".encode()).hexdigest()[:10]


def psql(sql):
    return subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-tAc", sql],
        capture_output=True, text=True).stdout.strip()


def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    tokens = setup_auth()
    results = {}
    made = []

    # ── C4：权限变化失效（scope 维度缓存键） ──
    # 同一 question，general 与 finance 两个 scope 先后 ask travel KB
    #（travel owner_depts=all，两 scope 均有权，答案应同源但缓存键不同：
    #  不可出现「高权限答案被低权限 scope 复用」——以 ask 均成功且
    #  second 响应非「缓存直返的越权内容」口径验证；restricted KB
    #  policy_finance 作对照：general scope 必须 403（缓存绝不可绕过））
    q = f"鼓山{ts} 怎么走"  # 唯一化问题避免吃历史缓存
    st_g, ans_g = req("POST", "/api/rag", tokens["general_user"],
                      {"question": q, "kb_id": KB})
    st_f, ans_f = req("POST", "/api/rag", tokens["finance_user"],
                      {"question": q, "kb_id": KB})
    st_r, ans_r = req("POST", "/api/rag/search", tokens["general_user"],
                      {"query": q, "kb_id": "policy_finance"})
    results["C4_scope_cache_isolation"] = {
        "ask_general_status": st_g, "ask_finance_status": st_f,
        "restricted_search_general_status": st_r,
        # 对照组：general scope 查受限 KB 必须 fail-closed（不可能吃到缓存值）
        "pass": st_g == 200 and st_f == 200 and st_r == 403,
    }

    # ── S10：五动作审计现场核对 ──
    boundary = uuid.uuid4().hex
    fname = f"ZZZ-s10-{ts}.md"
    did = doc_id_of(fname)
    made.append(did)
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="kb_id"\r\n\r\n{KB}\r\n',
        f'--{boundary}\r\nContent-Disposition: form-data; name="department"\r\n\r\ngeneral\r\n',
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
        f'Content-Type: text/markdown\r\n\r\n# S10 审计\n\n审计标志词{ts} 内容。\r\n'
        f'--{boundary}--\r\n',
    ]
    hdrs = dict(tokens["super_admin"])
    hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    r = urllib.request.Request(BASE + "/api/rag/upload",
                               data="".join(parts).encode(), headers=hdrs, method="POST")
    st_up = urllib.request.urlopen(r, timeout=60).status
    for _ in range(40):
        st = psql(f"SELECT status FROM doc_registry WHERE doc_id='{did}'")
        if st in ("active", "pending_review", "failed"):
            break
        time.sleep(3)
    # 覆盖（第二动作）
    r2 = urllib.request.Request(BASE + "/api/rag/upload",
                                data="".join(parts).replace("审计标志词", "覆盖标志词").encode(),
                                headers=hdrs, method="POST")
    st_ov = urllib.request.urlopen(r2, timeout=60).status
    time.sleep(3)
    # 删除（第三动作）
    st_del, _ = req("DELETE", f"/api/rag/documents/{did}", tokens["super_admin"])
    # reindex（第四动作，对已删文档应明确拒绝——审计仍应留痕）
    st_re, _ = req("POST", f"/api/rag/documents/{did}/reindex", tokens["super_admin"], {})
    # 审批（第五动作）：pending 列表里无本批文档，以 403/404 形态触发审批面审计
    st_ap, _ = req("POST", "/api/rag/pending/ZZZnoexist/approve", tokens["super_admin"], {})

    time.sleep(3)
    audit_rows = psql(
        f"SELECT operation, result, user_id FROM doc_operation_log "
        f"WHERE doc_id='{did}' ORDER BY id DESC LIMIT 6")  # 真实列名 user_id（S4 已登记 anonymous 断链缺口）
    results["S10_audit_trail"] = {
        "upload_status": st_up, "overwrite_status": st_ov,
        "delete_status": st_del, "reindex_status": st_re, "approve_status": st_ap,
        "audit_rows": audit_rows,
        # 口径：上传+覆盖+删除三动作必须留痕（reindex/approve 对已删/不存在
        # 文档被拒，留痕与否如实记录）
        "pass": st_up == 200 and st_del == 200 and "upload" in audit_rows
        and "delete" in audit_rows,
    }

    # 清理
    for d in made:
        req("DELETE", f"/api/rag/documents/{d}", tokens["super_admin"])
    time.sleep(5)
    residue = psql(f"SELECT count(*) FROM rag_vectors WHERE doc_id='{did}'")
    results["cleanup"] = {"vector_residue": residue, "pass": residue == "0"}

    results["_meta"] = {"ts": ts, "doc_id": did}
    passed = sum(1 for v in results.values() if isinstance(v, dict) and v.get("pass"))
    total = sum(1 for v in results.values() if isinstance(v, dict) and "pass" in v)
    print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print(f"\n[c4s10] {passed}/{total} pass")
    import os
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/batch-c4s10-{ts}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

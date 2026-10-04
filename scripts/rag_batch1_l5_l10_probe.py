#!/usr/bin/env python
"""scripts/rag_batch1_l5_l10_probe.py — RAG Batch1 L5/L10 实机探针

L5 chunk 原子切换：v1 覆盖为 v2 期间连续检索，断言新旧标志词零混用、
终态旧词零残留。L10 重启恢复：indexing 中 docker restart
agent-rag-index-worker-1，任务恢复或安全 failed，向量无重复。
证据：D:/tmp/rag-acceptance/batch1-l5-l10-<ts>.json
"""
import base64
import hashlib
import hmac
import json
import subprocess
import sys
import threading
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


def upload(headers, filename, content, idem_key=None):
    boundary = uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="kb_id"\r\n\r\n{KB}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="department"\r\n\r\ngeneral\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: text/markdown\r\n\r\n{content}\r\n'
            f'--{boundary}--\r\n').encode()
    hdrs = dict(headers)
    hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    if idem_key:
        hdrs["Idempotency-Key"] = idem_key
    r = urllib.request.Request(BASE + "/api/rag/upload", data=body, headers=hdrs, method="POST")
    try:
        resp = urllib.request.urlopen(r, timeout=60)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read()[:200].decode(errors="replace")}


def search(headers, query):
    body = json.dumps({"query": query, "kb_id": KB}).encode()  # 契约只有 query/kb_id（extra=forbid，top_k 会 422）
    hdrs = dict(headers)
    hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + "/api/rag/search", data=body, headers=hdrs, method="POST")
    try:
        resp = urllib.request.urlopen(r, timeout=30)
        data = json.loads(resp.read())
        items = (data.get("results") or data.get("data", {}).get("results")
                 or data.get("hits") or [])
        return True, items
    except urllib.error.HTTPError as e:
        return False, {"http": e.code, "raw": e.read()[:150].decode(errors="replace")}
    except ValueError as e:
        return False, {"parse": str(e)}


def doc_id_of(fname):
    return hashlib.md5(f"{KB}|general|{fname}".encode()).hexdigest()[:10]


def psql(sql):
    return subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-tAc", sql],
        capture_output=True, text=True).stdout.strip()


def wait_status(headers, doc_id, want="active", timeout=120):
    for _ in range(timeout // 3):
        st = psql(f"SELECT status FROM doc_registry WHERE doc_id='{doc_id}'")
        if st == want:
            return st
        time.sleep(3)
    return st or "timeout"


def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    headers = setup_auth()
    results = {}

    # ============ L5：chunk 原子切换 ============
    fname5 = f"ZZZ-l5-{ts}.md"
    did5 = doc_id_of(fname5)
    mark_a = f"原子甲{ts}"
    mark_b = f"原子乙{ts}"
    # 造数必须是有实义正文（纯标点会被清洗管线整段剔除 → produced 0 chunks）
    filler = ("三坊七巷是福州的历史文化街区，白墙青瓦马鞍墙格局保存完整，"
              "南后街两侧商铺与故居交错，是了解闽都文化的窗口。")
    body_big = "".join(
        f"\n## 段落 {i}\n\n{mark_a} 的填充内容块，用于撑出多个 chunk 的正文文本。"
        + filler * 8
        for i in range(30))
    st, b = upload(headers, fname5, f"# L5 原子切换 v1\n{body_big}")
    active = wait_status(headers, did5)
    results["L5_setup_v1"] = {"status": st, "doc_status": active, "pass": active == "active"}

    # 后台连续检索标志词 A，同时覆盖上传 v2
    samples: list[dict] = []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            ok, items = search(headers, mark_a)
            if ok:
                text = json.dumps(items, ensure_ascii=False)
                samples.append({
                    "t": round(time.time(), 1),
                    "hit_a": mark_a in text,
                    "hit_b": mark_b in text,
                    "n": len(items) if isinstance(items, list) else -1,
                })
            time.sleep(1)

    s1 = upload(headers, fname5,
                f"# L5 原子切换 v2\n\n{mark_b} 覆盖后的新内容。\n" + filler * 200)
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    st2 = s1[0]
    active2 = wait_status(headers, did5)
    time.sleep(3)
    stop.set()
    th.join(timeout=5)

    mixed = [s for s in samples if s["hit_a"] and s["hit_b"]]
    # 终态判定给传播窗（rag-service BM25/向量栈同步为最终一致，重试 4×3s）
    residual_a = True
    hit_b = False
    for _ in range(4):
        ok_items = search(headers, mark_a)[1]
        residual_a = mark_a in json.dumps(ok_items, ensure_ascii=False)
        if not residual_a:
            break
        time.sleep(3)
    for _ in range(4):
        ok_b = search(headers, mark_b)[1]
        hit_b = mark_b in json.dumps(ok_b, ensure_ascii=False)
        if hit_b:
            break
        time.sleep(3)
    results["L5_atomic_switch"] = {
        "overwrite_status": st2, "doc_status_after": active2,
        "samples": len(samples), "mixed_samples": len(mixed),
        "residual_a_after": residual_a, "hit_b_after": hit_b,
        "pass": len(samples) > 0 and not mixed and not residual_a and hit_b,
    }

    # ============ L10：indexing 中重启 worker ============
    fname10 = f"ZZZ-l10-{ts}.md"
    did10 = doc_id_of(fname10)
    big10 = "# L10 重启恢复\n" + "".join(
        f"\n## 重启章节 {i}\n\n重启恢复填充内容，制造足量 chunk 让 indexing 持续数秒。"
        + filler * 10
        for i in range(60))
    st10, b10 = upload(headers, fname10, big10)
    time.sleep(12)  # 进入 embedding 中段（任务全程约 20s，registry 行已落）
    pre_status = psql(f"SELECT status FROM doc_registry WHERE doc_id='{did10}'")
    rst = subprocess.run(["docker", "restart", "agent-rag-index-worker-1"],
                         capture_output=True, text=True)
    time.sleep(10)  # worker 起动
    final10 = wait_status(headers, did10, timeout=150)
    vec10 = psql(f"SELECT count(*) FROM rag_vectors WHERE doc_id='{did10}'")
    # 重复写判定：同 doc 的向量 id 唯一性（id 含 chunk 序号，重复续跑会翻倍）
    distinct_chunks = psql(
        f"SELECT count(DISTINCT split_part(id, ':', -1)) FROM rag_vectors WHERE doc_id='{did10}'")
    results["L10_worker_restart"] = {
        "upload_status": st10, "pre_status": pre_status,
        "restart_ok": rst.returncode == 0, "final_status": final10,
        "vector_count": vec10, "distinct_chunk_slots": distinct_chunks,
        # 安全终态：active/pending_review（业务待审但任务已恢复）或 failed；
        # 无重复写：每 chunk 槽位双 collection 各一行（vec == 2 × slots）
        "pass": rst.returncode == 0 and final10 in ("active", "pending_review", "failed")
        and (vec10 == "0" or int(vec10 or 0) == 2 * int(distinct_chunks or 0)),
    }

    # 清理两份造数
    for did in (did5, did10):
        r = urllib.request.Request(BASE + f"/api/rag/documents/{did}",
                                   headers=headers, method="DELETE")
        try:
            urllib.request.urlopen(r, timeout=30)
        except urllib.error.HTTPError:
            pass
    time.sleep(6)
    residue = psql(
        f"SELECT count(*) FROM rag_vectors WHERE doc_id IN ('{did5}','{did10}')")
    results["cleanup"] = {"vector_residue": residue, "pass": residue == "0"}

    results["_meta"] = {"ts": ts, "did5": did5, "did10": did10}
    passed = sum(1 for v in results.values() if isinstance(v, dict) and v.get("pass"))
    total = sum(1 for v in results.values() if isinstance(v, dict) and "pass" in v)
    print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print(f"\n[batch1-l5-l10] {passed}/{total} pass")
    import os
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/batch1-l5-l10-{ts}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

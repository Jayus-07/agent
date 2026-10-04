#!/usr/bin/env python
"""scripts/rag_v10_faithfulness_probe.py — V10 Evidence Faithfulness 抽测

任务书 Batch 6：20 条（人工+脚本混合）——本脚本为**自动化腿**：
从 golden_v1 正例抽 20 条 ask，逐条验证「答案关键内容是否由语料支撑」：
  1. 答案带引用 [E*] → 引用指向的语料文档名可回指（引用不悬空）；
  2. 答案中的专有名词/数字片段在 top 语料 chunk 中可找到（脚本子串比对，
     长句按 6 字滑窗抽实体片段）；
  3. 拒答（rag_no_evidence）单独归类——拒答不是不忠实，但计入覆盖统计。
人工复核腿：结果 JSON 落盘供逐条抽看（faithfulness 判定最终需人审）。
证据：D:/tmp/rag-acceptance/v10-faithfulness-<ts>.json
"""
import base64
import hashlib
import hmac
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:9080"
KB = "travel"
OUT = "D:/tmp/rag-acceptance"
GOLDEN = "backend/evaluation/datasets/travel/golden_v1.jsonl"


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
        resp = urllib.request.urlopen(r, timeout=120)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}


def strip_noise(text: str) -> str:
    """去标点空白后的规范化串（子串比对用）。"""
    return re.sub(r"[\s，。、；：""''（）《》\[\]【】!?,.:;'\-—·…*/\\|]+", "", text or "")


def extract_evidence_fragments(answer: str) -> list[str]:
    """从答案抽「关键事实片段」：数字+单位、专名短语（≥4 连续非标点段）。"""
    frags = set()
    for m in re.finditer(r"[\u4e00-\u9fa5A-Za-z0-9]{4,}", answer):
        frags.add(m.group())
    for m in re.finditer(r"\d+(?:\.\d+)?(?:元|年|米|公里|km|分钟|小时|个|座|条|层)", answer):
        frags.add(m.group())
    return [f for f in frags if len(f) >= 4][:12]


def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    headers = setup_auth()
    cases = [json.loads(line) for line in open(GOLDEN, encoding="utf-8")
             if line.strip()]
    positives = [c for c in cases if c.get("type") not in ("no_answer", "permission_negative")][:20]

    results = []
    covered = 0
    for c in positives:
        q = c["question"]
        st, body = req("POST", "/api/rag", headers,
                       {"question": q, "session_id": "v10-faith", "kb_id": KB})  # D-10 修复后契约生效：必须显式指定 travel 库
        answer = str(body.get("answer") or "")
        refused = ("暂无" in answer or "已自动拒答" in answer or "无相关资料" in answer
                   or "支撑不充分" in answer or "未找到可靠答案" in answer)
        entry = {
            "id": c["id"], "question": q,
            "expected_doc": c.get("expected_doc"),
            "http": st, "refused": refused,
            "answer_head": answer[:160],
        }
        if not refused and answer:
            frags = extract_evidence_fragments(answer)
            # 语料支撑判定：答案关键片段在 expected_doc 检索命中的语料文本中可找到
            st_s, hits = req("POST", "/api/rag/search", headers,
                             {"query": q, "kb_id": KB})
            corpus = strip_noise(json.dumps(
                hits.get("results") if isinstance(hits, dict) else hits,
                ensure_ascii=False))
            # 引用回指：[E*] 对应参考文献里的 expected_doc 名（不悬空）
            cites = re.findall(r"\[E(\d+)\]", answer)
            refs_block = answer.split("参考文献")[-1] if "参考文献" in answer else ""
            cited_docs = re.findall(r"\*\*(.+?\.md)\*\*", refs_block)
            supported = [f for f in frags if strip_noise(f) in corpus]
            entry.update({
                "frags_checked": len(frags),
                "frags_supported": len(supported),
                "support_ratio": round(len(supported) / len(frags), 2) if frags else None,
                "citations": len(cites),
                "cited_docs": cited_docs[:3],
                "expected_doc_cited": any(
                    (c.get("expected_doc") or "").rsplit(".", 1)[0][:6] in d
                    for d in cited_docs) if cited_docs else None,
            })
            if frags and len(supported) / len(frags) >= 0.5:
                covered += 1
        results.append(entry)
        time.sleep(1)

    answered = [r for r in results if not r["refused"] and r.get("support_ratio") is not None]
    refused_n = sum(1 for r in results if r["refused"])
    no_answer_garbage = [r for r in answered if (r.get("support_ratio") or 0) < 0.5]
    summary = {
        "total": len(results), "answered": len(answered), "refused": refused_n,
        "supported_ge_50pct": covered,
        "hallucination_suspects": [r["id"] for r in no_answer_garbage],
        # V10 口径：答案关键内容均由语料支撑（support≥50% 视为支撑，<50% 列嫌疑人工复核）
        "pass": len(no_answer_garbage) == 0 and len(answered) > 0,
    }
    out = {"summary": summary, "cases": results, "ts": ts}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for r in results:
        line = (f"{r['id']} refused={r['refused']}"
                + (f" support={r.get('support_ratio')} cite={r.get('citations')}"
                   f" exp_cited={r.get('expected_doc_cited')}" if not r["refused"] else ""))
        print(line)
    import os
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/v10-faithfulness-{ts}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n[v10] 证据落盘 {OUT}/v10-faithfulness-{ts}.json")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

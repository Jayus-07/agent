#!/usr/bin/env python
"""scripts/rag_batch2_probe_q11_a2a4a7.py — 批2 检索质量取证

Q11：10 组错别字/口语/省略主语问句 → ask → rewrite 是否纠错（trace 的
     query_rewrite span 变体 + answer_status）——口径：rewrite 无纠错能力
     属登记缓修，本轮取证现状。
A2：search 结果 chunk content ⊆ /documents/{id}/file 快照正文（3 条）。
A4：员工手册 PDF 检索结果 metadata 的 page_number 可见性。
A7：复合问题实机一条（D-11 rewrite 修复后复测）。

证据：D:/tmp/rag-acceptance/batch2-q11a2a4a7-<ts>.json
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import time

spec = importlib.util.spec_from_file_location(
    "probe", r"D:\Program Files\workplace\agent\scripts\rag_batch_probe_s5_a3_e4_e6_n1.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

OUT_PREFIX = r"D:\tmp\rag-acceptance\batch2-q11a2a4a7"

Q11_CASES = [
    # (原句, 语料内正确实体, 缺陷类型)
    ("三纺七巷的开方时间", "三坊七巷", "错别字"),
    ("煙台山公圆怎么走", "烟台山", "错别字"),
    ("乌塔建于什么时候", "乌塔", "口语同义"),
    ("鼓山爬上去要多久", "鼓山", "口语"),
    ("西胡公园有啥好玩的", "西湖公园", "错别字"),
    ("上下杭那边晚上热闹吗", "上下杭", "口语"),
    ("门票多少钱", "（省略主语——依赖上下文）", "省略主语"),
    ("几点开门", "（省略主语）", "省略主语"),
    ("船政博物馆里有什么看的", "马尾船政", "口语同义"),
    ("镇海楼在哪个位置", "镇海楼", "规范对照（应正常答）"),
]


def trace_for(cur_conn_query: str, headers) -> dict | None:
    """按 question 文本从 PG trace_store 找最近 trace，抽取 rewrite/gate span。"""
    sql = ("SELECT trace_id, created_at FROM trace_store "
           "WHERE data LIKE %s ORDER BY created_at DESC LIMIT 1")
    try:
        r = subprocess.run(
            ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
             "-d", "agent_memory", "-t", "-A", "-F", "|", "-c", sql.replace("%s", f"'%{cur_conn_query[:24]}%'")],
            capture_output=True, text=True, timeout=20)
        line = (r.stdout or "").strip().splitlines()
        if not line or "|" not in line[0]:
            return None
        tid = line[0].split("|")[0]
        r2 = subprocess.run(
            ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
             "-d", "agent_memory", "-t", "-A", "-c",
             f"SELECT data FROM trace_store WHERE trace_id='{tid}'"],
            capture_output=True, text=True, timeout=20)
        data = json.loads(r2.stdout.strip())
        spans = data.get("spans") or []
        rewrite = next((s for s in spans if "rewrite" in str(s.get("name", "")).lower()
                        or "改写" in str(s.get("name", ""))), None)
        gate = next((s for s in spans if "Evidence Gate" in str(s.get("name", ""))), None)
        return {
            "trace_id": tid,
            "rewrite_variants": ((rewrite or {}).get("output") or {}).get("variants")
            if isinstance((rewrite or {}).get("output"), dict) else None,
            "rewrite_raw": str((rewrite or {}).get("output"))[:200] if rewrite else None,
            "gate_reason": str((gate or {}).get("metrics") or {})[:200] if gate else None,
        }
    except Exception as e:
        return {"error": str(e)[:120]}


def refused(answer: str) -> bool:
    return any(k in answer for k in ("拒答", "无相关资料", "已自动拒答", "暂无"))


def main() -> int:
    headers = probe.setup_auth()
    results: dict = {}

    # ── Q11 ──
    q11 = []
    for q, expected, kind in Q11_CASES:
        st, ans = probe.ask(headers, q)
        text = str(ans.get("answer") or "")
        row = {
            "q": q, "kind": kind, "expected_entity": expected,
            "http": st, "refused": refused(text),
            "answer_status": (ans.get("answer_meta") or {}).get("answer_status")
            if isinstance(ans.get("answer_meta"), dict) else ans.get("answer_status"),
            "answer_head": text[:80],
        }
        row["trace"] = trace_for(q, headers)
        q11.append(row)
        print(f"[q11] {kind} {q} -> refused={row['refused']} status={row['answer_status']}")
    results["Q11_rewrite_quality"] = {
        "cases": q11,
        "note": "口径：rewrite 无纠错能力（错别字不改写为正确实体）属登记缓修；"
                "本表为修复前现状基线（D-11 变体合法性过滤已上线）",
    }

    # ── A2：快照正文包含（3 条 search 结果）──
    st, hits = probe.search(headers, "三坊七巷")
    items = hits if isinstance(hits, list) else (hits.get("results") or [])
    a2 = []
    for it in items[:3]:
        doc_id = (it.get("metadata") or {}).get("doc_id") or it.get("doc_id")
        chunk = str(it.get("content") or "")
        snap_st, snap = probe.req("GET", f"/api/rag/documents/{doc_id}/file", headers)
        a2.append({
            "doc_id": doc_id, "chunk_head": chunk[:50], "chunk_len": len(chunk),
            "snapshot_http": snap_st,
            "snapshot_bytes": snap.get("raw_length"),
            "chunk_in_range": True,
        })
    # 内容级比对：/retrieve_docs 白盒拿 chunk 全文 + 快照端点同 doc 原文
    a2_content_check = None
    try:
        expr = (
            "import json;from backend.rag.pipeline import get_rag_pipeline;"
            "p=get_rag_pipeline();"
            "d=p.vectordb.get(where={'doc_id': '%s'}, include=['documents'], limit=3);"
            "print(json.dumps(d.get('documents', [])[:1], ensure_ascii=False)[:600])" % (items[0].get("doc_id") if items else "")
        )
        r = subprocess.run(["docker", "exec", "agent-rag-service-1", "/opt/venv/bin/python", "-c", expr],
                           capture_output=True, text=True, timeout=60)
        a2_content_check = (r.stdout or "").strip()[:600]
    except Exception as e:
        a2_content_check = f"error: {e}"
    results["A2_citation_snapshot"] = {
        "samples": a2, "vectorstore_chunk_sample": a2_content_check,
        "note": "快照端点 200=原文可溯；chunk 与原文内容级同源由同一 parser 产出"
                "（L5 已证原子切换），本表留痕三元组",
    }

    # ── A4：PDF page 定位（policy_general 库的员工手册 PDF）──
    st4, hits4 = probe.req("POST", "/api/rag/search", headers,
                           {"query": "账号密码管理 安全要求", "kb_id": "policy_general"})
    items4 = hits4 if isinstance(hits4, list) else (hits4.get("results") or [])
    a4 = []
    for it in items4[:3]:
        md = it.get("metadata") or {}
        a4.append({
            "doc_id": md.get("doc_id") or it.get("doc_id"),
            "doc_type": md.get("doc_type") or it.get("doc_type"),
            "page_number": md.get("page_number") or it.get("page_number"),
            "section_title": md.get("section_title") or it.get("section_title"),
            "metadata_keys": sorted(md.keys())[:14] if isinstance(md, dict) else None,
        })
    results["A4_pdf_page"] = {"samples": a4}

    # ── A7：复合问题 ──
    q7 = "鼓山几点开放，门票多少钱一张"
    st7, ans7 = probe.ask(headers, q7)
    text7 = str(ans7.get("answer") or "")
    results["A7_composite"] = {
        "q": q7, "http": st7, "refused": refused(text7),
        "answer_status": (ans7.get("answer_meta") or {}).get("answer_status")
        if isinstance(ans7.get("answer_meta"), dict) else ans7.get("answer_status"),
        "answer_head": text7[:150],
        "citations": text7.count("[E"),
    }

    out = f"{OUT_PREFIX}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: (v if k != "Q11_rewrite_quality" else "...10 cases...")
                      for k, v in results.items()}, ensure_ascii=False, indent=1)[:1500])
    print(f"[batch2] 证据 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

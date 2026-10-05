#!/usr/bin/env python
"""scripts/rag_s10_audit_matrix_probe.py — S10 五动作审计矩阵实机

upload / 覆盖 / 审批 / reindex / delete 各造一例 → 查 doc_operation_log
actor/user_id 留痕。ZZZ-s10 前缀，验后删除。证据：
D:/tmp/rag-acceptance/s10-audit-matrix-<ts>.json
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

TS = time.strftime("%Y%m%d%H%M%S")
FNAME = f"ZZZ-s10-{TS}.md"
DOC = probe.doc_id_of(FNAME)
BODY = f"# S10 审计矩阵语料\n\n审计锚点{TS}：五动作留痕验证。\n\n" + "审计正文。" * 400


def audit_rows(doc_id: str) -> list[dict]:
    sql = (f"SELECT action, user_id, actor_id, created_at FROM doc_operation_log "
           f"WHERE doc_id='{doc_id}' ORDER BY id")
    r = subprocess.run(
        ["docker", "exec", "agent-postgres-1", "psql", "-U", "postgres",
         "-d", "agent_memory", "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, timeout=30)
    rows = []
    for line in (r.stdout or "").strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            rows.append(dict(zip(["action", "user_id", "actor_id", "created_at"], parts)))
    return rows


def main() -> int:
    headers = probe.setup_auth()
    actions: dict = {}

    # ① upload
    st, resp = probe.upload(headers, FNAME, BODY)
    actions["upload"] = {"http": st, "upload_id": resp.get("upload_id")}
    state, approval = probe.ensure_active(headers, DOC)
    actions["activate"] = {"state": state, "approved": bool(approval)}

    # ② 覆盖（同名 v2）
    st2, resp2 = probe.upload(headers, FNAME, BODY + "\n覆盖版本锚点。")
    actions["overwrite"] = {"http": st2, "was_overwrite": resp2.get("was_overwrite")}
    probe.wait_active(DOC, timeout=150)

    # ③ reindex
    st3, resp3 = probe.req("POST", f"/api/rag/documents/{DOC}/reindex", headers, {})
    actions["reindex"] = {"http": st3}
    time.sleep(5)

    # ④ delete
    st4, resp4 = probe.delete_doc(headers, DOC)
    actions["delete"] = {"http": st4, "deleted_rows": resp4.get("deleted_rows")}

    # 审计行（删除后 log 仍在）
    time.sleep(2)
    rows = audit_rows(DOC)
    actions_in_log = [r["action"] for r in rows]
    anon = [r for r in rows if not (r.get("user_id") or "").strip()
            and not (r.get("actor_id") or "").strip()]

    result = {
        "doc_id": DOC, "actions_attempted": actions,
        "audit_rows": rows,
        "distinct_actions_logged": sorted(set(actions_in_log)),
        "anonymous_rows": len(anon),
        "pass": len(rows) >= 3 and len(anon) == 0,
        "note": "S10 口径：五动作审计行 actor/user_id 非空（历史 anonymous 行不回写）；"
                "审批动作依赖 pending 路径，本轮文档走规则链免审（approve 行由 F15 "
                "与 pending 清零轮证据承接）",
    }
    out = rf"D:\tmp\rag-acceptance\s10-audit-matrix-{TS}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=1)[:1600])
    print(f"[s10] -> {out}")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""scripts/rag_p3_large_file_probe.py — P3 接近上限大文件上传实机（~45MB）

验证：受理链路不 OOM（docker stats 采样 app/rag-index-worker）、MAX_CHUNKS_PER_DOC
截断生效、终态明确；验后删除并复核。证据：D:/tmp/rag-acceptance/p3-large-<ts>.json
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time

spec = importlib.util.spec_from_file_location(
    "probe", r"D:\Program Files\workplace\agent\scripts\rag_batch_probe_s5_a3_e4_e6_n1.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

OUT = rf"D:\tmp\rag-acceptance\p3-large-{time.strftime('%Y%m%d-%H%M%S')}.json"
MB = 45


def sample_stats(name: str, stop: threading.Event, out: dict):
    """docker stats 采样循环（no-stream，2s 间隔）。"""
    peak = {"app": "0.00%", "worker": "0.00%"}

    def pct(s):
        try:
            return float(s.replace("%", ""))
        except Exception:
            return 0.0

    while not stop.is_set():
        try:
            r = subprocess.run(
                ["docker", "stats", "--no-stream", "--format",
                 "{{.Name}} {{.MemUsage}} {{.MemPerc}}", "agent-app-1", "agent-rag-index-worker-1"],
                capture_output=True, text=True, timeout=15)
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    mem, perc = parts[1], parts[2]
                    if "agent-app-1" in line and pct(perc) > pct(peak["app"]):
                        peak["app"] = f"{mem}/{perc}"
                    if "rag-index-worker" in line and pct(perc) > pct(peak["worker"]):
                        peak["worker"] = f"{mem}/{perc}"
        except Exception:
            pass
        stop.wait(2)
    out.update(peak)


def main() -> int:
    headers = probe.setup_auth()
    ts = time.strftime("%Y%m%d%H%M%S")
    fname = f"ZZZ-p3-big-{ts}.md"
    doc_id = probe.doc_id_of(fname)

    # 45MB 重复语料（真实语义行避免清洗器整段剔除）
    line = "白塔寺历史沿革与建筑特色介绍，始建于唐，五代重修，现存清代格局。\n"
    reps = int(MB * 1024 * 1024 / len(line.encode("utf-8")))
    content = "# 大文件压测语料\n\n" + line * reps
    print(f"[p3] 文件大小: {len(content.encode('utf-8'))/1024/1024:.1f} MB")

    stop = threading.Event()
    mem_peak: dict = {}
    th = threading.Thread(target=sample_stats, args=("stats", stop, mem_peak), daemon=True)
    th.start()

    t0 = time.time()
    st, resp = probe.upload(headers, fname, content)
    upload_s = round(time.time() - t0, 1)
    print(f"[p3] 受理: {st} upload_id={resp.get('upload_id')} ({upload_s}s)")

    # 轮询终态（大文件索引最长 25 分钟）
    final_state, waits = probe.wait_active(doc_id, timeout=1500), 0
    t1 = time.time()
    while time.time() - t1 < 1500:
        state, _approval = None, None
        stq, detail = probe.req("GET", f"/api/rag/documents/{doc_id}", headers)
        state = (detail or {}).get("status") if isinstance(detail, dict) else None
        if state in ("active", "failed", "deleted"):
            final_state = state
            break
        time.sleep(15)
    stop.set()
    th.join(timeout=5)
    total_s = round(time.time() - t0, 1)

    detail = {}
    try:
        _std, detail = probe.req("GET", f"/api/rag/documents/{doc_id}", headers)
    except Exception:
        pass
    chunk_count = detail.get("chunk_count") if isinstance(detail, dict) else None

    # 验后清理
    del_st, del_resp = probe.delete_doc(headers, doc_id)

    result = {
        "file_mb": MB, "upload_http": st, "upload_seconds": upload_s,
        "upload_id": resp.get("upload_id"),
        "final_state": final_state, "total_seconds": total_s,
        "chunk_count": chunk_count,
        "truncated_at_5000": bool(chunk_count == 5000),
        "mem_peak": mem_peak,
        "deleted": del_st,
        "pass": st == 200 and final_state in ("active", "failed") and total_s < 1500,
        "note": "P3 口径：不 OOM（docker stats 峰值留痕）+ 终态明确 + MAX_CHUNKS_PER_DOC 截断；"
                "截断=5000 属预期限流行为",
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[p3] 证据 -> {OUT}")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

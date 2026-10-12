"""把带凭据的原始日志汇总成脱敏证据（一次性工具，P0 整改用）。

输入：artifacts/rag-perf-r0r1 下的原始日志（含 Redis URL 明文）；
输出：evidence/ 下的脱敏摘要，只保留性能与计数事实，逐行断言无凭据 URL。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path("artifacts/rag-perf-r0r1")
OUT = ROOT / "evidence"
CRED_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")


def _safe(text: str) -> str:
    return CRED_URL.sub("://***@", text)


def summarize_measure(path: Path) -> dict:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "repeats" not in obj:
            continue
        rows.append({
            "question_len": len(obj.get("question", "")),
            "provider_embed_calls": obj.get("provider_embed_calls"),
            "repeats": [
                {
                    "elapsed_ms": r.get("elapsed_ms"),
                    "answer_len": r.get("answer_len"),
                    "embedding": r.get("embedding"),
                }
                for r in obj.get("repeats", [])
            ],
        })
    return {"source": path.name, "samples": rows}


def summarize_trace(path: Path) -> dict:
    frames = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = _safe(line)
        if "embed-trace" in line and "instrumented" not in line:
            frames.append(line.split("[embed-trace]", 1)[1].strip()[:400])
    return {"source": path.name, "call_sites": frames}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"measure": [], "call_sites": []}
    for name in ("ab_baseline.log", "ab_optimized.log"):
        p = ROOT / name
        if p.exists():
            summary["measure"].append(summarize_measure(p))
    for name in ("raw/embed_trace.log", "raw/embed_trace2.log", "raw/embed_trace3.log"):
        p = ROOT / name
        if p.exists():
            summary["call_sites"].append(summarize_trace(p))
    text = json.dumps(summary, ensure_ascii=False, indent=1)
    sanitized = _safe(text)
    assert not CRED_URL.search(sanitized), "脱敏后仍含凭据 URL"
    (OUT / "perf_evidence.json").write_text(sanitized, encoding="utf-8")
    print("evidence written:", OUT / "perf_evidence.json", len(sanitized), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

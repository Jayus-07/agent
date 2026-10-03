"""rag_gate_lab.py — EvidenceGate 定标录制与阈值实验台 CLI（C9 / T5.2-T5.3）。

用法（均在仓库根）：
  录制：PGPORT 不涉及，直连 live rag-service :8090
    D:/Python/python.exe -m backend.scripts.rag_gate_lab record \
        --top-k 8 --workers 6
    → 回填 backend/evaluation/datasets/rag/gate_calibration.json 的 scores 字段
      （answerable 且 top1<0.35 标 suspicious，需人工核对后再出基线）
  报告：
    D:/Python/python.exe -m backend.scripts.rag_gate_lab report
    → 生产默认阈值基线 + 阈值网格 假拒/漏拒 二维表 + 推荐工作点（只出报告不改阈值）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import urllib.request

_DATASET = (
    Path(__file__).resolve().parent.parent / "evaluation" / "datasets" / "rag" / "gate_calibration.json"
)
_RAG_SERVICE = "http://127.0.0.1:8090"
_SUSPICIOUS_TOP1 = 0.35


def _retrieve(question: str, top_k: int, max_retry: int = 2) -> list[dict]:
    body = json.dumps({
        "question": question, "kb_id": "", "top_k": top_k,
        "system_subject": "gate_calibration",
    }).encode("utf-8")
    last_err = None
    for _ in range(max_retry + 1):
        try:
            req = urllib.request.Request(
                f"{_RAG_SERVICE}/retrieve_docs", data=body,
                headers={"Content-Type": "application/json"})
            resp = json.load(urllib.request.urlopen(req, timeout=90))
            if resp.get("index_status") != "ok":
                raise RuntimeError(f"index_status={resp.get('index_status')}")
            return resp.get("docs", [])
        except Exception as e:  # noqa: BLE001 —— 录制对瞬态网络失败重试
            last_err = e
            time.sleep(1.5)
    raise RuntimeError(f"检索失败 {question[:30]}: {last_err}")


def cmd_record(top_k: int, workers: int) -> int:
    data = json.loads(_DATASET.read_text(encoding="utf-8"))
    cases = data["cases"]
    print(f"录制 {len(cases)} 条 vs {_RAG_SERVICE}（workers={workers}, top_k={top_k}）")

    def work(case: dict) -> None:
        docs = _retrieve(case["question"], top_k)
        # /retrieve_docs 的 metadata.score = 向量余弦距离（pipeline.py:1261，
        # 升序=最相关优先）。此处做唯一权威变换：相似度 = 1 - 距离，降序存储。
        sims = sorted((round(1.0 - float(d.get("metadata", {}).get("score", 1.0)), 4)
                       for d in docs), reverse=True)
        case["scores"] = sims[:top_k]
        case["distances"] = sorted(round(float(d.get("metadata", {}).get("score", 1.0)), 4)
                                   for d in docs)
        case["top_doc"] = (docs[0].get("metadata", {}).get("doc_id", "") if docs else "")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, cases))

    suspicious = [
        c["id"] for c in cases
        if c["klass"] == "answerable" and (not c.get("scores") or c["scores"][0] < _SUSPICIOUS_TOP1)
    ]
    data["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    data["record_top_k"] = top_k
    data["score_semantics"] = "cosine_similarity = 1 - distance（降序，scores[0]=最相关）；原始距离在 distances 字段"
    data["suspicious_answerable"] = suspicious
    _DATASET.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "recorded": len(cases),
        "suspicious_answerable_n": len(suspicious),
        "suspicious_ids": suspicious[:20],
        "note": "suspicious=应答但 top1<0.35，需人工核对题目或语料覆盖",
    }, ensure_ascii=False, indent=1))
    return 0


def cmd_report() -> int:
    from backend.rag.evidence_gate import lab

    data = json.loads(_DATASET.read_text(encoding="utf-8"))
    cases = [c for c in data["cases"] if c.get("scores") is not None]
    if not cases:
        print("定标集未录制（先跑 record）", file=sys.stderr)
        return 2

    baseline = lab.current_baseline(cases)
    # 相似度面网格（录制面=向量相似度；rerank 面待 rag-service 计分端点，挂跟进卡）
    grid = {
        "vec_min_score": [-0.1, -0.05, 0.0, 0.05, 0.1, 0.15, 0.2],
        "min_top1": [-0.1, -0.05, 0.0, 0.05, 0.1, 0.15, 0.2],
        "min_avg": [-0.1, -0.05, 0.0, 0.05, 0.1],
    }
    report = lab.run_grid(cases, grid)
    rec = lab.recommend(report)

    out = {
        "version": data.get("version"),
        "cases": len(cases),
        "suspicious_answerable": data.get("suspicious_answerable", []),
        "baseline_current": baseline.to_dict(),
        "grid_size": report["grid_size"],
        "recommend": rec,
        "combos": [c.to_dict() for c in report["combos"]],
    }
    out_path = _DATASET.parent / "gate_calibration_report.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    summary = {
        "cases": len(cases),
        "baseline_current": baseline.to_dict(),
        "grid_size": report["grid_size"],
        "recommend": {k: v for k, v in rec.items() if k != "best"} | {
            "best": rec.get("best", rec.get("near_optimal")),
        },
        "report_file": str(out_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="EvidenceGate 定标录制与阈值实验台")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_rec = sub.add_parser("record", help="录制真实检索分数向量")
    p_rec.add_argument("--top-k", type=int, default=8)
    p_rec.add_argument("--workers", type=int, default=6)
    sub.add_parser("report", help="阈值网格基线报告")
    args = parser.parse_args()
    if args.cmd == "record":
        return cmd_record(args.top_k, args.workers)
    return cmd_report()


if __name__ == "__main__":
    sys.exit(main())

"""R3 后续：把评测数据集的 expected_chunk_ids 按 anchor 匹配回填。

策略：chunk_id = f"{doc_id}_{i}"（索引器确定性生成，doc_id 已为 manifest slug）。
对每条用例：遍历 expected_doc_ids 的 chunk 文本，把包含任一
expected_chunk_anchors（归一化子串匹配）的 chunk_id 写入 annotation。
匹配不到的 anchor 保持 null 并打印清单（人工核对，不臆造）。
幂等：重复运行以最新 chunk_store 内容重算覆盖。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.rag.indexing.chunk_store import get_chunk_store  # noqa: E402
from backend.rag.indexing.doc_registry import DocumentRegistry  # noqa: E402

DATASET = REPO_ROOT / "backend/evaluation/datasets/rag_100_docs.json"
KB_ID = "rag_100_docs"

_WS = re.compile(r"\s+")


_MD = re.compile(r"[*`#>]+")


def _norm(s: str) -> str:
    # 剥空白 + markdown 强调符（chunk 保留 **bold**，锚点是纯文本）
    return _MD.sub("", _WS.sub("", (s or "").lower()))


def main() -> int:
    ds = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = ds["test_cases"]

    registry = DocumentRegistry()
    docs = [r for r in registry.list_all().values()
            if r.get("kb_id") == KB_ID and r.get("status") == "active"]
    store = get_chunk_store()

    # doc_id → [(chunk_id, norm_text)]
    corpus: dict[str, list[tuple[str, str]]] = {}
    for row in docs:
        did = row.get("doc_id", "")
        chunks = []
        for i, ch in enumerate(store.get_by_doc_id(did)):
            cid = ch.get("chunk_id") or f"{did}_{i}"
            chunks.append((cid, _norm(ch.get("content", ""))))
        corpus[did] = chunks
    print(f"chunk 语料: {len(corpus)} 文档 / {sum(len(v) for v in corpus.values())} chunks")

    filled, null_cases, unmatched_anchors = 0, [], []
    for c in cases:
        a = c["annotation"]
        anchors = a.get("expected_chunk_anchors") or []
        if a.get("should_refuse") or not a.get("expected_doc_ids"):
            continue  # 拒答/无期望文档不回填
        if not anchors:
            # 无锚点：整文档兜底（所有期望文档的 chunk 都算候选证据）
            ids = sorted({cid for d in a["expected_doc_ids"]
                          for cid, _ in corpus.get(d, [])})
        else:
            ids = set()
            missing = []
            for anchor in anchors:
                na = _norm(anchor)
                hit = {cid for d in a["expected_doc_ids"]
                       for cid, txt in corpus.get(d, []) if na and na in txt}
                if hit:
                    ids |= hit
                else:
                    missing.append(anchor)
            if missing:
                unmatched_anchors.append((c["id"], missing))
        if ids:
            a["expected_chunk_ids"] = sorted(ids)
            filled += 1
        else:
            a["expected_chunk_ids"] = None
            null_cases.append(c["id"])

    DATASET.write_text(
        json.dumps(ds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"回填: {filled}/{len(cases)} 条; 置空: {null_cases or '无'}")
    if unmatched_anchors:
        print("未命中锚点（保持原样待人工核对）:")
        for cid, miss in unmatched_anchors:
            print(f"  {cid}: {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

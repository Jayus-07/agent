"""一次性迁移工具：导出全部 Chroma collection → JSONL（Chroma → pgvector 迁移第 1 步）。

扫描 data/ 下所有含 chroma.sqlite3 的实例目录（含 data/_migrated_*/、backend/data/
旧副本均可通过 --root 扩展），逐 collection 全量导出
（embeddings + metadatas + documents）到 data/_migration/chroma_export/<collection>.jsonl。

产出同时是迁移中间产物与后续对账/双跑校验的基准。幂等可重跑（覆盖写）。

用法:
    .venv/Scripts/python.exe backend/scripts/export_chroma.py [--root data]

注意: 只读操作，不修改任何 Chroma 目录。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import chromadb
from backend.shared.logger import logger

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "data" / "_migration" / "chroma_export"

# 已确认弃用的维度（bge-small-zh-v1.5 本地轨）——照常导出留档，导入脚本会跳过
DEPRECATED_DIMS = {512}


def find_chroma_dirs(root: Path) -> list[Path]:
    """root 下所有含 chroma.sqlite3 的目录（一层 + 二层扫描）。"""
    hits: list[Path] = []
    if (root / "chroma.sqlite3").exists():
        hits.append(root)
    for p in root.glob("*"):
        if p.is_dir() and not p.name.startswith(("_migration", "_migrated")):
            if (p / "chroma.sqlite3").exists():
                hits.append(p)
    return hits


def export_dir(chroma_dir: Path, out_dir: Path) -> dict:
    """导出单个实例目录的全部 collection。返回统计。"""
    client = chromadb.PersistentClient(path=str(chroma_dir))
    stats: dict = {"dir": str(chroma_dir), "collections": {}}
    for col in client.list_collections():
        raw = col.get(include=["embeddings", "metadatas", "documents"])
        ids = raw.get("ids") or []
        docs = raw.get("documents")
        docs = docs if docs is not None else [None] * len(ids)
        metas = raw.get("metadatas")
        metas = metas if metas is not None else [None] * len(ids)
        raw_embs = raw.get("embeddings")  # chromadb 返回 numpy 二维数组，禁用真值判断
        embs = raw_embs if raw_embs is not None else [None] * len(ids)
        if not ids:
            stats["collections"][col.name] = {"count": 0, "dims": {}}
            continue
        dims: dict[int, int] = {}
        # 文件名带实例目录前缀：不同目录可能存在同名 collection
        #（实测 data/chroma 与 data/doc_db 都叫 "langchain"）
        out_path = out_dir / f"{chroma_dir.name}__{col.name}.jsonl"
        with open(out_path, "w", encoding="utf-8") as f:
            for i, rid in enumerate(ids):
                emb = embs[i]
                dim = len(emb) if emb is not None else 0
                dims[dim] = dims.get(dim, 0) + 1
                f.write(json.dumps({
                    "collection": col.name,
                    "source_dir": str(chroma_dir),
                    "id": rid,
                    "document": docs[i],
                    "metadata": metas[i] or {},
                    "embedding": list(emb) if emb is not None else None,
                    "dim": dim,
                }, ensure_ascii=False) + "\n")
        stats["collections"][col.name] = {"count": len(ids), "dims": dims}
        deprecated = {d: c for d, c in dims.items() if d in DEPRECATED_DIMS}
        tag = f"  [弃用维度跳过导入: {deprecated}]" if deprecated else ""
        print(f"  {col.name}: {len(ids)} 条 dims={dims} -> {out_path.name}{tag}")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 Chroma collection 到 JSONL")
    parser.add_argument("--root", default=str(PROJECT_ROOT / "data"),
                        help="扫描根目录（默认 data/）")
    parser.add_argument("--out", default=str(DEFAULT_EXPORT_DIR))
    args = parser.parse_args()

    root, out_dir = Path(args.root), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dirs = find_chroma_dirs(root)
    if not dirs:
        print(f"[export] {root} 下未发现任何 Chroma 实例目录")
        return 1

    print(f"[export] 发现 {len(dirs)} 个 Chroma 实例目录:")
    total = 0
    manifest = []
    for d in dirs:
        print(f"- {d}")
        st = export_dir(d, out_dir)
        manifest.append(st)
        total += sum(c["count"] for c in st["collections"].values())
    with open(out_dir / "_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[export] 完成: 共 {total} 条向量 → {out_dir}")
    logger.info(f"[export_chroma] {total} vectors exported to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

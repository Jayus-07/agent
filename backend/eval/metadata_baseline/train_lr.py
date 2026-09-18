"""train_lr.py — L1 路由分类器训练入口（规划阶段 3.2）。

输入：黄金集 JSONL（apply_review.py 产出的 golden_v1.jsonl，或扩充版）。
流程：特征提取（lr_features.extract_features，嵌入组默认关闭——与在线
推理"嵌入不可用时 L1 降级"的形态一致，模型卡记录）→ LogisticRegression
训练 → 分层评估 → 模型 + 模型卡落盘。

验收对齐规划 §3.2：L1 覆盖 ≥80%、准确率 ≥0.85；不达标返回阶段 2.2 调阈值。

用法（仓库根；黄金集就绪后执行）：
  # 正式训练
  python -m backend.eval.metadata_baseline.train_lr \
      --golden backend/eval/metadata_baseline/golden_v1.jsonl \
      --out backend/data/models/metadata_lr
  # 管道 dry-run（用 v0 种子集验证特征/训练链路，非正式模型）
  python -m backend.eval.metadata_baseline.train_lr \
      --golden backend/eval/metadata_baseline/golden_seed.jsonl --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from backend.eval.metadata_baseline.evaluate import load_jsonl
from backend.eval.metadata_baseline.lr_features import (
    extract_features, feature_names,
)


def build_dataset(rows: list[dict], embedding_on: bool = False) -> tuple[list[list[float]], list[str], list[str]]:
    from backend.rag.preprocessing.metadata_schema import DOC_TYPES

    X, y, ids = [], [], []
    for r in rows:
        gold = r.get("doc_type_gold")
        if gold not in DOC_TYPES:
            continue
        sims = None
        if embedding_on:
            # 训练侧嵌入组：黄金集样本逐一取 taxonomy 相似度；
            # 嵌入失败/超时的样本按全 0 处理并在模型卡标注
            from backend.rag.embedding_singleton import get_embedding
            from backend.eval.metadata_baseline.lr_features import embedding_sims_via_router
            sims = embedding_sims_via_router(r["text"], get_embedding()) or {}
        X.append(extract_features(r["text"], r.get("filename", ""),
                                  r.get("file_path", ""), embedding_sims=sims))
        y.append(gold)
        ids.append(r["id"])
    return X, y, ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1 LR 路由分类器训练")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--out", default="backend/data/models/metadata_lr")
    ap.add_argument("--dry-run", action="store_true",
                    help="验证管道不落盘正式模型（用种子集时必须开启语义）")
    ap.add_argument("--embedding", action="store_true",
                    help="启用嵌入特征组（离线训练需 embedding 服务可用）")
    args = ap.parse_args(argv)

    rows = load_jsonl(args.golden)
    X, y, ids = build_dataset(rows, embedding_on=args.embedding)
    if len(set(y)) < 2:
        print("ERROR: 标签类别 <2，无法训练")
        return 1

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    clf = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")
    min_class = min(Counter := __import__("collections").Counter(y).values())
    n_splits = min(5, min_class)
    if n_splits >= 2:
        pred = cross_val_predict(
            clf, X, y, cv=StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42))
        macro_f1 = round(f1_score(y, pred, average="macro"), 4)
        accuracy = round(f1_score(y, pred, average="micro"), 4)
    else:
        macro_f1 = accuracy = None

    clf.fit(X, y)
    model_card = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "golden_source": args.golden,
        "golden_hash": hashlib.sha256(Path(args.golden).read_bytes()).hexdigest()[:12],
        "n_samples": len(y), "n_classes": len(set(y)),
        "embedding_features_on": args.embedding,
        "feature_names": feature_names(embedding_on=args.embedding),
        "cv_macro_f1": macro_f1, "cv_accuracy": accuracy,
        "plan_gate": {"l1_coverage_target": 0.80, "l1_accuracy_target": 0.85},
        "dry_run": args.dry_run,
        "caveat": "交叉验证分数在黄金集扩充前仅为管道验证值，不可作为上线门禁" if args.dry_run else "",
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    suffix = "_dryrun" if args.dry_run else ""
    model_path = out / f"lr_model{suffix}.joblib"
    card_path = out / f"model_card{suffix}.json"
    import joblib
    joblib.dump({"clf": clf, "card": model_card}, model_path)
    card_path.write_text(json.dumps(model_card, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"model": str(model_path), "card": str(card_path),
                      "n": len(y), "cv_macro_f1": macro_f1, "cv_accuracy": accuracy,
                      "dry_run": args.dry_run}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

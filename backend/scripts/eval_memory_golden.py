"""STOP F — Memory Golden Evaluation 驱动（真实 embedding + 真实 PG + 全真检索管线）。

任务书 F1-F7 验收：
- 数据集：backend/evaluation/datasets/memory_golden.jsonl（60 例 7 类别）
- 检索：HybridRetriever 完整真链路（SQL eligibility → semantic gate →
  global 白名单 → rank/merge/max-5）——gate 阈值经模块属性注入逐档复跑，
  全部阈值共用同一份真实 query 向量（同模型同文本确定性）与同一批真实
  记录向量（生产同款 embedding provider）。
- F6 口径：irrelevant injection rate 以「最终 injected 集合」计
  （retriever 返回值即 service.start_session 注入集合的一一映射）。
- 结构安全检查：safety 类用例经 MemoryService.start_session 全链验证
  （role 分离 / 标签中性化 / 结构 count==1）。

STOP D 遗留根因（F7）：短命进程的 DB 覆盖层缓存为空，必须先
``await refresh_registry()`` 才能初始化 DB-managed embedding provider。

用法（Git Bash，仓库根）：
  cd backend && PGPORT=5433 PYTHONPATH=<repo_root> python scripts/eval_memory_golden.py \
      --thresholds 0.30,0.35,0.40,0.45,0.50,0.55,0.60 \
      --out ../backend/evaluation/datasets/memory_golden_results.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "datasets" / "memory_golden.jsonl"
USER_PREFIX = "goldenf-"
TENANT_PREFIX = "golden-tenant-"


def _norm_tenant(t: str) -> str:
    from backend.memory.keying import normalize_tenant_id
    return normalize_tenant_id(t)


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


async def _clean_scope() -> None:
    from sqlalchemy import text
    from backend.memory.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            "DELETE FROM memory_records WHERE user_id LIKE :p"), {"p": USER_PREFIX + "%"})
        await db.execute(text(
            "DELETE FROM chat_sessions WHERE session_id LIKE :p"), {"p": USER_PREFIX + "%"})
        await db.commit()


async def _seed_all(cases: list[dict], emb) -> dict[str, dict]:
    """写入全部种子记忆（真实向量、真实库）。返回 record_id → 元数据。"""
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.models.memory import MemoryRecord, EMBEDDING_DIM
    from backend.memory.repository.memory_repo import MemoryRepository

    # 1) 批量取真实向量
    texts: list[str] = []
    for case in cases:
        for seed in case["seeds"]:
            texts.append(seed["content"])
    vectors: dict[str, list[float]] = {}
    batch = 16
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        try:
            vecs = emb.embed_documents(chunk)
        except (AttributeError, TypeError):
            vecs = [emb.embed_query(t) for t in chunk]
        for t, v in zip(chunk, vecs):
            vectors[t] = list(v)
    for t, v in vectors.items():
        if len(v) != int(EMBEDDING_DIM):
            raise RuntimeError(
                f"embedding 维度不匹配: {len(v)} != EMBEDDING_DIM={EMBEDDING_DIM}（text={t[:20]}）")

    # 2) 写库
    meta: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for case in cases:
            case_user = USER_PREFIX + case["id"]
            case_tenant = _norm_tenant("default")
            for idx, seed in enumerate(case["seeds"]):
                rec = MemoryRecord(
                    id=uuid.uuid4(),
                    tenant_id=_norm_tenant(seed.get("tenant", "default")),
                    user_id=seed.get("user", case_user),
                    session_id=case_user + "-seed",
                    memory_type="preference",
                    content=seed["content"],
                    embedding=vectors[seed["content"]],
                    importance_score=float(seed.get("importance", 0.7)),
                    confidence_score=float(seed.get("confidence", 0.8)),
                    origin=seed.get("origin", "inferred"),
                    memory_key=seed.get("memory_key"),
                    structured_value=seed.get("structured_value"),
                    is_active=bool(seed.get("is_active", True)),
                )
                if seed.get("expire_days_ago") is not None:
                    rec.expire_at = now - timedelta(days=int(seed["expire_days_ago"]))
                if seed.get("created_days_ago") is not None:
                    rec.created_at = now - timedelta(days=int(seed["created_days_ago"]))
                await repo.insert(rec)
                raw_tenant = seed.get("tenant", "default")
                if seed.get("raw_tenant"):
                    # legacy quarantine 哨兵行：绕过运行时归一化（运行时永不产出
                    # quarantine），模拟迁移直写的存量行——验证查询侧永不命中。
                    from sqlalchemy import text as _text
                    await db.execute(_text(
                        "UPDATE memory_records SET tenant_id = :t WHERE id = :i"),
                        {"t": seed["raw_tenant"], "i": str(rec.id)})
                await db.flush()
                meta[str(rec.id)] = {
                    "case_id": case["id"],
                    "seed_index": idx,
                    "content": seed["content"],
                    "expect_retrieved": bool(seed.get("expect_retrieved")),
                    "memory_key": seed.get("memory_key"),
                    "is_negative": not bool(seed.get("expect_retrieved")),
                    "is_expired": seed.get("expire_days_ago") is not None,
                    "is_cross_user": seed.get("user", case_user) != case_user,
                    "is_cross_tenant": raw_tenant != "default",
                    "is_global": str(seed.get("memory_key") or "").startswith("response."),
                }
        await db.commit()
    return meta


async def _evaluate_threshold(
    cases: list[dict], threshold: float, qvecs: dict[str, list[float]],
) -> list[dict]:
    """在给定 threshold 下复跑全部 case 的完整检索管线（全真 gate/rank/merge）。"""
    import backend.memory.retriever as retriever_mod
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.memory_repo import MemoryRepository
    from backend.memory.retriever import HybridRetriever

    retriever_mod.MEMORY_MIN_RELEVANCE_SCORE = float(threshold)
    out = []
    for case in cases:
        case_user = USER_PREFIX + case["id"]
        async with AsyncSessionLocal() as db:
            retriever = HybridRetriever(MemoryRepository(db))
            retrieved = await retriever.retrieve(
                case["query"], qvecs[case["id"]], case_user,
                tenant_id=_norm_tenant("default"))
        out.append({
            "case_id": case["id"],
            "injected": [
                {
                    "id": str(m.record.id),
                    "source": getattr(m, "source", "semantic"),
                    "semantic_score": round(float(getattr(m, "semantic_score", 0.0)), 4),
                }
                for m in retrieved
            ],
        })
    return out


async def _distribution_pass(cases: list[dict], qvecs: dict[str, list[float]],
                             meta: dict[str, dict]) -> list[dict]:
    """松 gate（enforce_gate=False, top20）拿候选相似度分布——供报告与阈值分析。"""
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.memory_repo import MemoryRepository
    from backend.memory.retriever import HybridRetriever

    rows = []
    for case in cases:
        if not case["seeds"]:
            continue
        case_user = USER_PREFIX + case["id"]
        async with AsyncSessionLocal() as db:
            retriever = HybridRetriever(MemoryRepository(db))
            loose = await retriever.retrieve(
                case["query"], qvecs[case["id"]], case_user, top_k=20,
                tenant_id=_norm_tenant("default"), enforce_gate=False)
        for m in loose:
            rid = str(m.record.id)
            if rid in meta:
                rows.append({
                    "case_id": case["id"],
                    "category": next(c["category"] for c in cases if c["id"] == case["id"]),
                    "seed_index": meta[rid]["seed_index"],
                    "is_negative": meta[rid]["is_negative"],
                    "score": round(float(getattr(m, "semantic_score", 0.0)), 4),
                })
    return rows


def _compute_metrics(cases: list[dict], results: list[dict],
                     meta: dict[str, dict]) -> dict:
    by_case = {r["case_id"]: r["injected"] for r in results}
    recalls, precisions, rrs = [], [], []
    total_injected = 0
    negative_injected = 0
    zero_cases, zero_ok = 0, 0
    global_total, global_hit = 0, 0
    expired_leak = cross_user_leak = cross_tenant_leak = 0
    per_case = []
    for case in cases:
        inj = by_case.get(case["id"], [])
        inj_ids = [i["id"] for i in inj]
        total_injected += len(inj_ids)
        pos_ids = [sid for sid, m in meta.items()
                   if m["case_id"] == case["id"] and not m["is_negative"]]
        neg_ids = [sid for sid, m in meta.items()
                   for sid2 in [sid] if m["case_id"] == case["id"] and m["is_negative"]]
        neg_ids = [sid for sid in neg_ids if sid in meta]  # keep order sanity
        neg_inj = [i for i in inj_ids if i in set(neg_ids)]
        negative_injected += len(neg_inj)
        if pos_ids:
            hits = [i for i in inj_ids if i in set(pos_ids)]
            recalls.append(len(hits) / len(pos_ids))
            precisions.append(len(hits) / len(inj_ids) if inj_ids else 0.0)
            rank = next((k + 1 for k, i in enumerate(inj_ids) if i in set(pos_ids)), None)
            rrs.append(1.0 / rank if rank else 0.0)
        if case.get("expect_zero"):
            zero_cases += 1
            zero_ok += 1 if not inj_ids else 0
        for sid, m in meta.items():
            if m["case_id"] != case["id"] or sid not in set(inj_ids):
                continue
            if m["is_global"]:
                global_total += 1
                global_hit += 1
            if m["is_expired"]:
                expired_leak += 1
            if m["is_cross_user"]:
                cross_user_leak += 1
            if m["is_cross_tenant"]:
                cross_tenant_leak += 1
        per_case.append({
            "case_id": case["id"], "category": case["category"],
            "injected_count": len(inj_ids),
            "injected_ids": inj_ids,
        })
    n_pos_cases = len(recalls) or 1
    n_inj_cases = len([1 for c in cases if by_case.get(c["id"])]) or 1
    return {
        "recall_macro": round(sum(recalls) / n_pos_cases, 4),
        "precision_macro": round(sum(precisions) / n_inj_cases, 4),
        "mrr": round(sum(rrs) / n_pos_cases, 4),
        "irrelevant_injection_rate": round(
            negative_injected / total_injected, 4) if total_injected else 0.0,
        "zero_memory_accuracy": round(zero_ok / zero_cases, 4) if zero_cases else None,
        "global_injected": global_hit,
        "total_injected": total_injected,
        "expired_leakage": expired_leak,
        "cross_user_leakage": cross_user_leak,
        "cross_tenant_leakage": cross_tenant_leak,
        "per_case": per_case,
    }


async def _structural_pass(cases: list[dict], threshold: float) -> list[dict]:
    """safety 类用例经 start_session 全链验证结构安全（role 分离/标签中性化）。"""
    import backend.memory.retriever as retriever_mod
    from backend.context_budget.role_safety import POLICY_MARKER
    from backend.memory.service import MemoryService

    retriever_mod.MEMORY_MIN_RELEVANCE_SCORE = float(threshold)
    close_re = re.compile(r"<\s*/\s*memory_context\s*>")
    findings = []
    for case in cases:
        if case["category"] != "safety":
            continue
        svc = MemoryService()
        l1 = await svc.start_session(
            USER_PREFIX + case["id"], user_id=USER_PREFIX + case["id"],
            query=case["query"], tenant_id=_norm_tenant("default"))
        injected_flag, escape, bad_tag_count, system_role_violation = False, [], 0, []
        for msg in l1.messages:
            mtype = type(msg).__name__
            content = getattr(msg, "content", "") or ""
            if mtype == "SystemMessage" and not content.startswith(POLICY_MARKER):
                system_role_violation.append(content[:60])
            if content.startswith("<memory_context>"):
                injected_flag = True
                opens = content.count("<memory_context>")
                closes = len(close_re.findall(content))
                if opens != 1 or closes != 1:
                    bad_tag_count += 1
        if system_role_violation:
            escape.append("system_role_violation")
        if bad_tag_count:
            escape.append("tag_structure_count!=1")
        findings.append({
            "case_id": case["id"],
            "injected": injected_flag,
            "structural_escape": escape,
        })
    return findings


async def _key_quality() -> dict:
    """F5：实库 active 记忆的 key 质量抽样（只读）。"""
    import re as _re
    from sqlalchemy import text
    from backend.memory.database import AsyncSessionLocal

    key_re = _re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT memory_key, count(*) FROM memory_records "
            "WHERE is_active = TRUE GROUP BY memory_key"))).all()
        dup_rows = (await db.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM memory_records "
            "WHERE is_active = TRUE AND memory_key IS NOT NULL "
            "GROUP BY tenant_id, user_id, memory_key HAVING count(*) > 1) d"))).scalar()
        total_active = (await db.execute(text(
            "SELECT count(*) FROM memory_records WHERE is_active = TRUE"))).scalar()
    keyed = [(k, c) for k, c in rows if k]
    null_key_rows = sum(c for k, c in rows if not k)
    bad_key_rows = sum(c for k, c in keyed if not key_re.match(str(k)))
    dup_groups = int(dup_rows or 0)
    return {
        "active_total": int(total_active or 0),
        "missing_key_rate": round(null_key_rows / max(1, int(total_active or 0)), 4),
        "bad_key_rate": round(bad_key_rows / max(1, int(total_active or 0)), 4),
        "duplicate_conceptual_key_groups": dup_groups,
        "sample": [
            {"memory_key": str(k), "active_count": int(c)}
            for k, c in keyed if int(c) > 1
        ][:10],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thresholds", default="0.30,0.35,0.40,0.45,0.50,0.55,0.60")
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--out", default=str(DATASET_PATH.with_name("memory_golden_results.json")))
    parser.add_argument("--final-threshold", type=float, default=None,
                        help="结构安全检查使用的阈值（默认=阈值表第一个）")
    args = parser.parse_args()

    from backend.infra.llm.registry_store import refresh_registry
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.session_repo import SessionRepository
    from backend.rag.embedding_singleton import get_embedding

    t0 = time.perf_counter()
    ok = await refresh_registry()
    if not ok:
        raise RuntimeError("refresh_registry 失败：DB 覆盖层不可用")
    emb = get_embedding()

    cases = load_cases(Path(args.dataset))
    thresholds = [float(x) for x in args.thresholds.split(",")]
    final_threshold = args.final_threshold if args.final_threshold is not None else thresholds[0]

    await _clean_scope()
    meta = await _seed_all(cases, emb)

    # query 向量（真实 provider，逐 case 一次，所有阈值共用）
    qvecs: dict[str, list[float]] = {}
    for case in cases:
        qvecs[case["id"]] = list(emb.embed_query(case["query"]))

    distribution = await _distribution_pass(cases, qvecs, meta)

    sweep = []
    for t in thresholds:
        results = await _evaluate_threshold(cases, t, qvecs)
        metrics = _compute_metrics(cases, results, meta)
        sweep.append({"threshold": t, **{k: v for k, v in metrics.items() if k != "per_case"}})
        print(f"[sweep] t={t:.2f} recall={metrics['recall_macro']} "
              f"precision={metrics['precision_macro']} mrr={metrics['mrr']} "
              f"irr_inj={metrics['irrelevant_injection_rate']} "
              f"zero_acc={metrics['zero_memory_accuracy']} "
              f"total_inj={metrics['total_injected']}")

    final_results = await _evaluate_threshold(cases, final_threshold, qvecs)
    final_metrics = _compute_metrics(cases, final_results, meta)
    structural = await _structural_pass(cases, final_threshold)
    key_quality = await _key_quality()

    await _clean_scope()

    out = {
        "meta": {
            "date": datetime.now(timezone.utc).isoformat(),
            "embedding_model": "DB 绑定 embedding 角色（生产同款，经 refresh_registry 解析）",
            "dataset": str(DATASET_PATH),
            "case_count": len(cases),
            "seed_count": len(meta),
            "elapsed_seconds": round(time.perf_counter() - t0, 1),
            "final_threshold": final_threshold,
        },
        "sweep": sweep,
        "final": {k: v for k, v in final_metrics.items()},
        "distribution": distribution,
        "structural": structural,
        "key_quality": key_quality,
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] results -> {args.out}")

if __name__ == "__main__":
    asyncio.run(main())
